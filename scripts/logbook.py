"""nightwatch-logbook 쓰기 경로 공용 모듈 (schema v2 — run 단위).

dobee는 integration에 올라오는 pegging sha(FTL sha와 1:1) 단위로 전 구성을
테스트한다. 원본 파일 단위 = 쓰기 이벤트 단위 원칙에 따라 **원본은
구성×run당 1개**이고, 날짜는 소비 층(대시보드·리뷰)의 축으로만 쓴다:

    results/{config}/runs/{date}-{run_id}-{sha7}.json   # 원본, 불변
    results/{config}/index.json                         # 날짜별 rollup (파생)

- diff는 run 시퀀스 기준: 직전 run에서 pass → 이번 run에서 fail = 신규.
  신규 fail의 후보 변경점은 (직전 run pegging .. 최초 fail run pegging]
  구간의 FTL 커밋들 — pegging=FTL 1:1이므로 git log로 정확히 열거된다
- 날짜별 대표는 그날 마지막 run. 리뷰의 "신규"는 since(최초 fail 일자)가
  당일인 항목이다 (run 중간에 유입돼 마지막 run까지 fail인 것 포함)
- md 템플릿은 대시보드 내장 렌더러의 문법 부분집합만 사용한다. LLM 자유
  텍스트는 rationale 슬롯으로만 유입된다
"""

import json
import re
from pathlib import Path

SCHEMA_VERSION = 2
CONF_KO = {"high": "높음", "medium": "추정", "unknown": "불명"}
CONF_ORDER = ["high", "medium", "unknown"]
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)-([0-9a-f]{7,40})\.json$")


# ---------------------------------------------------------------- 파일 IO

def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(root: Path) -> dict:
    return load_json(root / "results" / "configs.json")


def run_path(root: Path, cfg_id: str, iso: str, run_id: int, sha: str) -> Path:
    return root / "results" / cfg_id / "runs" / f"{iso}-{run_id}-{sha[:7]}.json"


def list_runs(root: Path, cfg_id: str) -> list[dict]:
    """구성의 run 목록 (시퀀스 순). [{date, run_id, sha, path}]"""
    runs_dir = root / "results" / cfg_id / "runs"
    if not runs_dir.is_dir():
        return []
    out = []
    for p in runs_dir.glob("*.json"):
        m = RUN_FILE_RE.match(p.name)
        if m:
            out.append({"date": m.group(1), "run_id": int(m.group(2)),
                        "sha": m.group(3), "path": p})
    return sorted(out, key=lambda r: (r["date"], r["run_id"]))


def runs_of_date(root: Path, cfg_id: str, iso: str) -> list[dict]:
    return [r for r in list_runs(root, cfg_id) if r["date"] == iso]


def load_last_run(root: Path, cfg_id: str, iso: str) -> dict | None:
    """날짜별 대표 = 그날 마지막 run."""
    runs = runs_of_date(root, cfg_id, iso)
    return load_json(runs[-1]["path"]) if runs else None


def all_dates(root: Path, manifest: dict) -> list[str]:
    s: set[str] = set()
    for cfg in manifest["configs"]:
        s.update(r["date"] for r in list_runs(root, cfg["id"]))
    return sorted(s)


def prev_date_of(root: Path, manifest: dict, iso: str) -> str | None:
    prevs = [d for d in all_dates(root, manifest) if d < iso]
    return prevs[-1] if prevs else None


def op_on(meta: dict, iso: str) -> bool:
    """매니페스트 메타 기준으로 해당 일자에 운영 중인가 (since <= d < retired)."""
    if meta.get("since") and iso < meta["since"]:
        return False
    if meta.get("retired") and iso >= meta["retired"]:
        return False
    return True


def ftl_range(root: Path, cfg_id: str, since_sha: str) -> str | None:
    """최초 fail run의 유입 구간 '(직전 pegging..since_sha]'를 문자열로.

    첫 run이라 직전이 없으면 None (구간 미상).
    """
    runs = list_runs(root, cfg_id)
    for i, r in enumerate(runs):
        if r["sha"] == since_sha[:7]:
            return f"{runs[i - 1]['sha']}..{since_sha[:7]}" if i > 0 else None
    return None


# ---------------------------------------------------------------- run 생성

