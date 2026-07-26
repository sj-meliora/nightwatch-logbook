#!/usr/bin/env python3
"""적재 상태·조사 대상 조회 — 스킬 데일리 실행의 시작점 (읽기 전용).

두 가지 질문에 답한다:

1. **오늘 어떤 구성을 조사해야 하는가** — `investigate[]`: 기준일에 운영
   중(since <= date < retired)인 구성 id 목록. dobee 조회에는 각 구성의
   `dobee_name`을 쓴다 (이름 변경 이력이 있어도 id는 불변).
2. **어디부터가 미처리 run인가** — 구성별 `last_run`: dobee에서 run_id가
   이 값보다 큰 run들이 그 구성의 미처리분이다. 직전 실행이 중간에
   실패했으면 구성별 값이 갈리므로, 재개는 전역 run_id가 아니라 구성별
   last_run 기준이어야 한다 (resume_min_run_id는 참고용 요약값).
   last_run이 null이면 첫 적재(baseline) — 시작 run은 스킬 설정으로 정한다.

runs/ 파일명이나 configs.json을 직접 파싱하지 말고 이 출력을 사용한다
(파일 구조는 내부 구현, stdout JSON이 계약).

exit code: 0=성공 / 2=인자 오류 / 3=IO 오류
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook


def main() -> int:
    ap = logbook.JsonArgumentParser(
        description="조사 대상 구성과 구성별 마지막 적재 run 조회.")
    ap.add_argument("--root", default=".", help="nightwatch-logbook repo 루트 (기본: cwd)")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="운영 여부 판정 기준일 YYYY-MM-DD (기본: 오늘)")
    args = ap.parse_args()

    if not logbook.DATE_RE.match(args.date):
        print(json.dumps({"ok": False, "error_code": "INVALID_DATE",
                          "error": f"날짜 형식 오류: {args.date}"},
                         ensure_ascii=False))
        return 2
    root = Path(args.root).resolve()
    try:
        manifest = logbook.load_manifest(root)
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error_code": "MANIFEST_NOT_FOUND",
                          "error": "results/configs.json 없음 — --root 확인"},
                         ensure_ascii=False))
        return 3

    configs = []
    active_last_ids = []
    for meta in manifest["configs"]:
        runs = logbook.list_runs(root, meta["id"])
        last = runs[-1] if runs else None
        operating = logbook.op_on(meta, args.date)
        entry = {
            "id": meta["id"],
            "label": meta["label"],
            # dobee 조회 키 — 별도 지정이 없으면 id와 동일
            "dobee_name": meta.get("dobee_name", meta["id"]),
            "operating": operating,
            "last_run": ({"date": last["date"], "run_id": last["run_id"],
                          "pegging_sha": last["sha"]} if last else None),
            "runs_total": len(runs),
        }
        for key in ("since", "retired"):
            if key in meta:
                entry[key] = meta[key]
        configs.append(entry)
        if operating and last:
            active_last_ids.append(last["run_id"])

    json.dump({
        "schema_version": logbook.SCHEMA_VERSION,
        "ok": True,
        "date": args.date,
        # 기준일의 조사(수집) 대상 — 스킬은 이 목록의 구성만 처리한다
        "investigate": [c["id"] for c in configs if c["operating"]],
        "configs": configs,
        # 조사 대상 구성 중 가장 뒤처진 적재 지점 — 전 구성이 여기까지는 도달함.
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
