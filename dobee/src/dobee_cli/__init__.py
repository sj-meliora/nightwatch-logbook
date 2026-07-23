"""dobee 공유 feature 모듈: dobee 서비스 접근 프리미티브 (Capability 층).

워크플로우(Intent 층)는 여기 없다 — 스킬(sim-test-sop / regression-review)이
이 CLI를 조합해서 쓴다.
"""

__version__ = "0.1.0"

# stdout JSON 계약 버전. 필드 추가는 minor, 필드 의미 변경/제거는 major.
SCHEMA_VERSION = 1
