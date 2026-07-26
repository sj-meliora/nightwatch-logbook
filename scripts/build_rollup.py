#!/usr/bin/env python3
"""데일리 파일들에서 구성별 index.json rollup을 재생성한다 (파생물).

exit code: 0=성공 / 2=인자 오류 / 3=IO 오류
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook


def main() -> int:
    ap = argparse.ArgumentParser(description="results/{config}/index.json 재생성.")
    ap.add_argument("--date", required=True,
                    help="rollup의 updated에 기록할 실행 일자 YYYY-MM-DD")
    ap.add_argument("--config", help="특정 구성만 재생성 (기본: 전체)")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        manifest = logbook.load_manifest(root)
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error_code": "MANIFEST_NOT_FOUND",
                          "error": "results/configs.json 없음"},
                         ensure_ascii=False))
        return 3

    targets = [m for m in manifest["configs"]
               if args.config is None or m["id"] == args.config]
    if not targets:
        print(json.dumps({"ok": False, "error_code": "CONFIG_NOT_FOUND",
                          "error": f"구성 없음: {args.config}"},
                         ensure_ascii=False))
        return 2
    for meta in targets:
        logbook.write_rollup(root, meta, updated=args.date)

    json.dump({"schema_version": logbook.SCHEMA_VERSION, "ok": True,
               "updated": args.date, "configs": [m["id"] for m in targets]},
              sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
