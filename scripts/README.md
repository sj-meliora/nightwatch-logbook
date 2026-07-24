# scripts/ — 데일리 파이프라인

autodevops nightwatch 스킬이 호출하는 production 진입점과, 같은 쓰기 경로를
공유하는 예시 데이터 생성기. **쓰기 로직(스키마·diff·템플릿)은 이 repo가
소유하고, 스킬은 오케스트레이션만 한다** — repo를 clone한 시점의 스크립트를
쓰므로 스킬과 스키마 사이 버전 스큐가 없다.

## 데일리 실행 흐름 (스킬 관점)

```
1. setup_workspace.sh            # 스킬 소유 — repo clone까지만
2. status.py                     # 조사 대상 + 적재 상태 조회 (읽기 전용)
   └ investigate[] = 오늘 조사할 구성 (운영 중인 것만), dobee 조회 키는
     각 구성의 dobee_name. 어떤 구성을 볼지 스킬이 판단하지 않는다
   └ 미처리 run = dobee에서 각 구성의 last_run.run_id 이후 pegging들.
     구성별 값이 다를 수 있다(직전 실행이 중간 실패한 경우) — 구성별로 재개.
     last_run이 null이면 첫 적재(baseline): 시작 run은 스킬 설정으로 정하고,
     첫 run은 전체 fail이 신규로 기록되므로 매핑(4단계)을 건너뛴다
3. run마다: dobee parse-result → ingest_run.py (run_id 오름차순)
   └ stdout: new[] = 이 run에서 유입된 신규 fail (로그 발췌 포함)
             ftl_range = "직전pegging..이번pegging" — 후보 변경점 구간
4. (LLM) 신규 fail마다 `git log <ftl_range>`로 후보 FTL 커밋 조회 → 매핑
   → mapping.json 작성. 구간에 커밋이 1개면 사실상 확정(high),
     구간이 비어 있으면 원인이 FTL 밖 — 매핑하지 말고 unknown으로 남긴다
5. apply_mapping.py              # 검증 후 추정 필드 기입 (원자적, 실패 시 exit 2)
6. build_rollup.py               # index.json 재생성
7. render_reviews.py             # 일간 보고 + 월간 리뷰 렌더링
8. git commit "data: YYYY-MM-DD 데일리 결과 및 리뷰 반영" + push
```

각 스크립트의 인자는 `--help`로 확인한다 (스킬 문서에 사용법을 중복 기술하지
않는다). 공통 계약: **stdout은 JSON 한 덩어리** (`schema_version` 포함),
exit code는 `0`=성공 / `2`=인자·검증 오류 (LLM이 읽고 재시도) / `3`=IO 오류.

## mapping.json — LLM이 작성하는 유일한 파일

```json
[
  {
    "config": "cfg-a",
    "tc": "TC_FTL_GC_017",
    "suspect_sha": "a3f9c21",
    "confidence": "high",
    "rationale": "후보 구간 내 유일한 커밋이며 fail 위치(gc_victim.c:412)가 변경 범위와 일치"
  }
]
```

- 대상은 그날 **신규 유입(since==당일) fail만** — ingest 출력의 `new[]` 참조.
  해당 TC가 등장하는 당일의 모든 run 파일에 기입된다
- `confidence`: `high` | `medium` | `unknown`
- `rationale`(선택)은 일간 보고의 근거 슬롯으로만 삽입된다 — LLM이 md를
  직접 쓰지 않으므로 리포트 서식은 항상 대시보드 렌더러와 호환된다
- 검증 실패 시 아무것도 쓰지 않고 `errors[]`를 반환한다 (원자적)

## 파일 목록

| 파일 | 역할 |
|---|---|
| `logbook.py` | 공용 모듈 — 직렬화·run 시퀀스 diff·rollup·md 템플릿 (모든 쓰기의 단일 경로) |
| `status.py` | 조사 대상 구성(investigate) + 구성별 마지막 적재 run 조회 (읽기 전용) |
| `ingest_run.py` | facts JSON → `results/{config}/runs/` (append-only — 덮어쓰기는 `--force`) |
| `apply_mapping.py` | mapping.json 검증·기입 |
| `build_rollup.py` | `index.json` 재생성 |
| `render_reviews.py` | `reviews/daily/` + `reviews/monthly/` 렌더링 |
| `generate_sample_data.py` | 예시 데이터 생성 — run 타임라인·에피소드 정의만 소유, 쓰기는 logbook 경유 |

예시 데이터와 production이 같은 경로를 지나므로, 쓰기 로직 수정 후
`python3 scripts/generate_sample_data.py && git diff` 로 회귀를 확인할 수 있다
(의도한 변경 외 diff가 나오면 안 된다).
