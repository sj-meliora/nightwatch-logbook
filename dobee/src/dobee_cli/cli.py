"""dobee CLI — stdout JSON이 곧 인터페이스 계약이다.

- 모든 출력은 JSON 한 덩어리 + `schema_version` 필드. 사람용 출력 금지
  (진단 메시지는 전부 stderr).
- 스킬 문서는 사용법을 중복 기술하지 않는다: "먼저 `dobee <cmd> --help` 실행".
- exit code 규약은 exit_codes.py 참조.

서브커맨드:
  submit        테스트 제출 (dobee 벤더 CLI 래핑, PoC에서는 --mock)
  parse-result  결과 웹페이지를 headless 브라우저로 파싱해 JSON으로 변환
"""

import argparse
import json
import os
import subprocess
import sys

from . import SCHEMA_VERSION
from . import exit_codes as ec
from .browser import launch_chromium

MAX_LOG_EXCERPT = 400  # 실패 로그는 발췌만 커밋, 원본은 log_url 참조 (repo 비대화 방지)


def _emit(payload: dict) -> None:
    payload = {"schema_version": SCHEMA_VERSION, **payload}
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _emit_error(command: str, kind: str, message: str, code: int) -> int:
    _emit({"command": command, "error": {"kind": kind, "message": message}, "ok": False})
    return code


# ---------------------------------------------------------------- submit

def cmd_submit(args: argparse.Namespace) -> int:
    """벤더 dobee CLI(DOBEE_BIN, 기본 'dobee-vendor')를 래핑해 테스트를 제출한다.

    PoC에서는 벤더 CLI가 없으므로 --mock으로 제출 성공 응답을 시뮬레이션한다.
    """
    if args.mock:
        run_id = f"mock-{args.config}-{args.sha[:7]}"
        _emit({
            "command": "submit",
            "ok": True,
            "run_id": run_id,
            "config": args.config,
            "sha": args.sha,
            "status": "accepted",
            "result_url": f"https://dobee.example.internal/run/{run_id}",
        })
        return ec.OK

    vendor = os.environ.get("DOBEE_BIN", "dobee-vendor")
    try:
        proc = subprocess.run(
            [vendor, "submit", "--config", args.config, "--sha", args.sha],
            capture_output=True, text=True, timeout=args.timeout,
        )
    except FileNotFoundError:
        return _emit_error("submit", "infra",
                           f"벤더 CLI를 찾을 수 없음: {vendor} (DOBEE_BIN 확인)", ec.INFRA_ERROR)
    except subprocess.TimeoutExpired:
        return _emit_error("submit", "infra",
                           f"제출 타임아웃 ({args.timeout}s)", ec.INFRA_ERROR)
    if proc.returncode != 0:
        return _emit_error("submit", "infra", proc.stderr.strip()[:500], ec.INFRA_ERROR)

    # 벤더 CLI stdout에서 run id를 추출해 계약된 JSON으로 재포장한다.
    run_id = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    _emit({
        "command": "submit",
        "ok": True,
        "run_id": run_id,
        "config": args.config,
        "sha": args.sha,
        "status": "accepted",
        "result_url": f"https://dobee.example.internal/run/{run_id}",
    })
    return ec.OK


# ---------------------------------------------------------- parse-result

def cmd_parse_result(args: argparse.Namespace) -> int:
    """dobee 결과 웹페이지(JS 렌더링)를 Playwright로 열어 구조화 JSON으로 뽑는다.

    출력은 dobee가 알려주는 사실(facts)만 담는다 — status/since/suspect 같은
    판단 필드는 하류(diff 스크립트, agent)가 채운다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _emit_error("parse-result", "infra",
                           "playwright 미설치 (pip install 'playwright==1.56.0')", ec.INFRA_ERROR)

    try:
        with sync_playwright() as p:
            browser = launch_chromium(p)
            page = browser.new_page()
            page.goto(args.url, timeout=args.timeout * 1000)
            # dobee 결과 페이지는 JS가 테이블을 그린다 — 렌더 완료를 기다린다.
            page.wait_for_selector("#results-table tbody tr", timeout=args.timeout * 1000)

            summary = {
                "total": int(page.text_content("#summary-total").strip()),
                "pass": int(page.text_content("#summary-pass").strip()),
                "fail": int(page.text_content("#summary-fail").strip()),
            }
            failures = {}
            for row in page.query_selector_all("#results-table tbody tr[data-status='fail']"):
                tc = row.get_attribute("data-tc")
                excerpt = (row.query_selector(".log-excerpt").text_content() or "").strip()
                failures[tc] = {
                    "log_excerpt": excerpt[:MAX_LOG_EXCERPT],
                    "log_url": row.query_selector("a.log-link").get_attribute("href"),
                }
            browser.close()
    except Exception as e:  # 접속 실패/렌더 타임아웃/브라우저 기동 실패 = 인프라 문제
        return _emit_error("parse-result", "infra", str(e)[:500], ec.INFRA_ERROR)

    if summary["fail"] != len(failures):
        return _emit_error(
            "parse-result", "parse",
            f"summary fail={summary['fail']} 와 실패 행 수={len(failures)} 불일치 — 페이지 구조 변경 의심",
            ec.INFRA_ERROR,
        )

    _emit({
        "command": "parse-result",
        "ok": True,
        "config": args.config,
        "date": args.date,
        "source_url": args.url,
        "summary": summary,
        "failures": failures,
    })
    return ec.OK if summary["fail"] == 0 else ec.TEST_FAILURES


# ------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dobee",
        description="dobee 서비스 접근 프리미티브. 모든 stdout은 JSON (schema_version 포함).",
        epilog="exit codes: 0=성공(전부 pass) / 1=결과에 fail 존재(정상) / 2=인자 오류 / 3=인프라 오류",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="테스트 제출",
                              description="dobee에 테스트를 제출하고 run_id/result_url을 JSON으로 반환.")
    p_submit.add_argument("--config", required=True, help="시뮬레이션 구성 이름 (예: cfg-a)")
    p_submit.add_argument("--sha", required=True, help="테스트 대상 커밋 SHA")
    p_submit.add_argument("--timeout", type=int, default=60, help="제출 타임아웃 초 (기본 60)")
    p_submit.add_argument("--mock", action="store_true",
                          help="벤더 CLI 없이 제출 성공을 시뮬레이션 (PoC용)")
    p_submit.set_defaults(func=cmd_submit)

    p_parse = sub.add_parser("parse-result", help="결과 웹페이지 파싱",
                             description="결과 페이지를 headless Chromium으로 렌더링해 "
                                         "summary/failures를 JSON으로 반환. 사실(facts)만 출력.")
    p_parse.add_argument("--url", required=True, help="결과 페이지 URL (PoC에서는 file:// 허용)")
    p_parse.add_argument("--config", required=True, help="시뮬레이션 구성 이름")
    p_parse.add_argument("--date", required=True, help="결과 기준일 YYYY-MM-DD")
    p_parse.add_argument("--timeout", type=int, default=30, help="페이지 로드 타임아웃 초 (기본 30)")
    p_parse.set_defaults(func=cmd_parse_result)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse 인자 오류(exit 2)는 규약과 일치. --help(exit 0)도 그대로 통과.
        return int(e.code or 0)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
