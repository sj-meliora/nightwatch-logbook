#!/usr/bin/env python3
"""dobee parse-result 출력(facts)을 run JSON으로 적재한다.

run은 pegging sha(FTL sha와 1:1) 단위이며 구성별 run 시퀀스에 순서대로
추가된다. 직전 run과 diff해 status/since/since_sha를 산출하고 추정 필드를
승계한다. stdout은 JSON 한 덩어리 — 신규 fail 목록(로그 발췌 포함)과
후보 변경점 구간(ftl_range)을 담고 있어, 호출측 LLM이 바로
`git log <ftl_range>`로 후보 커밋을 조회할 수 있다.

exit code: 0=성공 / 2=인자·검증 오류 / 3=IO 오류
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook


def emit(payload: dict, code: int = 0) -> int:
    json.dump({"schema_version": logbook.SCHEMA_VERSION, **payload},
              sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


def fail(msg: str, code: int = 2) -> int:
    return emit({"ok": False, "error": msg}, code)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="dobee parse-result 출력을 results/{config}/runs/에 적재.",
        epilog="facts 파일은 dobee CLI parse-result의 stdout JSON — run_id, "
               "pegging_sha, summary{total,pass,fail}, "
               "failures{tc:{log_excerpt,log_url}} 필요. 같은 날 run이 여러 개면 "
               "run_id 오름차순으로 호출한다.")
    ap.add_argument("--config", required=True, help="구성 id (예: cfg-a)")
    ap.add_argument("--date", required=True, help="run 완료 일자 YYYY-MM-DD")
    ap.add_argument("--facts", required=True, help="dobee parse-result stdout JSON 파일 경로")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    ap.add_argument("--force", action="store_true",
                    help="기존 run 파일 덮어쓰기 허용 (원본 불변 원칙의 예외 — 정정 시에만)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not logbook.DATE_RE.match(args.date):
        return fail(f"날짜 형식 오류: {args.date}")
    try:
        manifest = logbook.load_manifest(root)
    except FileNotFoundError:
        return fail(f"{root}/results/configs.json 없음 — --root 확인", 3)

    meta = next((m for m in manifest["configs"] if m["id"] == args.config), None)
    if meta is None:
        return fail(f"매니페스트에 없는 구성: {args.config}")
    if not logbook.op_on(meta, args.date):
        return fail(f"{args.config}는 {args.date}에 미운영 (since/retired 확인)")

    try:
        facts = logbook.load_json(Path(args.facts))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return fail(f"facts 파일 오류: {e}", 3)
    failures = facts.get("failures")
    summary = facts.get("summary", {})
    run_id = facts.get("run_id")
    pegging = facts.get("pegging_sha", "")
    if failures is None or "total" not in summary:
        return fail("facts에 failures/summary.total 필요 (dobee parse-result 출력인지 확인)")
    if not isinstance(run_id, int):
        return fail("facts에 run_id(정수) 필요")
    if not logbook.SHA_RE.match(pegging):
        return fail(f"facts의 pegging_sha 형식 오류: {pegging!r}")
    if summary.get("fail") != len(failures):
        return fail(f"facts summary.fail={summary.get('fail')} != failures {len(failures)}건")

    # run 시퀀스는 append-only — 뒤늦게 과거 run을 끼워 넣으면 diff가 깨진다
    runs = logbook.list_runs(root, args.config)
    if runs and (args.date, run_id) <= (runs[-1]["date"], runs[-1]["run_id"]) \
            and not args.force:
        return fail(f"run 순서 위반: 마지막 run은 {runs[-1]['date']} #{runs[-1]['run_id']} — "
                    f"이 run({args.date} #{run_id})은 그 이후여야 함")

    target = logbook.run_path(root, args.config, args.date, run_id, pegging)
    if target.exists() and not args.force:
        return fail(f"{target.name} 이미 존재 — 원본은 불변. 정정이면 --force 사용")

    prev_run = logbook.load_json(runs[-1]["path"]) if runs else None
    run = logbook.build_run(args.config, args.date, run_id, pegging,
                            summary["total"], failures, prev_run)
    logbook.write_json(target, run)

    prev_failures = (prev_run or {}).get("failures", {})
    return emit({
        "ok": True,
        "config": args.config,
        "date": args.date,
        "run_id": run_id,
        "pegging_sha": pegging[:7],
        "prev_pegging_sha": prev_run["pegging_sha"] if prev_run else None,
        # 이 run에서 처음 fail한 TC들의 후보 변경점 구간 (FTL repo 기준)
        "ftl_range": (f"{prev_run['pegging_sha']}..{pegging[:7]}" if prev_run else None),
        "summary": run["summary"],
        "new": [{"tc": tc, "log_excerpt": e["log_excerpt"], "log_url": e["log_url"]}
                for tc, e in sorted(run["failures"].items()) if e["status"] == "new"],
        "fixed": [tc for tc in sorted(prev_failures) if tc not in run["failures"]],
        "path": str(target.relative_to(root)),
    })


if __name__ == "__main__":
    sys.exit(main())
