#!/usr/bin/env python3
"""데일리 파일들에서 일간 보고와 월간 리뷰 md를 렌더링한다 (파생물).

md 템플릿은 logbook 모듈이 소유하며 대시보드 내장 렌더러가 지원하는
문법 부분집합만 사용한다. LLM 텍스트는 데일리 JSON의 rationale 필드를
통해서만 유입된다 (apply_mapping.py 참조).

exit code: 0=성공 / 2=인자 오류 / 3=IO 오류
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook


def main() -> int:
    ap = argparse.ArgumentParser(
        description="reviews/daily/{date}.md + reviews/monthly/{YYYY-MM}.md 렌더링.")
    ap.add_argument("--date", required=True, help="대상 일자 YYYY-MM-DD")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    ap.add_argument("--daily-only", action="store_true", help="일간 보고만 렌더링")
    ap.add_argument("--monthly-only", action="store_true", help="월간 리뷰만 렌더링")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        manifest = logbook.load_manifest(root)
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error": "results/configs.json 없음"},
                         ensure_ascii=False))
        return 3

    wrote = []
    try:
        if not args.monthly_only:
            p = root / "reviews" / "daily" / f"{args.date}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(logbook.render_daily_md(root, manifest, args.date),
                         encoding="utf-8")
            wrote.append(str(p.relative_to(root)))
        if not args.daily_only:
            ym = args.date[:7]
            p = root / "reviews" / "monthly" / f"{ym}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(logbook.render_monthly_md(root, manifest, ym),
                         encoding="utf-8")
            wrote.append(str(p.relative_to(root)))
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2

    json.dump({"schema_version": logbook.SCHEMA_VERSION, "ok": True, "wrote": wrote},
              sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
