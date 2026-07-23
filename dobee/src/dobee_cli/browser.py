"""Playwright 브라우저 기동 — 폐쇄망 배포 대응.

폐쇄망에서는 `playwright install`이 불가능하므로 브라우저 바이너리는
사전 배포된 경로에 존재한다고 가정한다. 기동 전략은 3단계 폴백:

1. DOBEE_CHROMIUM_PATH 환경변수가 있으면 그 실행파일을 그대로 사용
2. 기본 launch — pyproject의 playwright 버전 핀이 배포된 브라우저
   리비전과 일치하면 그대로 성공
3. 리비전 불일치 시 PLAYWRIGHT_BROWSERS_PATH 아래에서 chromium-*/
   chrome-linux/chrome 을 탐색해 executable_path로 지정

3번 폴백 덕에 playwright 패키지 버전이 올라가도 CLI는 계속 동작하지만,
정상 상태는 2번(버전 핀 일치)이다. 3번으로 동작 중이면 stderr에 경고를 남긴다.
"""

import os
import sys
from pathlib import Path


def _discover_chromium() -> str | None:
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root or not Path(root).is_dir():
        return None
    candidates = sorted(Path(root).glob("chromium-*/chrome-linux/chrome"), reverse=True)
    return str(candidates[0]) if candidates else None


def launch_chromium(playwright):
    """폴백 전략에 따라 headless Chromium을 기동한다. 실패 시 예외 전파."""
    env_path = os.environ.get("DOBEE_CHROMIUM_PATH")
    if env_path:
        return playwright.chromium.launch(headless=True, executable_path=env_path)
    try:
        return playwright.chromium.launch(headless=True)
    except Exception:
        discovered = _discover_chromium()
        if discovered is None:
            raise
        print(
            f"[dobee] warn: playwright 버전과 배포 브라우저 리비전 불일치, "
            f"탐색된 실행파일로 폴백: {discovered}",
            file=sys.stderr,
        )
        return playwright.chromium.launch(headless=True, executable_path=discovered)
