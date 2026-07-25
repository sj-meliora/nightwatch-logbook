#!/usr/bin/env python3
"""integration pegging sha → 실제 반영된 FTL 커밋 해석 (읽기 전용).

FTL develop이 a→b로 바뀌면 integration_ftl에 'sub-module update(FTL)'
커밋으로 반영되고, pegging 커밋 트리의 submodule gitlink가 그 시점의
FTL sha를 가리킨다. 그런데 integration_ftl은 잘못된 pegging을 정정할 때
그 위에 쌓인 pegging까지 reset(force push)되는 일이 잦아, 브랜치 이력에
기대는 조회(integration에서의 `git log A..B`, first-parent 추적 등)는
구간이 통째로 사라지거나 ancestry가 끊겨 깨진다. 이 스크립트는 이력을
신뢰하지 않는다:

1. pegging 커밋을 sha로 직접 지정해 gitlink를 읽는다 — reset으로
   unreachable해져도 object가 로컬 odb에 남아 있으면 해석된다
2. FTL 커밋 열거는 integration이 아니라 **FTL repo에서 두 gitlink sha의
   rev-list 차집합**으로 계산한다. 이번 구간에 새로 반영된 커밋(added)과
   reset으로 되돌려진 커밋(removed)이 구분되어 나온다

ingest_run.py stdout의 `ftl_range`("직전pegging..이번pegging")를 그대로
인자로 넘기는 것이 데일리 흐름의 표준 사용법이다. added가 1개면 사실상
확정(high), added가 비어 있으면 원인이 FTL 밖이다 (removed만 있으면
되돌림 자체가 원인일 수 있으니 수동 판단).

회사 AI 정책에 따라 출력에 author 등 개발자 식별 정보는 싣지 않는다
(sha·날짜·제목만).

exit code: 0=성공 / 2=인자·해석 오류 (--fetch 등으로 재시도 가능) /
3=repo 접근 오류
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook


def emit(payload: dict, code: int = 0) -> int:
    json.dump({"schema_version": logbook.SCHEMA_VERSION, **payload},
              sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


def fail(msg: str, code: int = 2) -> int:
    return emit({"ok": False, "error": msg}, code)


def git(repo: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def is_git_repo(repo: Path) -> bool:
    return git(repo, "rev-parse", "--git-dir")[0] == 0


def resolve_commit(repo: Path, rev: str, fetch: bool) -> str | None:
    """rev → 전체 commit sha. unreachable이어도 object만 있으면 된다."""
    rc, out, _ = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    if rc == 0:
        return out
    if fetch:
        # reset으로 사라진 sha는 서버 설정에 따라 fetch가 거부될 수 있다 — best effort
        git(repo, "fetch", "--quiet", "origin", rev)
        rc, out, _ = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
        if rc == 0:
            return out
    return None


def gitlink_at(repo: Path, commit: str, subpath: str) -> tuple[str | None, str | None]:
    """commit 트리의 submodule gitlink sha. 반환 (sha, 오류사유)."""
    rc, out, err = git(repo, "ls-tree", commit, "--", subpath)
    if rc != 0:
        return None, err or f"ls-tree 실패: {commit[:7]}"
    if not out:
        return None, f"pegging {commit[:7]} 트리에 {subpath!r} 경로 없음 — --submodule 확인"
    mode, otype, sha = out.split(None, 3)[:3]
    if mode != "160000" or otype != "commit":
        return None, f"{subpath!r}는 submodule(gitlink)이 아님 (mode={mode}, type={otype})"
    return sha, None


def is_ancestor(repo: Path, a: str, b: str) -> bool | None:
    rc, _, _ = git(repo, "merge-base", "--is-ancestor", a, b)
    return rc == 0 if rc in (0, 1) else None  # object 부재 등 판정 불가 → None


def list_commits(repo: Path, spec: str, limit: int) -> tuple[list[dict], int, bool]:
    """spec('A..B') 차집합의 커밋 목록 (신규순). 반환 (목록, 전체 수, 절단 여부)."""
    rc, out, err = git(repo, "log", "--format=%H%x1f%cs%x1f%s", spec)
    if rc != 0:
        raise RuntimeError(err)
    commits = []
    for line in out.splitlines():
        sha, date, subject = line.split("\x1f", 2)
        commits.append({"sha": sha, "short": sha[:7], "date": date,
                        "subject": subject})
    total = len(commits)
    if limit and total > limit:
        return commits[:limit], total, True
    return commits, total, False


def pegging_info(integ: Path, rev: str, sha: str, subpath: str) -> tuple[dict | None, str | None]:
    ftl_sha, why = gitlink_at(integ, sha, subpath)
    if ftl_sha is None:
        return None, why
    return {"rev": rev, "sha": sha, "short": sha[:7],
            "ftl_sha": ftl_sha, "ftl_short": ftl_sha[:7]}, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="integration pegging sha에서 반영된 FTL 커밋을 해석 (reset 안전).",
        epilog="예: resolve_ftl.py --repo ~/integration_ftl a3f9c21..77d0e4f "
               "(ingest_run.py stdout의 ftl_range 그대로). "
               "sha 하나만 주면 그 pegging의 FTL sha만 해석한다.")
    ap.add_argument("range_or_sha",
                    help="pegging sha 하나 또는 '직전pegging..이번pegging' 구간")
    ap.add_argument("--repo", required=True, help="integration_ftl clone 경로")
    ap.add_argument("--submodule", default="FTL",
                    help="submodule 경로 (기본: FTL)")
    ap.add_argument("--ftl-repo",
                    help="FTL repo 경로 (기본: <repo>/<submodule> — 초기화된 submodule)")
    ap.add_argument("--fetch", action="store_true",
                    help="로컬에 없는 sha를 origin에서 fetch 시도 (best effort)")
    ap.add_argument("--limit", type=int, default=100,
                    help="added/removed 커밋 목록 상한 (0=무제한, 기본 100). "
                         "초과 시 *_truncated=true, *_total은 전체 수")
    args = ap.parse_args()

    integ = Path(args.repo).resolve()
    if not is_git_repo(integ):
        return fail(f"integration repo 아님: {integ}", 3)

    if "..." in args.range_or_sha:
        return fail("'...'이 아니라 '..' 구간을 사용 (예: a3f9c21..77d0e4f)")
    if ".." in args.range_or_sha:
        prev_rev, _, cur_rev = args.range_or_sha.partition("..")
        if not prev_rev or not cur_rev:
            return fail(f"구간 형식 오류: {args.range_or_sha!r} (양쪽 sha 필요)")
    else:
        prev_rev, cur_rev = None, args.range_or_sha

    def resolve_pegging(rev: str) -> tuple[dict | None, str | None]:
        sha = resolve_commit(integ, rev, args.fetch)
        if sha is None:
            return None, (f"pegging {rev!r}를 integration repo에서 해석 불가 — "
                          "reset으로 unreachable해도 object가 남아 있으면 해석된다. "
                          "--fetch 재시도 또는 전체 sha 사용, 그래도 없으면 gc로 "
                          "유실된 것 (직전 run 시점의 clone/기록 참조)")
        return pegging_info(integ, rev, sha, args.submodule)

    cur, why = resolve_pegging(cur_rev)
    if cur is None:
        return fail(why)

    if prev_rev is None:  # 단건 해석 — FTL repo 불필요
        return emit({"ok": True, "mode": "single", "submodule": args.submodule,
                     "pegging": cur})

    prev, why = resolve_pegging(prev_rev)
    if prev is None:
        return fail(why)

    ftl = Path(args.ftl_repo).resolve() if args.ftl_repo else integ / args.submodule
    if not is_git_repo(ftl):
        return fail(f"FTL repo 아님: {ftl} — submodule 미초기화면 --ftl-repo로 지정", 3)
    # 미초기화 submodule은 빈 디렉토리라 git -C가 상위(integration) repo로 해석된다
    if git(ftl, "rev-parse", "--show-toplevel")[1] == git(integ, "rev-parse", "--show-toplevel")[1]:
        return fail(f"{ftl}는 초기화된 submodule이 아님 — "
                    "`git submodule update --init` 또는 --ftl-repo로 별도 clone 지정", 3)

    for sha in (prev["ftl_sha"], cur["ftl_sha"]):
        if resolve_commit(ftl, sha, args.fetch) is None:
            return fail(f"FTL repo에 {sha[:7]} 없음 — `git -C {ftl} fetch` 후 재시도")

    changed = prev["ftl_sha"] != cur["ftl_sha"]
    try:
        added, added_total, added_trunc = list_commits(
            ftl, f"{prev['ftl_sha']}..{cur['ftl_sha']}", args.limit)
        removed, removed_total, removed_trunc = list_commits(
            ftl, f"{cur['ftl_sha']}..{prev['ftl_sha']}", args.limit)
    except RuntimeError as e:
        return fail(f"FTL 커밋 열거 실패: {e}", 3)

    notes = []
    if not changed:
        notes.append("FTL gitlink 변동 없음 — 원인이 FTL 밖일 수 있음 (매핑하지 말 것)")
    if removed_total:
        notes.append("reset 감지 — removed는 이번 구간에서 되돌려진 FTL 커밋. "
                     "added가 비었으면 되돌림 자체가 원인일 수 있으니 수동 판단")

    return emit({
        "ok": True,
        "mode": "range",
        "submodule": args.submodule,
        "prev_pegging": prev,
        "pegging": cur,
        # False = 두 pegging 사이에 integration_ftl reset/rewrite가 있었음
        "integration_forward": is_ancestor(integ, prev["sha"], cur["sha"]),
        "ftl_changed": changed,
        "ftl_forward": is_ancestor(ftl, prev["ftl_sha"], cur["ftl_sha"]),
        "ftl_range": f"{prev['ftl_short']}..{cur['ftl_short']}",
        "added": added, "added_total": added_total, "added_truncated": added_trunc,
        "removed": removed, "removed_total": removed_total,
        "removed_truncated": removed_trunc,
        "notes": notes,
    })


if __name__ == "__main__":
    sys.exit(main())
