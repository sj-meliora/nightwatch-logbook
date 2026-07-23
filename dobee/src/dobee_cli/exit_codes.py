"""exit code 규약 — 테스트 fail(정상 동작)과 인프라 문제를 구분한다.

호출측(스킬/스케줄 워크플로우)은 이 코드로 재시도 여부를 결정한다:
TEST_FAILURES는 정상적인 결과이므로 재시도 금지, INFRA_ERROR만 재시도 대상.
"""

OK = 0              # 성공 (parse-result의 경우: 전부 pass)
TEST_FAILURES = 1   # 정상 동작했고 결과에 fail이 존재 (parse-result 전용)
USAGE_ERROR = 2     # 잘못된 인자/입력
INFRA_ERROR = 3     # dobee 접속 실패, 브라우저 기동 실패 등 인프라 문제