def build_run(cfg_id: str, iso: str, run_id: int, pegging_sha: str, total: int,
              facts_failures: dict, prev_run: dict | None) -> dict:
    """dobee 사실(facts)과 직전 run에서 run JSON을 만든다.

    - status: 직전 run에 없던 TC = new, 있던 TC = ongoing (run 시퀀스 기준)
    - since/since_sha: 최초 fail run의 날짜/pegging — ongoing이면 승계
    - 추정 필드(suspect_sha/confidence/rationale)도 승계. 신규 TC는 null로
      시작하며 apply_entries()가 채운다. rationale 키는 값이 있을 때만 존재
    """
    prev_failures = (prev_run or {}).get("failures", {})
    failures = {}
    for tc, fact in facts_failures.items():
        prev = prev_failures.get(tc)
        entry = {
            "status": "ongoing" if prev else "new",
            "since": prev["since"] if prev else iso,
            "since_sha": prev["since_sha"] if prev else pegging_sha[:7],
            "log_excerpt": fact["log_excerpt"],
            "log_url": fact["log_url"],
            "suspect_sha": prev["suspect_sha"] if prev else None,
            "confidence": prev["confidence"] if prev else None,
        }
        if prev and prev.get("rationale"):
            entry["rationale"] = prev["rationale"]
        failures[tc] = entry

    return {
        "schema_version": SCHEMA_VERSION,
        "config": cfg_id,
        "date": iso,
        "run_id": run_id,
        "pegging_sha": pegging_sha[:7],
        "summary": {
            "total": total,
            "pass": total - len(failures),
            "fail": len(failures),
            "new_fail": sum(1 for e in failures.values() if e["status"] == "new"),
        },
        "failures": failures,
    }


def validate_mapping_entry(entry: dict, last_run: dict, iso: str) -> str | None:
    """매핑 항목 검증. 문제 없으면 None, 있으면 사유 문자열."""
    tc = entry.get("tc")
    if not tc or tc not in last_run["failures"]:
        return f"{tc}: 해당 일자 마지막 run의 failures에 없는 TC"
    if last_run["failures"][tc]["since"] != iso:
        return (f"{tc}: 당일 신규 fail이 아님 "
                f"(since={last_run['failures'][tc]['since']})")
    sha = entry.get("suspect_sha", "")
    if not SHA_RE.match(sha or ""):
        return f"{tc}: suspect_sha 형식 오류 ({sha!r})"
    if entry.get("confidence") not in CONF_ORDER:
        return f"{tc}: confidence는 {CONF_ORDER} 중 하나여야 함"
    return None


def apply_entries(run: dict, entries: list[dict]) -> None:
    """검증된 매핑 항목을 run dict에 기입한다 (커밋 전 단계에서만 호출)."""
    for e in entries:
        if e["tc"] not in run["failures"]:
            continue
        f = run["failures"][e["tc"]]
        f["suspect_sha"] = e["suspect_sha"]
        f["confidence"] = e["confidence"]
        if e.get("rationale"):
            f["rationale"] = e["rationale"]


# ---------------------------------------------------------------- rollup

