# nightwatch-logbook

daily regression 결과를 기록하는 로그북 repo의 PoC입니다.
**열흘치 daily log 예시 데이터**와 이를 보여주는 **정적 대시보드**로 구성됩니다.

## 구조

```
results/
├─ configs.json                  # 구성 목록 매니페스트 (대시보드가 구성을 하드코딩하지 않도록)
├─ {config}/{YYYY-MM-DD}.json    # 데일리 원본 — 생성 후 불변, JSON이 source of truth
└─ {config}/index.json           # 파생 rollup — 날짜별 summary 배열, 매일 재생성
reviews/
├─ daily/{YYYY-MM-DD}.md         # agent 생성 데일리 리뷰 초안 (당번 검수용)
└─ monthly/{YYYY-MM}.md          # 월간 회고/성과 보고용 리뷰 (매핑 커버리지 포함)
docs/
└─ index.html                    # 정적 대시보드 (의존성 없는 단일 파일, GitHub Pages용)
scripts/
└─ generate_sample_data.py       # 예시 데이터 생성기 (2026-07-14 ~ 07-23, 구성 3개)
```

### 데일리 JSON 스키마 (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "config": "cfg-a",
  "date": "2026-07-23",
  "summary": { "total": 900, "pass": 872, "fail": 28, "new_fail": 3 },
  "failures": {
    "TC_FTL_GC_017": {
      "status": "new",
      "since": "2026-07-23",
      "log_excerpt": "ASSERT at gc_victim.c:412 ...",
      "log_url": "https://dobee.example.internal/run/8799/tc/TC_FTL_GC_017",
      "suspect_sha": "a3f9c21",
      "confidence": "high"
    }
  }
}
```

- `failures`는 배열이 아닌 **TC 이름 키 객체 + 키 정렬 + pretty print** —
  git diff 자체가 TC 추가/제거를 사람이 읽을 수 있는 형태로 보여줍니다.
- 실패 로그는 **발췌 + 링크만** 커밋하고 원본은 dobee URL을 참조합니다.
- `suspect_sha` / `confidence`(high·medium·unknown)는 **agent 추정 결과**로,
  dobee가 알려주는 사실(로그)과 구분됩니다.
- **회사 AI 정책: 개발자 아이디는 repo에 기록하지 않습니다.** suspect는 sha로만
  기록하고, 분석 의뢰 발송 단계에서 내부 시스템으로 sha → 개발자를 조회합니다.
- git 이력이 시계열 DB 역할을 합니다: 전날 대비 diff는 파일 비교,
  fail 시작 시점은 `git log`로 추적합니다.

### 구성 생애주기 (configs.json)

```json
{
  "id": "cfg-a",                       // 불변 — 디렉토리명·시계열 연속성의 기준
  "label": "FTL full regression",      // 표시용, 자유롭게 변경
  "total": 900,
  "dobee_name": "ftl_full_regr_v2",    // dobee 조회 키 — 이름이 바뀌면 여기만 갱신
  "renamed_from": ["ftl_full_regr"],   // 이름 변경 이력 (선택)
  "since": "2026-07-18",               // 운영 시작일 (선택)
  "retired": "2026-07-20"              // 운영 중단일 — 그날부터 미운영 (선택)
}
```

- **id는 최초 부여 후 불변**입니다. dobee 쪽 구성 이름 변경은 `dobee_name`
  메타데이터로만 반영하고 디렉토리는 rename하지 않습니다.
- 데이터 파일은 운영 기간(`since` ≤ 날짜 < `retired`)에만 존재합니다.
  중단된 구성도 디렉토리와 이력은 보존합니다.
- 대시보드는 부분 이력을 그대로 처리합니다: 중간 추가/중단 구성은 차트
  라인이 끊기고, 현황 테이블에 운영 시작/중단 칩이 표시되며, 스탯의
  "fail 없는 구성" 분모는 당일 운영 구성 수입니다.

## 대시보드 보기

로컬:

```sh
python3 -m http.server        # repo 루트에서 실행
# http://localhost:8000/docs/ 접속
```

GitHub Pages: Pages 소스를 **root**로 설정하면 `/docs/`에서 접근 가능합니다
(대시보드 JS가 `../results/`를 fetch하므로 repo 루트가 서빙 루트여야 합니다).

기능: 구성 현황 테이블(신규·증감 순 정렬, 행별 스파크라인), fail 추이 차트
(행 클릭으로 전체 합계/구성 전환, 신규 발생일 마커), 전체 구성 × 날짜 매트릭스,
날짜별 failure 상세(신규 fail 상단 정렬·자동 펼침, suspect sha·확신도, dobee 로그
링크). suspect sha를 클릭하면 해당일 `reviews/daily/{date}.md` 리포트가 모달로
열리고 그 변경점 섹션으로 이동합니다 ("일간 보고" 버튼으로도 열람).
"월간 리뷰" 버튼은 선택된 날짜가 속한 달의 `reviews/monthly/{YYYY-MM}.md`를 엽니다. 라이트/다크 테마 지원.

## 예시 데이터 재생성

```sh
python3 scripts/generate_sample_data.py
```

구성은 11개(`cfg-a` ~ `cfg-k`, TC 150~900)로, 만성 fail 위에 신규/해소
에피소드가 오가는 시나리오입니다. `cfg-a`(FTL full regression, TC 900)의
7/23은 설계 문서 예시(900/872/28/신규 3)와 일치하고, `cfg-a`와 `cfg-d`에
같은 변경점(`a3f9c21`)이 의심되는 교차 구성 신호도 포함되어 있습니다.
`cfg-c`/`cfg-h`는 대부분 all green입니다.
