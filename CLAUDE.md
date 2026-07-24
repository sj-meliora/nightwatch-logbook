# Nightwatch Logbook

daily regression 결과를 기록·열람하는 로그북 repo. dobee 시뮬레이션 테스트의
일자별 결과(JSON)를 시계열로 축적하고, agent가 생성한 일간 보고/월간 리뷰(md)와
GitHub Pages 정적 대시보드를 제공한다. 현재는 예시 데이터 기반 PoC 단계.

## 작동 방식

```
dobee run 결과 → results/{config}/runs/{date}-{run_id}-{sha7}.json
                                            (원본, 구성×run당 1개, 불변)
              → results/{config}/index.json (파생 rollup — 날짜별 대표+run 체인)
              → reviews/daily/{date}.md     (일간 보고 — 당번 검수용)
              → reviews/monthly/{YYYY-MM}.md (월간 리뷰 — 회고/성과 보고용)
docs/index.html ← 위 파일들을 fetch해 렌더링하는 단일 파일 대시보드
```

run은 integration pegging sha(FTL sha와 1:1) 단위이고 전 구성 공통이다.
신규 fail 판정은 run 시퀀스 diff이며, 후보 변경점 구간
`(직전 pegging..since_sha]`가 데이터에서 재구성된다.

- 운영 시 daily agent가 매일 위 파일들을 커밋하며, 커밋 = 배포다
  (Pages가 main 브랜치 root를 서빙, 대시보드 진입은 `/docs/`).
- 예시 데이터·리뷰는 `python3 scripts/generate_sample_data.py`로 재생성한다
  (구성별 fail 에피소드 정의에서 JSON/md를 파생 — 정합성이 구조적으로 보장됨).
- 로컬 확인: repo 루트에서 `python3 -m http.server` 후 `/docs/` 접속.

## 불변 원칙 (수정 시 반드시 지킬 것)

1. **원본 run JSON은 생성 후 불변이며 run 시퀀스는 append-only.** 정정이
   필요하면 새 커밋으로 파일을 교체하고, 파생물(rollup·리뷰)은 재생성한다.
2. **구성 id와 디렉토리명은 최초 부여 후 불변.** 이름 변경·추가·중단은
   `results/configs.json`의 메타데이터(`dobee_name`/`renamed_from`/`since`/
   `retired`)로만 반영한다. 디렉토리 rename 금지.
3. **개발자 아이디를 repo에 기록하지 않는다** (회사 AI 정책). suspect는
   sha로만 기록하고, 개발자 식별은 의뢰 발송 단계에서 내부 시스템으로 조회한다.
4. **`docs/index.html`은 외부 의존성 없는 단일 파일 유지** (폐쇄망 배포).
   CDN·webfont·외부 라이브러리 금지. md 렌더링도 내장 렌더러를 사용하며,
   DOM 구성은 `textContent` 기반 (`innerHTML` 금지).
5. **failures는 배열이 아닌 TC 이름 키 객체 + 키 정렬 + pretty print.**
   git diff가 사람이 읽는 리포트 역할을 하기 위한 조건이다.

## 커밋 컨벤션

형식: `<type>(<scope>): <제목>` — 제목은 한국어, 명령형, 마침표 없이.
scope는 선택이며 `dashboard` / `data` / `reports` / `scripts` 중 하나.

| type | 용도 |
|---|---|
| `feat` | 기능 추가 (대시보드 기능, 리포트 섹션, 스키마 필드 등) |
| `fix` | 버그 수정 |
| `data` | 데일리 결과·리뷰 반영 (운영 agent의 정기 커밋 전용) |
| `docs` | README·CLAUDE.md 등 문서 변경 |
| `refactor` | 동작 변경 없는 구조 개선 |
| `chore` | 빌드·설정·기타 잡무 |

예시:

```
feat(dashboard): 구성 현황 테이블에 스파크라인 추가
fix(scripts): 월간 리뷰의 운영 구간 계산 오류 수정
data: 2026-07-23 데일리 결과 및 리뷰 반영
docs: 구성 생애주기 규칙 추가
```

- 스키마(JSON 필드) 변경은 `feat`로 커밋하고 `schema_version` 증감 여부를
  본문에 명시한다 (필드 추가 = minor, 의미 변경/제거 = major).
- 커밋 본문은 "무엇을"이 아니라 "왜"를 남긴다.

### PR 머지 규칙

- **PR은 squash 머지가 기본.** main 이력을 깔끔하게 유지한다 — WIP·중간
  수정 커밋을 main에 남기지 않는다.
- squash 커밋 제목은 위 커밋 컨벤션 형식(`<type>(<scope>): <제목>`)을 따르고,
  PR 번호를 끝에 붙인다 (예: `feat(dashboard): 리포트 뷰어 추가 (#12)`).
- 예외: 운영 agent의 정기 `data` 커밋은 PR 없이 main에 직접 커밋한다
  (커밋 = 배포).

## 검증

대시보드를 수정하면 로컬 서버 + headless 브라우저(Playwright, 사전 배포
Chromium 사용)로 콘솔 에러 0 여부와 주요 인터랙션(구성 선택, 날짜 선택,
리포트 모달)을 확인한 뒤 커밋한다. 데이터 생성기를 수정하면 재생성 후
summary 합계와 failures 건수의 정합성을 확인한다.
