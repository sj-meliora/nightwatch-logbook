#!/usr/bin/env python3
"""열흘치 daily regression log 예시 데이터를 생성한다 (구성 11개 + 데일리 리포트).

실제 운영에서는 스케줄 워크플로우가 dobee 결과를 파싱해 데일리 파일을
커밋하지만, 이 스크립트는 대시보드/스키마 검증용 예시 데이터를 만든다.

원칙 (설계 문서와 동일):
- 데일리 파일은 생성 후 불변, JSON이 source of truth
- failures는 배열이 아닌 TC 이름 키 객체, 키 정렬 + pretty print
  → git diff가 TC 추가/제거를 사람이 읽을 수 있게 보여준다
- suspect_sha/confidence는 agent 추정 결과로, dobee 사실(로그)과 구분
- 회사 AI 정책: 개발자 아이디는 repo 데이터에 기록하지 않는다.
  suspect는 sha로만 기록하고, 분석 의뢰 발송 단계에서 내부 시스템으로
  sha → 개발자를 조회한다
- index.json은 데일리 파일에서 파생되는 rollup으로, 매일 재생성
- reviews/daily/{date}.md는 agent가 매일 생성하는 당번 검수용 리뷰 초안
- reviews/monthly/{YYYY-MM}.md는 월간 회고/성과 보고용 리뷰 (매핑 커버리지 포함)

사용법: python3 scripts/generate_sample_data.py  (repo 루트에서 실행)
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
REVIEWS_DIR = REPO_ROOT / "reviews"
SCHEMA_VERSION = 1

DATES = [date(2026, 7, 14) + timedelta(days=i) for i in range(10)]

CONF_KO = {"high": "높음", "medium": "추정", "unknown": "불명"}


@dataclass
class Episode:
    """한 TC의 연속 fail 구간. until은 다시 pass가 된 날(비활성 시작일), None이면 계속 fail."""
    tc: str
    since: date
    until: date | None
    log_excerpt: str
    # agent 추정 필드 (신규 fail 분석 시점에 채워지고 이후 날짜로 승계)
    suspect_sha: str | None = None
    confidence: str | None = None

    def active_on(self, d: date) -> bool:
        return self.since <= d and (self.until is None or d < self.until)


def d_(s: str) -> date:
    return date.fromisoformat(s)


def chronic(entries: list[tuple[str, str, str]]) -> list[Episode]:
    """윈도우 시작 전부터 계속 fail 중인 만성 케이스들."""
    return [Episode(tc, d_(s), None, log) for tc, s, log in entries]


# ---------------------------------------------------------------- cfg-a
# FTL full regression: TC 900개, 만성 fail이 20건 이상 깔려 있는 대형 구성.
CFG_A = chronic([
    ("TC_FTL_GC_003",       "2026-06-30", "ASSERT at gc_policy.c:88: victim_score < 0"),
    ("TC_FTL_GC_009",       "2026-07-04", "ASSERT at gc_policy.c:132: free_blk < reserve_min"),
    ("TC_FTL_WEAR_101",     "2026-07-02", "TIMEOUT after 1800s in wear_level_stress"),
    ("TC_FTL_WEAR_115",     "2026-06-27", "wear delta 412 > threshold 256 after 10k cycles"),
    ("TC_FTL_MAP_133",      "2026-07-09", "map entry crc mismatch, lpn=0x8821a"),
    ("TC_FTL_TRIM_060",     "2026-07-07", "trimmed range readback non-zero at lba=0x1f000"),
    ("TC_IO_RAND_034",      "2026-07-05", "MISCOMPARE lba=0x3f2a10 expected=0xAA got=0x00"),
    ("TC_IO_RAND_047",      "2026-07-11", "MISCOMPARE lba=0x11b204 expected=0x5A got=0xFF"),
    ("TC_IO_LAT_083",       "2026-07-03", "p99 latency 812ms > budget 300ms under mixed load"),
    ("TC_IO_QD1_101",       "2026-06-25", "IOPS 8.2k < floor 12k at qd1 rand read"),
    ("TC_META_SNAP_007",    "2026-06-21", "PANIC: snapshot replay mismatch, gen=42"),
    ("TC_META_JOURNAL_002", "2026-07-06", "journal replay stopped at seq=99182, tail corrupt"),
    ("TC_META_FMT_027",     "2026-07-10", "format with meta_v3 leaves stale sb copy"),
    ("TC_PWR_LOSS_112",     "2026-07-08", "recovery loop exceeded 3 retries after power cut"),
    ("TC_PWR_LOSS_127",     "2026-07-12", "unflushed window 48ms > spec 20ms on plp path"),
    ("TC_STRESS_LOOP_031",  "2026-06-29", "TIMEOUT after 3600s in mixed stress iteration 88"),
    ("TC_STRESS_LOOP_042",  "2026-07-01", "TIMEOUT after 3600s in mixed stress iteration 141"),
    ("TC_STRESS_THERM_055", "2026-07-05", "throttle oscillation, temp 87C > limit 85C"),
    ("TC_NVME_AER_071",     "2026-07-09", "AER completion missing after ns attach event"),
    ("TC_NVME_FMT_083",     "2026-07-11", "format nvm timeout, csts.rdy stuck 1"),
    ("TC_SEC_ERASE_014",    "2026-07-03", "crypto erase leaves readable region at lba=0x0"),
    ("TC_DBG_TRACE_090",    "2026-07-12", "trace buffer overrun drops 1.2% events under load"),
]) + [
    Episode("TC_PWR_LOSS_118", d_("2026-07-10"), d_("2026-07-18"),
            "spor recovery hang at stage 2, ftl_open() no return"),
    Episode("TC_FTL_MAP_141", d_("2026-07-15"), d_("2026-07-20"),
            "map rebuild reads stale l2p page after unclean shutdown",
            suspect_sha="9e12ab4", confidence="medium"),
    Episode("TC_IO_QD32_055", d_("2026-07-16"), None,
            "hang: qd32 rand write stalls at 97% completion",
            suspect_sha="4c77f02", confidence="unknown"),
    Episode("TC_META_JOURNAL_019", d_("2026-07-18"), None,
            "journal checkpoint skipped when gc pressure high"),
    Episode("TC_FTL_TRIM_072", d_("2026-07-19"), d_("2026-07-22"),
            "deallocate during gc migrates trimmed block, data resurrect"),
    Episode("TC_IO_MIXED_090", d_("2026-07-20"), None,
            "MISCOMPARE under 70/30 mixed after 2h, lba=0x29ff10",
            suspect_sha="b8d4310", confidence="medium"),
    # 2026-07-23 신규 3건 — 설계 문서 예시 시나리오
    Episode("TC_FTL_GC_017", d_("2026-07-23"), None,
            "ASSERT at gc_victim.c:412: invalid victim block state during gc",
            suspect_sha="a3f9c21", confidence="high"),
    Episode("TC_FTL_MAP_205", d_("2026-07-23"), None,
            "SEGV in map_cache_evict(), map_cache.c:207",
            suspect_sha="77d0e4f", confidence="high"),
    Episode("TC_IO_SEQ_078", d_("2026-07-23"), None,
            "IO latency spike 4200ms > budget 500ms in seq write",
            suspect_sha="c91b502", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-b
CFG_B = chronic([
    ("TC_NVME_FMT_009",    "2026-07-01", "format nvm with ses=1 returns invalid field"),
    ("TC_IO_LAT_QOS_021",  "2026-06-28", "qos class B p95 4.1ms > sla 2.0ms"),
    ("TC_META_GC_MIX_044", "2026-07-06", "gc during meta update drops one dirty page"),
    ("TC_WEAR_STATIC_130", "2026-07-11", "static wear leveling never triggers in 24h soak"),
    ("TC_PWR_CYCLE_202",   "2026-06-15", "device not ready within 5s after 500th cycle"),
]) + [
    Episode("TC_IO_FLUSH_067", d_("2026-07-15"), d_("2026-07-17"),
            "flush returns before nand program complete (write-through off)"),
    Episode("TC_NVME_RESET_033", d_("2026-07-17"), None,
            "controller reset during io leaves qpair zombie",
            suspect_sha="f0a9912", confidence="medium"),
    Episode("TC_MAP_REBUILD_058", d_("2026-07-21"), d_("2026-07-23"),
            "rebuild time 41s > budget 30s on 75% full drive"),
    Episode("TC_IO_WRCACHE_012", d_("2026-07-22"), None,
            "write cache disable ignored under sustained seq write",
            suspect_sha="5b21d9e", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-c
CFG_C = [
    Episode("TC_SMOKE_BOOT_001", d_("2026-07-16"), d_("2026-07-18"),
            "boot-to-ready 6.2s > smoke budget 5s"),
    Episode("TC_IO_BASIC_005", d_("2026-07-21"), d_("2026-07-22"),
            "single seq write/read verify miscompare at lba=0x100"),
]

# ---------------------------------------------------------------- cfg-d
CFG_D = chronic([
    ("TC_GCS_HOT_COLD_012", "2026-07-03", "gc write amp 4.8 > limit 3.5 under hot/cold mix"),
    ("TC_GCS_URGENT_027",   "2026-06-26", "urgent gc misses deadline, host write stall 1.2s"),
    ("TC_GCS_RESERVE_031",  "2026-07-10", "reserve blocks below min watermark after loop 77"),
]) + [
    # cfg-a의 TC_FTL_GC_017과 같은 변경점(a3f9c21)이 의심되는 교차 구성 신호
    Episode("TC_GCS_VICTIM_044", d_("2026-07-23"), None,
            "victim pick loops on same block, gc starvation",
            suspect_sha="a3f9c21", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-e
CFG_E = chronic([
    ("TC_PLR_SPOR_009", "2026-06-30", "spor iteration 412 mount fail, dirty map"),
    ("TC_PLR_CAP_021",  "2026-07-08", "cap discharge curve below spec at 85C"),
]) + [
    Episode("TC_PLR_NESTED_033", d_("2026-07-12"), d_("2026-07-20"),
            "nested power cut during replay corrupts journal tail"),
]

# ---------------------------------------------------------------- cfg-f
CFG_F = chronic([
    ("TC_PERF_SEQW_005",    "2026-07-06", "seq write 2.1GB/s < target 2.4GB/s"),
    ("TC_PERF_RNDR_018",    "2026-06-24", "rand read iops 388k < target 420k"),
    ("TC_PERF_LAT_QOS_030", "2026-07-09", "p99.99 latency 9.8ms > sla 8ms"),
    ("TC_PERF_SUSTAIN_041", "2026-07-01", "sustained write drops 38% after 30min, thermal"),
]) + [
    Episode("TC_PERF_MIXED_052", d_("2026-07-17"), d_("2026-07-19"),
            "mixed rw throughput dip after fw slot swap"),
]

# ---------------------------------------------------------------- cfg-g
CFG_G = chronic([
    ("TC_MJ_REPLAY_014", "2026-07-05", "replay time 8.4s > budget 5s on full journal"),
]) + [
    Episode("TC_MJ_CKPT_022", d_("2026-07-23"), None,
            "checkpoint skip when gc pressure high, journal wrap",
            suspect_sha="77d0e4f", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-h (전 기간 green)
CFG_H: list[Episode] = []

# ---------------------------------------------------------------- cfg-i
CFG_I = chronic([
    ("TC_CMP_HOSTA_101", "2026-06-28", "host A hotplug: link retrain loop after s3 resume"),
    ("TC_CMP_HOSTB_115", "2026-07-02", "host B nvme timeout on admin q during boot storm"),
    ("TC_CMP_OSX_133",   "2026-07-07", "os X trim burst causes cmd timeout at qd64"),
    ("TC_CMP_BIOS_140",  "2026-06-22", "legacy bios: option rom hang on cold boot 1/50"),
    ("TC_CMP_VMD_152",   "2026-07-09", "vmd passthrough: msix vector loss after reset"),
]) + [
    Episode("TC_CMP_HOSTC_128", d_("2026-07-11"), d_("2026-07-21"),
            "host C aspm l1.2 entry storm drops io"),
]

# ---------------------------------------------------------------- cfg-j
CFG_J = chronic([
    ("TC_WLS_STATIC_007", "2026-07-04", "static wl not triggered in 24h soak"),
    ("TC_WLS_DELTA_019",  "2026-06-29", "erase count delta 512 > limit 384"),
]) + [
    Episode("TC_WLS_MIGRATE_028", d_("2026-07-19"), None,
            "wl migration collides with gc, double relocation"),
]

# ---------------------------------------------------------------- cfg-k
CFG_K = [
    Episode("TC_THM_OSC_011", d_("2026-07-22"), None,
            "throttle oscillation 2Hz between P1/P3",
            suspect_sha="f31c807", confidence="unknown"),
    Episode("TC_THM_SENSOR_024", d_("2026-07-22"), None,
            "composite temp sensor stuck during thermal ramp"),
]

CONFIGS = [
    {"id": "cfg-a", "label": "FTL full regression",  "total": 900, "episodes": CFG_A},
    {"id": "cfg-b", "label": "NVMe protocol suite",  "total": 420, "episodes": CFG_B},
    {"id": "cfg-c", "label": "smoke suite",          "total": 150, "episodes": CFG_C},
    {"id": "cfg-d", "label": "FTL GC stress",        "total": 300, "episodes": CFG_D},
    {"id": "cfg-e", "label": "power loss recovery",  "total": 250, "episodes": CFG_E},
    {"id": "cfg-f", "label": "IO performance",       "total": 500, "episodes": CFG_F},
    {"id": "cfg-g", "label": "metadata journal",     "total": 350, "episodes": CFG_G},
    {"id": "cfg-h", "label": "security & sanitize",  "total": 180, "episodes": CFG_H},
    {"id": "cfg-i", "label": "compat matrix",        "total": 600, "episodes": CFG_I},
    {"id": "cfg-j", "label": "wear leveling soak",   "total": 220, "episodes": CFG_J},
    {"id": "cfg-k", "label": "thermal & throttle",   "total": 280, "episodes": CFG_K},
]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def run_id_for(cfg_idx: int, d: date) -> int:
    # dobee run 번호 흉내: 구성/날짜별로 안정적인 가짜 번호
    return 3000 + cfg_idx * 700 + (d - DATES[0]).days * 11


# ---------------------------------------------------------------- 리포트
def build_report(d: date, prev: date | None) -> str:
    """reviews/daily/{date}.md — agent가 데일리 데이터에서 생성하는 사람용 리뷰 초안."""
    total_fail = prev_fail = 0
    new_entries: list[tuple[str, Episode]] = []
    fixed_entries: list[tuple[str, Episode]] = []
    ongoing: list[tuple[str, Episode]] = []
    per_cfg_new: list[tuple[str, int]] = []
    per_cfg_delta: list[tuple[str, int]] = []
    green: list[str] = []

    for cfg in CONFIGS:
        active = [ep for ep in cfg["episodes"] if ep.active_on(d)]
        total_fail += len(active)
        if prev:
            cfg_prev = sum(1 for ep in cfg["episodes"] if ep.active_on(prev))
            prev_fail += cfg_prev
            if len(active) != cfg_prev:
                per_cfg_delta.append((cfg["id"], len(active) - cfg_prev))
        news = [ep for ep in active if ep.since == d]
        if news:
            per_cfg_new.append((cfg["id"], len(news)))
        new_entries += [(cfg["id"], ep) for ep in news]
        fixed_entries += [(cfg["id"], ep) for ep in cfg["episodes"] if ep.until == d]
        ongoing += [(cfg["id"], ep) for ep in active if ep.since < d]
        if not active:
            green.append(cfg["id"])

    L: list[str] = []
    L.append(f"# Daily Regression Review — {d.isoformat()}")
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
    L.append("")

    L.append("## 신규 fail — 변경점별 분석")
    L.append("")
    if not new_entries:
        L.append("신규 fail 없음.")
        L.append("")
    else:
        groups: dict[str | None, list[tuple[str, Episode]]] = {}
        for cid, ep in new_entries:
            groups.setdefault(ep.suspect_sha, []).append((cid, ep))
        shas = [s for s in groups if s is not None] + ([None] if None in groups else [])
        for sha in shas:
            entries = groups[sha]
            L.append(f"### {sha if sha else '원인 미상'}")
            L.append("")
            for cid, ep in entries:
                conf = f" (확신도 {CONF_KO.get(ep.confidence, '불명')})" if ep.confidence else ""
                L.append(f"- **{cid}** `{ep.tc}`{conf}: {ep.log_excerpt}")
            L.append("")
            if sha is None:
                L.append("변경점 매핑에 실패했다. 로그 기반 수동 분석이 필요하다.")
            elif len(entries) > 1:
                cfgs = ", ".join(sorted({cid for cid, _ in entries}))
                L.append(f"동일 변경점 `{sha}`가 {len(entries)}건의 신규 fail({cfgs})에 "
                         f"걸려 있다. 교차 구성 회귀 가능성이 높아 우선 분석 대상이다.")
            else:
                L.append(f"`{sha}` 변경점에 대한 분석 의뢰 대상이다.")
            L.append("")

    if fixed_entries:
        L.append("## 해소")
        L.append("")
        for cid, ep in fixed_entries:
            L.append(f"- **{cid}** `{ep.tc}` — {ep.since.isoformat()}부터 지속되던 fail 해소")
        L.append("")

    L.append("## 지속 관찰")
    L.append("")
    if ongoing:
        oldest = sorted(ongoing, key=lambda t: t[1].since)[:5]
        L.append(f"지속 fail {len(ongoing)}건. 최장기 5건:")
        L.append("")
        for cid, ep in oldest:
            L.append(f"- **{cid}** `{ep.tc}` — since {ep.since.isoformat()}")
    else:
        L.append("지속 fail 없음.")
    L.append("")
    return "\n".join(L)


def build_monthly_report(month_dates: list[date]) -> str:
    """reviews/monthly/{YYYY-MM}.md — 월간 회고/성과 보고용 리뷰.

    데일리와 달리 의뢰 액션이 아닌 집계가 목적: 구성별 월초→월말 현황,
    변경점 매핑 커버리지(정확도 측정 근거), 교차 구성 회귀 이력, 만성 fail.
    """
    first, last = month_dates[0], month_dates[-1]
    ym = first.strftime("%Y-%m")

    rows = []            # (cfg_id, 월초 fail, 월말 fail, 신규, 해소)
    total_new = total_fixed = 0
    new_by_sha: dict[str, list[tuple[str, date, Episode]]] = {}
    conf_count: dict[str, int] = {}
    unmapped: list[tuple[str, Episode]] = []
    chronic_all: list[tuple[str, Episode]] = []

    for cfg in CONFIGS:
        eps = cfg["episodes"]
        start_fail = sum(1 for ep in eps if ep.active_on(first))
        end_fail = sum(1 for ep in eps if ep.active_on(last))
        news = [ep for ep in eps if first <= ep.since <= last]
        fixed = [ep for ep in eps if ep.until and first <= ep.until <= last]
        rows.append((cfg["id"], start_fail, end_fail, len(news), len(fixed)))
        total_new += len(news)
        total_fixed += len(fixed)
        for ep in news:
            if ep.suspect_sha:
                new_by_sha.setdefault(ep.suspect_sha, []).append((cfg["id"], ep.since, ep))
                conf_count[ep.confidence or "unknown"] = \
                    conf_count.get(ep.confidence or "unknown", 0) + 1
            else:
                unmapped.append((cfg["id"], ep))
        chronic_all += [(cfg["id"], ep) for ep in eps
                        if ep.active_on(first) and ep.active_on(last) and ep.since < first]

    day_totals = [sum(1 for cfg in CONFIGS for ep in cfg["episodes"] if ep.active_on(d))
                  for d in month_dates]
    mapped = total_new - len(unmapped)
    cover = round(mapped / total_new * 100) if total_new else 0

    L: list[str] = []
    L.append(f"# Monthly Regression Review — {ym}")
    L.append("")
    L.append(f"> agent 생성 초안 — 월간 회고/성과 보고용. 데이터 범위: "
             f"{first.isoformat()} ~ {last.isoformat()} ({len(month_dates)}일). "
             "회사 AI 정책에 따라 개발자 아이디는 기록하지 않는다.")
    L.append("")
    L.append("## 월간 요약")
    L.append("")
    L.append(f"- 전체 fail: 월초 **{day_totals[0]}건** → 월말 **{day_totals[-1]}건** "
             f"(최저 {min(day_totals)} · 최고 {max(day_totals)})")
    L.append(f"- 신규 **{total_new}건** · 해소 **{total_fixed}건**")
    conf_str = " · ".join(f"{CONF_KO.get(k, k)} {v}" for k, v in
                          sorted(conf_count.items(),
                                 key=lambda kv: ["high", "medium", "unknown"].index(kv[0])))
    L.append(f"- 변경점 매핑: 신규 {total_new}건 중 **{mapped}건 매핑 ({cover}%)**"
             + (f" — 확신도 {conf_str}" if conf_str else ""))
    L.append("")

    L.append("## 구성별 현황")
    L.append("")
    L.append("| 구성 | 월초 fail | 월말 fail | 신규 | 해소 |")
    L.append("|---|---|---|---|---|")
    for cid, s, e, n, f in rows:
        L.append(f"| {cid} | {s} | {e} | {n if n else '·'} | {f if f else '·'} |")
    L.append("")

    L.append("## 변경점별 신규 fail 이력")
    L.append("")
    if new_by_sha:
        for sha, entries in sorted(new_by_sha.items(),
                                   key=lambda kv: (min(e[1] for e in kv[1]), kv[0])):
            cfgs = sorted({cid for cid, _, _ in entries})
            dates_s = sorted({s.strftime("%m/%d") for _, s, _ in entries})
            cross = " — **교차 구성 회귀**" if len(cfgs) > 1 else ""
            L.append(f"- `{sha}` ({', '.join(dates_s)}) · {len(entries)}건 · "
                     f"{', '.join(cfgs)}{cross}")
            for cid, _, ep in entries:
                L.append(f"  - {cid} `{ep.tc}` (확신도 {CONF_KO.get(ep.confidence, '불명')})")
    else:
        L.append("매핑된 신규 fail 없음.")
    L.append("")
    if unmapped:
        L.append(f"원인 미상 신규 {len(unmapped)}건 — 매핑 실패 사례로 회고 대상:")
        L.append("")
        for cid, ep in unmapped:
            L.append(f"- {cid} `{ep.tc}` ({ep.since.strftime('%m/%d')})")
        L.append("")

    L.append("## 만성 fail (월 전체 지속)")
    L.append("")
    L.append(f"월초부터 월말까지 계속 fail인 TC **{len(chronic_all)}건**. 최장기 5건:")
    L.append("")
    for cid, ep in sorted(chronic_all, key=lambda t: t[1].since)[:5]:
        L.append(f"- **{cid}** `{ep.tc}` — since {ep.since.isoformat()}")
    L.append("")
    return "\n".join(L)


def main() -> None:
    manifest = {"schema_version": SCHEMA_VERSION, "configs": []}

    for ci, cfg in enumerate(CONFIGS):
        cfg_dir = RESULTS_DIR / cfg["id"]
        rollup_days = []

        for d in DATES:
            active = [ep for ep in cfg["episodes"] if ep.active_on(d)]
            failures = {}
            for ep in active:
                failures[ep.tc] = {
                    "status": "new" if ep.since == d else "ongoing",
                    "since": ep.since.isoformat(),
                    "log_excerpt": ep.log_excerpt,
                    "log_url": (f"https://dobee.example.internal/run/"
                                f"{run_id_for(ci, d)}/tc/{ep.tc}"),
                    "suspect_sha": ep.suspect_sha,
                    "confidence": ep.confidence,
                }

            summary = {
                "total": cfg["total"],
                "pass": cfg["total"] - len(active),
                "fail": len(active),
                "new_fail": sum(1 for ep in active if ep.since == d),
            }
            write_json(cfg_dir / f"{d.isoformat()}.json", {
                "schema_version": SCHEMA_VERSION,
                "config": cfg["id"],
                "date": d.isoformat(),
                "summary": summary,
                "failures": failures,
            })
            rollup_days.append({"date": d.isoformat(), **summary})

        # index.json — 데일리 파일에서 파생되는 rollup (agent가 매일 재생성)
        write_json(cfg_dir / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "config": cfg["id"],
            "label": cfg["label"],
            "updated": DATES[-1].isoformat(),
            "days": rollup_days,
        })
        manifest["configs"].append({"id": cfg["id"], "label": cfg["label"], "total": cfg["total"]})
        print(f"{cfg['id']}: {len(DATES)} daily files, "
              f"fail {rollup_days[0]['fail']} -> {rollup_days[-1]['fail']}")

    # 대시보드가 구성 목록을 하드코딩하지 않도록 하는 매니페스트
    write_json(RESULTS_DIR / "configs.json", manifest)
    print(f"wrote {RESULTS_DIR / 'configs.json'}")

    # 데일리 리포트 (당번 검수용 리뷰 초안)
    daily_dir = REVIEWS_DIR / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    for i, d in enumerate(DATES):
        prev = DATES[i - 1] if i > 0 else None
        (daily_dir / f"{d.isoformat()}.md").write_text(build_report(d, prev), encoding="utf-8")
    print(f"wrote {len(DATES)} daily reports to {daily_dir}")

    # 먼슬리 리뷰 (회고/성과 보고용) — 월 단위로 그룹핑해 생성
    monthly_dir = REVIEWS_DIR / "monthly"
    monthly_dir.mkdir(parents=True, exist_ok=True)
    months: dict[str, list[date]] = {}
    for d in DATES:
        months.setdefault(d.strftime("%Y-%m"), []).append(d)
    for ym, month_dates in months.items():
        (monthly_dir / f"{ym}.md").write_text(build_monthly_report(month_dates),
                                              encoding="utf-8")
    print(f"wrote {len(months)} monthly reviews to {monthly_dir}")


if __name__ == "__main__":
    main()
