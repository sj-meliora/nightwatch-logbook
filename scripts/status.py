#!/usr/bin/env python3
"""적재 상태 조회 — 구성별 마지막 적재 run을 JSON으로 반환한다 (읽기 전용).

스킬이 "미처리 run 열거"를 하기 위한 재개 지점 계약이다: dobee에서
run_id가 이 값보다 큰 run들이 각 구성의 미처리분이다. runs/ 파일명을
직접 파싱하지 말고 이 출력을 사용한다 (파일 구조는 내부 구현).

구성별로 값이 다를 수 있다 — 이전 실행이 중간에 실패했으면 일부 구성만
앞서 있다. 그래서 재개는 전역 run_id가 아니라 구성별 last_run 기준이어야
한다 (resume_min_run_id는 참고용 요약값).

exit code: 0=성공 / 3=IO 오류
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook


def main() -> int:
    ap = argparse.ArgumentParser(
        description="구성별 마지막 적재 run 조회 (미처리 run 열거의 기준점).")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        manifest = logbook.load_manifest(root)
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error": "results/configs.json 없음 — --root 확인"},
                         ensure_ascii=False))
        return 3

    configs = []
    active_last_ids = []
    for meta in manifest["configs"]:
        runs = logbook.list_runs(root, meta["id"])
        last = runs[-1] if runs else None
        entry = {
            "id": meta["id"],
            "last_run": ({"date": last["date"], "run_id": last["run_id"],
                          "pegging_sha": last["sha"]} if last else None),
            "runs_total": len(runs),
        }
        for key in ("since", "retired"):
            if key in meta:
                entry[key] = meta[key]
        configs.append(entry)
        if "retired" not in meta and last:
            active_last_ids.append(last["run_id"])

    json.dump({
        "schema_version": logbook.SCHEMA_VERSION,
        "ok": True,
        "configs": configs,
        # 운영(비 retired) 구성 중 가장 뒤처진 적재 지점 — 전 구성이 여기까지는 도달함.
        # 실제 재개는 구성별 last_run 기준으로 할 것 (참고용 요약값)
        "resume_min_run_id": min(active_last_ids) if active_last_ids else None,
        "latest_run_id": max(active_last_ids) if active_last_ids else None,
        "latest_date": max((c["last_run"]["date"] for c in configs if c["last_run"]),
                           default=None),
    }, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
