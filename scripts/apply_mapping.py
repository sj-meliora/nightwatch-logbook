#!/usr/bin/env python3
"""LLM의 변경점 매핑 결과(mapping.json)를 데일리 JSON에 기입한다.

mapping.json 형식 (LLM이 작성하는 유일한 파일):

    [
      {
        "config": "cfg-a",
        "tc": "TC_FTL_GC_017",
        "suspect_sha": "a3f9c21",
        "confidence": "high",            // high | medium | unknown
        "rationale": "fail 위치가 해당 PR의 변경 범위와 일치"   // 선택
      }
    ]

전 항목을 먼저 검증하고, 하나라도 실패하면 아무것도 쓰지 않는다(원자적).
검증 실패는 exit 2 + errors 배열 — 호출측 LLM이 읽고 재시도한다.

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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="mapping.json의 suspect_sha/confidence/rationale을 "
                    "해당 일자 데일리 JSON의 신규 fail 항목에 기입.")
    ap.add_argument("--date", required=True, help="대상 일자 YYYY-MM-DD")
    ap.add_argument("--mapping", required=True, help="mapping.json 경로")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        entries = logbook.load_json(Path(args.mapping))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return emit({"ok": False, "error": f"mapping 파일 오류: {e}"}, 3)
    if not isinstance(entries, list):
        return emit({"ok": False, "error": "mapping.json은 항목 배열이어야 함"}, 2)

    # 1단계: 전 항목 검증 (원자적 적용을 위해 쓰기 전에 모두 확인)
    errors: list[str] = []
    dailies: dict[str, dict] = {}
    for i, e in enumerate(entries):
        cid = e.get("config")
        if cid not in dailies:
            daily = logbook.load_daily(root, cid, args.date) if cid else None
            if daily is None:
                errors.append(f"[{i}] config={cid!r}: {args.date} 데일리 파일 없음")
                continue
            dailies[cid] = daily
        problem = logbook.validate_mapping_entry(e, dailies[cid])
        if problem:
            errors.append(f"[{i}] {cid}: {problem}")
    if errors:
        return emit({"ok": False, "errors": errors,
                     "hint": "신규(status=new) TC만 매핑 대상. ingest 출력의 new 배열 참조"}, 2)

    # 2단계: 기입
    by_cfg: dict[str, list[dict]] = {}
    for e in entries:
        by_cfg.setdefault(e["config"], []).append(e)
    for cid, cfg_entries in by_cfg.items():
        logbook.apply_entries(dailies[cid], cfg_entries)
        logbook.write_json(logbook.daily_path(root, cid, args.date), dailies[cid])

    return emit({
        "ok": True,
        "date": args.date,
        "applied": [{"config": e["config"], "tc": e["tc"],
                     "suspect_sha": e["suspect_sha"], "confidence": e["confidence"]}
                    for e in entries],
    })


if __name__ == "__main__":
    sys.exit(main())
