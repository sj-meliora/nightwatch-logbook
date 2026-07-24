#!/usr/bin/env python3
"""dobee parse-result 출력(facts)을 데일리 JSON으로 적재한다.

전일 파일과 diff해 status/since를 산출하고 추정 필드를 승계한다.
stdout은 JSON 한 덩어리 — 신규 fail 목록(로그 발췌 포함)을 담고 있어
호출측 LLM이 파일을 다시 열지 않고 매핑 작업을 시작할 수 있다.

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
        description="dobee parse-result 출력을 results/{config}/{date}.json으로 적재.",
        epilog="facts 파일은 dobee CLI parse-result의 stdout JSON "
               "(summary{total,pass,fail} + failures{tc:{log_excerpt,log_url}}).")
    ap.add_argument("--config", required=True, help="구성 id (예: cfg-a)")
    ap.add_argument("--date", required=True, help="적재 일자 YYYY-MM-DD")
    ap.add_argument("--facts", required=True, help="dobee parse-result stdout JSON 파일 경로")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    ap.add_argument("--force", action="store_true",
                    help="기존 데일리 파일 덮어쓰기 허용 (원본 불변 원칙의 예외 — 정정 시에만)")
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

    target = logbook.daily_path(root, args.config, args.date)
    if target.exists() and not args.force:
        return fail(f"{target} 이미 존재 — 원본은 불변. 정정이면 --force 사용")

    try:
        facts = logbook.load_json(Path(args.facts))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return fail(f"facts 파일 오류: {e}", 3)
    failures = facts.get("failures")
    summary = facts.get("summary", {})
    if failures is None or "total" not in summary:
        return fail("facts에 failures/summary.total 필요 (dobee parse-result 출력인지 확인)")
    if summary.get("fail") != len(failures):
        return fail(f"facts summary.fail={summary.get('fail')} != failures {len(failures)}건")

    prev_iso = logbook.prev_date_of(root, manifest, args.date)
    prev_daily = logbook.load_daily(root, args.config, prev_iso) if prev_iso else None

    daily = logbook.build_daily(args.config, args.date, summary["total"], failures, prev_daily)
    logbook.write_json(target, daily)

    prev_failures = (prev_daily or {}).get("failures", {})
    return emit({
        "ok": True,
        "config": args.config,
        "date": args.date,
        "prev_date": prev_daily["date"] if prev_daily else None,
        "summary": daily["summary"],
        "new": [{"tc": tc, "log_excerpt": e["log_excerpt"], "log_url": e["log_url"]}
                for tc, e in sorted(daily["failures"].items()) if e["status"] == "new"],
        "fixed": [tc for tc in sorted(prev_failures) if tc not in daily["failures"]],
        "path": str(target.relative_to(root)),
    })


if __name__ == "__main__":
    sys.exit(main())