def write_rollup(root: Path, meta: dict, updated: str) -> dict:
    """run 파일들에서 날짜별 rollup(index.json)을 재생성한다.

    날짜별 대표는 그날 마지막 run. new_fail은 since==당일 항목 수
    (run 중간 유입 포함). runs[]에 그날의 run 체인을 기록한다.
    """
    days = []
    by_date: dict[str, list[dict]] = {}
    for r in list_runs(root, meta["id"]):
        by_date.setdefault(r["date"], []).append(r)
    for iso in sorted(by_date):
        runs = by_date[iso]
        last = load_json(runs[-1]["path"])
        s = last["summary"]
        days.append({
            "date": iso,
            "total": s["total"],
            "pass": s["pass"],
            "fail": s["fail"],
            "new_fail": sum(1 for e in last["failures"].values() if e["since"] == iso),
            "runs": [{"run_id": r["run_id"], "pegging_sha": r["sha"],
                      "fail": load_json(r["path"])["summary"]["fail"]} for r in runs],
            "file": str(runs[-1]["path"].relative_to(root / "results")),
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "config": meta["id"],
        "label": meta["label"],
        "updated": updated,
        "days": days,
    }
    write_json(root / "results" / meta["id"] / "index.json", payload)
    return payload


# ---------------------------------------------------------------- 일간 보고

def render_daily_md(root: Path, manifest: dict, iso: str) -> str:
    """reviews/daily/{date}.md — 날짜별 대표 run(전일 포함)에서 렌더링한다."""
    prev = prev_date_of(root, manifest, iso)

    total_fail = prev_fail = 0
    new_entries: list[tuple[str, str, dict]] = []       # (cfg, tc, entry)
    fixed_entries: list[tuple[str, str, dict]] = []     # (cfg, tc, prev entry)
    ongoing: list[tuple[str, str, dict]] = []
    per_cfg_new: list[tuple[str, int]] = []
    per_cfg_delta: list[tuple[str, int]] = []
    green: list[str] = []
    day_runs: list[dict] | None = None                  # pegging 전 구성 공통 — 첫 운영 구성 기준

    for meta in manifest["configs"]:
        cid = meta["id"]
        today = load_last_run(root, cid, iso)
        if today is None:  # 미운영
            continue
        if day_runs is None:
            day_runs = [{"run_id": r["run_id"], "sha": r["sha"]}
                        for r in runs_of_date(root, cid, iso)]
        s = today["summary"]
        total_fail += s["fail"]
        prevd = load_last_run(root, cid, prev) if prev else None
        if prevd:  # 양일 모두 운영된 구성만 like-for-like 비교
            prev_fail += prevd["summary"]["fail"]
            if s["fail"] != prevd["summary"]["fail"]:
                per_cfg_delta.append((cid, s["fail"] - prevd["summary"]["fail"]))
        news = [(tc, e) for tc, e in sorted(today["failures"].items())
                if e["since"] == iso]
        if news:
            per_cfg_new.append((cid, len(news)))
        new_entries += [(cid, tc, e) for tc, e in news]
        if prevd:
            fixed_entries += [(cid, tc, prevd["failures"][tc])
                              for tc in sorted(prevd["failures"])
                              if tc not in today["failures"]]
        ongoing += [(cid, tc, e) for tc, e in sorted(today["failures"].items())
                    if e["since"] < iso]
        if s["fail"] == 0:
            green.append(cid)

    L: list[str] = []
    L.append(f"# Daily Regression Review — {iso}")
    L.append("")
    L.append("> agent 생성 초안 — 당번 검수 후 분석 의뢰를 발송한다. "
             "회사 AI 정책에 따라 개발자 아이디는 기록하지 않으며, "
             "의뢰 발송 단계에서 suspect sha로 내부 조회한다.")
    L.append("")
    L.append("## 요약")
    L.append("")
    delta = ""
    if prev:
        diff = total_fail - prev_fail
        delta = f" (전일 대비 {'+' if diff > 0 else ''}{diff})" if diff != 0 else " (전일과 동일)"
    L.append(f"- 전체 fail **{total_fail}건**{delta} · 신규 **{len(new_entries)}건** · "
             f"해소 **{len(fixed_entries)}건**")
    if day_runs:
        chain = " → ".join(f"`{r['sha']}`" for r in day_runs)
        L.append(f"- 오늘 pegging {len(day_runs)}건: {chain}")
    if prev:
        if per_cfg_delta:
            per_cfg_delta.sort(key=lambda t: (-t[1], t[0]))
            L.append("- 전일 대비 증감: " + " · ".join(
                f"**{cid}** {'+' if n > 0 else ''}{n}" for cid, n in per_cfg_delta)
                + " (그 외 변동 없음)")
        else:
            L.append("- 전일 대비 증감: 전 구성 변동 없음")
    if per_cfg_new:
        L.append("- 구성별 신규: " + " · ".join(f"**{cid}** {n}건" for cid, n in per_cfg_new))
    if green:
        L.append(f"- fail 없는 구성: {', '.join(green)}")
    lifecycle = [f"**{m['id']}** 운영 시작" for m in manifest["configs"]
                 if m.get("since") == iso]
    lifecycle += [f"**{m['id']}** 운영 중단" for m in manifest["configs"]
                  if m.get("retired") == iso]
    if lifecycle:
        L.append("- 구성 변경: " + " · ".join(lifecycle)
                 + " (전일 대비 수치는 양일 모두 운영된 구성 기준)")
    L.append("")

    L.append("## 신규 fail — 변경점별 분석")
    L.append("")
    if not new_entries:
        L.append("신규 fail 없음.")
        L.append("")
    else:
        groups: dict[str | None, list[tuple[str, str, dict]]] = {}
        for cid, tc, e in new_entries:
            groups.setdefault(e["suspect_sha"], []).append((cid, tc, e))
        shas = [s for s in groups if s is not None] + ([None] if None in groups else [])
        for sha in shas:
            entries = groups[sha]
            L.append(f"### {sha if sha else '원인 미상'}")
            L.append("")
            for cid, tc, e in entries:
                conf = (f" (확신도 {CONF_KO.get(e['confidence'], '불명')})"
                        if e["confidence"] else "")
                L.append(f"- **{cid}** `{tc}`{conf}: {e['log_excerpt']}")
                rng = ftl_range(root, cid, e["since_sha"])
                L.append(f"  - 유입: pegging `{e['since_sha']}`"
                         + (f" · 후보 구간 `{rng}`" if rng else ""))
                if e.get("rationale"):
                    L.append(f"  - 근거: {e['rationale']}")
            L.append("")
            if sha is None:
                L.append("변경점 매핑에 실패했다. 로그 기반 수동 분석이 필요하다.")
            elif len(entries) > 1:
                cfgs = ", ".join(sorted({cid for cid, _, _ in entries}))
                L.append(f"동일 변경점 `{sha}`가 {len(entries)}건의 신규 fail({cfgs})에 "
                         f"걸려 있다. 교차 구성 회귀 가능성이 높아 우선 분석 대상이다.")
            else:
                L.append(f"`{sha}` 변경점에 대한 분석 의뢰 대상이다.")
            L.append("")

    if fixed_entries:
        L.append("## 해소")
        L.append("")
        for cid, tc, e in fixed_entries:
            L.append(f"- **{cid}** `{tc}` — {e['since']}부터 지속되던 fail 해소")
        L.append("")

    L.append("## 지속 관찰")
    L.append("")
    if ongoing:
        oldest = sorted(ongoing, key=lambda t: t[2]["since"])[:5]
        L.append(f"지속 fail {len(ongoing)}건. 최장기 5건:")
        L.append("")
        for cid, tc, e in oldest:
            L.append(f"- **{cid}** `{tc}` — since {e['since']}")
    else:
        L.append("지속 fail 없음.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 월간 리뷰

def _mmdd(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}"


def render_monthly_md(root: Path, manifest: dict, ym: str) -> str:
    """reviews/monthly/{YYYY-MM}.md — 해당 월의 날짜별 대표 run에서 렌더링한다."""
    month_dates = [d for d in all_dates(root, manifest) if d.startswith(ym)]
    if not month_dates:
        raise ValueError(f"{ym}에 해당하는 run 파일이 없음")
    first, last = month_dates[0], month_dates[-1]

    rows = []            # (표시 이름, 월초 fail, 월말 fail, 신규, 해소)
    total_new = total_fixed = 0
    new_by_sha: dict[str, list[tuple[str, str, str, dict]]] = {}  # sha -> (cfg, date, tc, e)
    conf_count: dict[str, int] = {}
    unmapped: list[tuple[str, str, str]] = []          # (cfg, tc, date)
    chronic_all: list[tuple[str, str, str]] = []       # (cfg, tc, since)
    run_total = 0

    for meta in manifest["configs"]:
        cid = meta["id"]
        ods = sorted({r["date"] for r in list_runs(root, cid) if r["date"].startswith(ym)})
        if not ods:
            continue
        c_first, c_last = ods[0], ods[-1]
        name = cid
        if meta.get("since") == c_first and c_first != first:
            name += f" ({_mmdd(c_first)}~)"
        if meta.get("retired") and c_last != last:
            name += f" (~{_mmdd(c_last)})"

        dailies = {d: load_last_run(root, cid, d) for d in ods}
        start_fail = dailies[c_first]["summary"]["fail"]
        end_fail = dailies[c_last]["summary"]["fail"]

        news = 0
        for d in ods:
            for tc, e in sorted(dailies[d]["failures"].items()):
                if e["since"] != d:
                    continue
                news += 1
                if e["suspect_sha"]:
                    new_by_sha.setdefault(e["suspect_sha"], []).append((cid, d, tc, e))
                    conf = e["confidence"] or "unknown"
                    conf_count[conf] = conf_count.get(conf, 0) + 1
                else:
                    unmapped.append((cid, tc, d))
        fixed = 0
        for d1, d2 in zip(ods, ods[1:]):
            fixed += sum(1 for tc in dailies[d1]["failures"]
                         if tc not in dailies[d2]["failures"])
        rows.append((name, start_fail, end_fail, news, fixed))
        total_new += news
        total_fixed += fixed
        chronic_all += [(cid, tc, e["since"])
                        for tc, e in sorted(dailies[c_first]["failures"].items())
                        if tc in dailies[c_last]["failures"] and e["since"] < c_first]

    # pegging run 수는 전 구성 공통 — 날짜별 첫 운영 구성 기준으로 집계
    for d in month_dates:
        for meta in manifest["configs"]:
            runs = runs_of_date(root, meta["id"], d)
            if runs:
                run_total += len(runs)
                break

    day_totals = []
    for d in month_dates:
        t = 0
        for meta in manifest["configs"]:
            daily = load_last_run(root, meta["id"], d)
            if daily:
                t += daily["summary"]["fail"]
        day_totals.append(t)
    mapped = total_new - len(unmapped)
    cover = round(mapped / total_new * 100) if total_new else 0

    L: list[str] = []
    L.append(f"# Monthly Regression Review — {ym}")
    L.append("")
    L.append(f"> agent 생성 초안 — 월간 회고/성과 보고용. 데이터 범위: "
             f"{first} ~ {last} ({len(month_dates)}일, pegging {run_total}건). "
             "회사 AI 정책에 따라 개발자 아이디는 기록하지 않는다.")
    L.append("")
    L.append("## 월간 요약")
    L.append("")
    L.append(f"- 전체 fail: 월초 **{day_totals[0]}건** → 월말 **{day_totals[-1]}건** "
             f"(최저 {min(day_totals)} · 최고 {max(day_totals)})")
    L.append(f"- 신규 **{total_new}건** · 해소 **{total_fixed}건**")
    conf_str = " · ".join(f"{CONF_KO.get(k, k)} {v}" for k, v in
                          sorted(conf_count.items(), key=lambda kv: CONF_ORDER.index(kv[0])))
    L.append(f"- 변경점 매핑: 신규 {total_new}건 중 **{mapped}건 매핑 ({cover}%)**"
             + (f" — 확신도 {conf_str}" if conf_str else ""))
    L.append("")

    L.append("## 구성별 현황")
    L.append("")
    L.append("| 구성 | 월초 fail | 월말 fail | 신규 | 해소 |")
    L.append("|---|---|---|---|---|")
    for name, s, e, n, f in rows:
        L.append(f"| {name} | {s} | {e} | {n if n else '·'} | {f if f else '·'} |")
    L.append("")

    L.append("## 변경점별 신규 fail 이력")
    L.append("")
    if new_by_sha:
        for sha, entries in sorted(new_by_sha.items(),
                                   key=lambda kv: (min(e[1] for e in kv[1]), kv[0])):
            cfgs = sorted({cid for cid, _, _, _ in entries})
            dates_s = sorted({_mmdd(d) for _, d, _, _ in entries})
            cross = " — **교차 구성 회귀**" if len(cfgs) > 1 else ""
            L.append(f"- `{sha}` ({', '.join(dates_s)}) · {len(entries)}건 · "
                     f"{', '.join(cfgs)}{cross}")
            for cid, _, tc, e in entries:
                L.append(f"  - {cid} `{tc}` (확신도 {CONF_KO.get(e['confidence'], '불명')}, "
                         f"유입 pegging `{e['since_sha']}`)")
    else:
        L.append("매핑된 신규 fail 없음.")
    L.append("")
    if unmapped:
        L.append(f"원인 미상 신규 {len(unmapped)}건 — 매핑 실패 사례로 회고 대상:")
        L.append("")
        for cid, tc, d in unmapped:
            L.append(f"- {cid} `{tc}` ({_mmdd(d)})")
        L.append("")

    L.append("## 만성 fail (월 전체 지속)")
    L.append("")
    L.append(f"월초부터 월말까지 계속 fail인 TC **{len(chronic_all)}건**. 최장기 5건:")
    L.append("")
    for cid, tc, since in sorted(chronic_all, key=lambda t: t[2])[:5]:
        L.append(f"- **{cid}** `{tc}` — since {since}")
    L.append("")
    return "\n".join(L)
