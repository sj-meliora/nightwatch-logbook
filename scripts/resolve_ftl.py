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

모드 세 가지:

- 단건: `resolve_ftl.py --repo R <sha>` — 그 pegging의 FTL sha만 해석
- 구간: `resolve_ftl.py --repo R <직전..이번>` — ingest_run.py stdout의
  `ftl_range`를 그대로 넘기는 데일리 흐름의 표준 사용법. added가 1개면
  사실상 확정(high). **added가 비어 있어도 곧바로 "원인이 FTL 밖"으로
  단정하지 말 것** — 신규 fail은 FTL이 아니라 같은 구간에 함께 pegging된
  다른 submodule(FIL·HOME 등) 변경이 원인인 경우가 있다. 이 구간에서
  FTL 외 submodule gitlink가 움직였는지는 `companions[]`에 실제 커밋
  목록까지 담겨 나온다 — 초기화된 submodule이 없으면 `--sub-repo
  PATH=DIR`로 로컬 clone을 지정한다 (removed만 있으면 되돌림 자체가
  원인일 수 있으니 수동 판단)
- reset 진단: `resolve_ftl.py --repo R --inspect-reset` — reset 전후로
  integration/FTL tip이 각각 무엇이었고 어떻게 바뀌었는지 best effort로
  재구성한다. reset 이전 tip은 ① 이 clone의 remote-tracking reflog
  (reset 이전에 fetch한 적이 있어야 함) ② `--old <sha>` 직접 지정
  (예: 로그북 run 시퀀스의 마지막 pegging) 순으로 찾고, 못 찾으면
  reset_status=unknown과 다음 시도를 notes로 안내한다. reset_status는
  detected/not_detected/unknown 세 값이며 unknown일 때 reset_detected는 null이다

회사 AI 정책에 따라 출력에 author 등 개발자 식별 정보는 싣지 않는다
(sha·날짜·제목만).

exit code: 0=성공 (진단 모드에서 '판별 불가'도 성공 — notes 참조) /
2=인자·해석 오류 (--fetch 등으로 재시도 가능) / 3=repo 접근 오류
"""

from dataclasses import dataclass
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook

NULL_SHA_RE = re.compile(r"^0+$")


def emit(payload: dict, code: int = 0) -> int:
    json.dump({"schema_version": logbook.SCHEMA_VERSION, **payload},
              sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


def fail(error_code: str, msg: str, code: int = 2, **extra: object) -> int:
    return emit({"ok": False, "error_code": error_code, "error": msg, **extra}, code)


@dataclass
class FetchReport:
    """`--fetch`의 실제 수행 여부와 결과를 stdout용으로 누적한다."""

    requested: bool
    attempted: bool = False
    status: str = "not_requested"
    attempts: int = 0
    successes: int = 0

    def __post_init__(self) -> None:
        if self.requested:
            self.status = "skipped"

    def found_locally(self) -> None:
        if self.requested and not self.attempted:
            self.status = "not_needed"

    def record(self, succeeded: bool) -> None:
        self.attempted = True
        self.attempts += 1
        self.successes += int(succeeded)
        if self.successes == self.attempts:
            self.status = "succeeded"
        elif self.successes == 0:
            self.status = "failed"
        else:
            self.status = "partially_succeeded"

    def payload(self) -> dict:
        return {"requested": self.requested, "attempted": self.attempted,
                "status": self.status}


@dataclass(frozen=True)
class ResolutionError:
    error_code: str
    message: str
    exit_code: int


def git(repo: Path, *args: str) -> tuple[int, str, str]:
    # Git의 log 출력은 기본적으로 UTF-8이지만 text=True만 지정하면 Python이
    # Windows 콘솔 코드 페이지(cp949 등)를 사용해 decode한다. 커밋 제목에
    # 한글이 포함된 경우 여기서 UnicodeDecodeError가 날 수 있으므로 Git 출력
    # 인코딩을 명시한다. 손상되었거나 선언이 잘못된 과거 커밋도 진단 전체를
    # 중단시키지 않도록 디코딩 불가 바이트는 대체 문자로 보존한다.
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def is_git_repo(repo: Path) -> bool:
    return git(repo, "rev-parse", "--git-dir")[0] == 0


def resolve_commit(repo: Path, rev: str, fetch: FetchReport) -> str | None:
    """rev → 전체 commit sha. unreachable이어도 object만 있으면 된다."""
    rc, out, _ = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    if rc == 0:
        fetch.found_locally()
        return out
    if fetch.requested:
        # reset으로 사라진 sha는 서버 설정에 따라 fetch가 거부될 수 있다 — best effort
        fetch_rc, _, _ = git(repo, "fetch", "--quiet", "origin", rev)
        rc, out, _ = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
        succeeded = fetch_rc == 0 and rc == 0
        fetch.record(succeeded)
        if succeeded:
            return out
    return None


def gitlink_at(repo: Path, commit: str, subpath: str) -> tuple[str | None, str | None]:
    """commit 트리의 submodule gitlink sha. 반환 (sha, 오류사유)."""
    rc, out, _ = git(repo, "ls-tree", commit, "--", subpath)
    if rc != 0:
        return None, f"ls-tree 실패: {commit[:7]}"
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
        # git stderr에는 remote URL, credential helper 출력 등 비공개 정보가
        # 섞일 수 있으므로 호출자에게 전달하지 않는다.
        raise RuntimeError("git log failed")
    commits = []
    for line in out.splitlines():
        sha, date, subject = line.split("\x1f", 2)
        commits.append({"sha": sha, "short": sha[:7], "date": date,
                        "subject": subject})
    total = len(commits)
    if limit and total > limit:
        return commits[:limit], total, True
    return commits, total, False


def reflog_shas(repo: Path, ref: str) -> list[str]:
    """reflog가 거쳐간 sha 목록 (최신순, 중복 제거).

    `reflog show`는 각 entry의 new oid만 보여주므로 old oid는 ref@{n}으로
    추가 수집한다 — fetch 한 번만 기록된 clone이어도 @{1}이 reset 직전
    tip을 돌려준다.
    """
    rc, out, _ = git(repo, "reflog", "show", "--format=%H", ref)
    if rc != 0 or not out:
        return []
    shas = out.splitlines()
    for i in range(1, len(shas) + 1):
        rc, old, _ = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}@{{{i}}}")
        if rc == 0:
            shas.append(old)
    seen: set[str] = set()
    return [s for s in shas if not (s in seen or seen.add(s))]


def tracking_branch(repo: Path, branch: str) -> str:
    """origin/HEAD 같은 remote symref를 실제 추적 브랜치명으로 변환한다."""
    ref = branch if branch.startswith("refs/") else f"refs/remotes/{branch}"
    rc, out, _ = git(repo, "symbolic-ref", "--quiet", "--short", ref)
    return out if rc == 0 and out else branch


def pegging_info(integ: Path, rev: str, sha: str, subpath: str) -> tuple[dict | None, str | None]:
    ftl_sha, why = gitlink_at(integ, sha, subpath)
    if ftl_sha is None:
        return None, why
    return {"rev": rev, "sha": sha, "short": sha[:7],
            "ftl_sha": ftl_sha, "ftl_short": ftl_sha[:7]}, None


def ftl_repo_path(args, integ: Path) -> tuple[Path | None, str | None]:
    ftl = Path(args.ftl_repo).resolve() if args.ftl_repo else integ / args.submodule
    if not is_git_repo(ftl):
        return None, "FTL repo 아님 — submodule 미초기화면 --ftl-repo로 지정"
    # 미초기화 submodule은 빈 디렉토리라 git -C가 상위(integration) repo로 해석된다
    if git(ftl, "rev-parse", "--show-toplevel")[1] == git(integ, "rev-parse", "--show-toplevel")[1]:
        return None, ("기본 FTL 경로는 초기화된 submodule이 아님 — "
                      "`git submodule update --init` 또는 --ftl-repo로 별도 clone 지정")
    return ftl, None


def ftl_diff(ftl: Path, prev_sha: str, cur_sha: str, fetch: FetchReport,
             limit: int) -> tuple[dict | None, ResolutionError | None]:
    """FTL repo에서 gitlink 전후의 커밋 차집합. 반환 (결과, 오류사유)."""
    for sha in (prev_sha, cur_sha):
        if resolve_commit(ftl, sha, fetch) is None:
            return None, ResolutionError(
                "FTL_REVISION_NOT_FOUND",
                f"FTL repo에 {sha[:7]} 없음 — --fetch로 재시도", 2)
    try:
        added, added_total, added_trunc = list_commits(
            ftl, f"{prev_sha}..{cur_sha}", limit)
        removed, removed_total, removed_trunc = list_commits(
            ftl, f"{cur_sha}..{prev_sha}", limit)
    except RuntimeError:
        return None, ResolutionError(
            "FTL_HISTORY_READ_FAILED", "FTL 커밋 열거 실패", 3)
    return {
        "ftl_forward": is_ancestor(ftl, prev_sha, cur_sha),
        "added": added, "added_total": added_total, "added_truncated": added_trunc,
        "removed": removed, "removed_total": removed_total,
        "removed_truncated": removed_trunc,
    }, None


def companion_repo(integ: Path, sub_repos: dict[str, Path], path: str) -> Path | None:
    """path의 로컬 clone. --sub-repo 지정 우선, 없으면 초기화된 submodule."""
    if path in sub_repos:
        repo = sub_repos[path]
        return repo if is_git_repo(repo) else None
    cand = integ / path
    if not is_git_repo(cand):
        return None
    # 미초기화 submodule은 빈 디렉토리라 git -C가 상위(integration) repo로 해석된다
    if git(cand, "rev-parse", "--show-toplevel")[1] == git(integ, "rev-parse", "--show-toplevel")[1]:
        return None
    return cand


def companions_in_range(integ: Path, prev_sha: str, cur_sha: str, primary: str,
                         sub_repos: dict[str, Path], fetch: FetchReport,
                         limit: int) -> tuple[list[dict], list[str]]:
    """prev_sha..cur_sha 구간에서 primary(FTL) 외 submodule gitlink 변경을 열거한다.

    dobee run은 pegging 1개 단위지만, 이 구간(직전 run..이번 run)에는 dobee가
    돌지 않은 pegging이 여러 개 끼어 있을 수 있다. 그래서 first-parent diff가
    아니라 두 트리(prev_sha, cur_sha) 자체를 통째로 비교한다 — 구간 안에서
    몇 번을 움직였든 최종 gitlink 전후 값만 있으면 된다.
    """
    rc, out, _ = git(integ, "diff-tree", "--no-renames", "-r", "--raw", "--full-index",
                     prev_sha, cur_sha)
    if rc != 0:
        return [], ["companion 변경점 조회 실패 — integration diff 읽기 오류"]
    notes: list[str] = []
    result: list[dict] = []
    for line in out.splitlines():
        if not line.startswith(":"):
            continue
        front, _, path = line.partition("\t")
        old_mode, new_mode, old_sha, new_sha = front[1:].split()[:4]
        if "160000" not in (old_mode, new_mode) or path == primary:
            continue
        entry: dict = {"path": path,
                       "from": None if NULL_SHA_RE.match(old_sha) else old_sha,
                       "to": None if NULL_SHA_RE.match(new_sha) else new_sha,
                       "commits": None, "commits_total": None,
                       "commits_truncated": None, "removed_total": None,
                       "repo_available": False}
        repo = companion_repo(integ, sub_repos, path)
        if repo is None:
            notes.append(f"{path!r} repo 접근 불가 — gitlink 전후 sha만 보고 "
                         "(--sub-repo PATH=DIR로 지정)")
        elif entry["from"] is None or entry["to"] is None:
            notes.append(f"{path!r} submodule이 이 구간에서 추가/제거됨 — 커밋 목록 생략")
        elif any(resolve_commit(repo, sha, fetch) is None
                for sha in (entry["from"], entry["to"])):
            notes.append(f"{path!r} repo에 gitlink 대상 커밋 없음 — --fetch로 재시도")
        else:
            try:
                added, total, trunc = list_commits(
                    repo, f"{entry['from']}..{entry['to']}", limit)
                _, removed_total, _ = list_commits(
                    repo, f"{entry['to']}..{entry['from']}", limit)
            except RuntimeError:
                notes.append(f"{path!r} 커밋 열거 실패")
            else:
                entry.update({"commits": added, "commits_total": total,
                              "commits_truncated": trunc, "removed_total": removed_total,
                              "repo_available": True})
                if removed_total:
                    notes.append(f"{path!r} gitlink 비전진 이동 — removed "
                                 f"{removed_total}건은 되돌려진 커밋")
        result.append(entry)
    return result, notes


# ---------------------------------------------------------------- 모드별 실행

def run_range(args, integ: Path, prev: dict, cur: dict) -> int:
    ftl, why = ftl_repo_path(args, integ)
    if ftl is None:
        return fail("FTL_REPOSITORY_INVALID", why, 3,
                    fetch=args.fetch_report.payload())
    diff, problem = ftl_diff(ftl, prev["ftl_sha"], cur["ftl_sha"],
                             args.fetch_report, args.limit)
    if diff is None:
        return fail(problem.error_code, problem.message, problem.exit_code,
                    fetch=args.fetch_report.payload())

    changed = prev["ftl_sha"] != cur["ftl_sha"]
    companions, companion_notes = companions_in_range(
        integ, prev["sha"], cur["sha"], args.submodule, args.sub_repos,
        args.fetch_report, args.limit)
    has_companion_commits = any(c["commits_total"] for c in companions)

    notes = []
    if not changed and not has_companion_commits:
        notes.append("FTL gitlink 변동 없음 — 이 구간의 다른 submodule도 변동 없음 "
                     "(원인이 이 구간 밖일 수 있음, 매핑하지 말 것)")
    elif not changed:
        notes.append("FTL gitlink 변동 없음 — companions[]에 이 구간에서 변경된 "
                     "다른 layer가 있다. FTL 밖으로 단정하지 말고 그쪽 커밋을 먼저 검토할 것")
    notes.extend(companion_notes)
    if diff["removed_total"]:
        notes.append("FTL gitlink 간 비전진 변경 감지 — removed는 현재 tip에서 "
                     "도달할 수 없어진 커밋. reset·rebase·branch 전환 여부는 별도 "
                     "판단하고, added가 비었으면 되돌림 자체도 원인 후보로 검토")

    return emit({
        "ok": True,
        "mode": "range",
        "submodule": args.submodule,
        "prev_pegging": prev,
        "pegging": cur,
        # False = 두 pegging 사이에 integration_ftl reset/rewrite가 있었음
        "integration_forward": is_ancestor(integ, prev["sha"], cur["sha"]),
        "ftl_changed": changed,
        "ftl_range": f"{prev['ftl_short']}..{cur['ftl_short']}",
        **diff,
        "companions": companions,
        "notes": notes,
        "fetch": args.fetch_report.payload(),
    })


def run_inspect(args, integ: Path) -> int:
    # origin/HEAD 같은 symref는 자체 reflog가 없으므로 실제 추적 브랜치로 변환
    branch = tracking_branch(integ, args.branch)

    tip_sha = resolve_commit(integ, branch, FetchReport(False))
    if tip_sha is None:
        return fail("BRANCH_NOT_FOUND",
                    f"{branch!r} 해석 불가 — --branch로 추적 브랜치 지정 (예: origin/develop)",
                    fetch=args.fetch_report.payload())
    tip, why = pegging_info(integ, branch, tip_sha, args.submodule)
    if tip is None:
        return fail("GITLINK_READ_FAILED", why, fetch=args.fetch_report.payload())

    entries = reflog_shas(integ, branch)
    old_sha = source = None
    if args.old:
        old_sha = resolve_commit(integ, args.old, args.fetch_report)
        if old_sha is None:
            return fail("PEGGING_REVISION_NOT_FOUND",
                        f"--old {args.old!r} 해석 불가 — object가 로컬에도 서버에도 없으면 "
                        "이 sha 기준 복원은 불가. reset 이전에 fetch한 장기 clone"
                        "(보존 미러)에서 실행하거나, 로그북 run 시퀀스의 다른 pegging "
                        "sha로 재시도", fetch=args.fetch_report.payload())
        source = "--old"
    else:
        for sha in entries:
            if sha == tip_sha or resolve_commit(integ, sha, FetchReport(False)) is None:
                continue  # 현재 tip이거나, reflog에만 남고 object는 gc로 유실
            if is_ancestor(integ, sha, tip_sha):
                continue  # 정상 전진 이력 — reset 아님
            old_sha, source = sha, f"reflog({branch})"
            break

    if old_sha is None:
        if len(entries) <= 1:
            notes = ["reset 이전 tip을 찾지 못함 — 이 clone의 reflog에 이전 fetch "
                     "기록이 없다 (fresh clone이면 정상, 판별 불가). 다음을 시도: "
                     "(1) 로그북 run 시퀀스의 마지막 pegging sha를 --old로 지정 "
                     "(2) reset 이전에 fetch한 장기 clone(보존 미러)에서 실행 "
                     "(3) 서버가 unreachable sha fetch를 허용하면 --old <full sha> --fetch"]
        else:
            notes = [f"reflog {len(entries)}건이 모두 현재 tip의 ancestor — "
                     "이 clone이 관측한 범위에서는 reset 없음"]
        status = "unknown" if len(entries) <= 1 else "not_detected"
        return emit({"ok": True, "mode": "reset", "branch": branch,
                     "submodule": args.submodule, "tip": tip, "old_tip": None,
                     "reset_status": status,
                     "reset_detected": None if status == "unknown" else False,
                     "fetch": args.fetch_report.payload(),
                     "notes": notes})

    old, why = pegging_info(integ, source, old_sha, args.submodule)
    if old is None:
        return fail("GITLINK_READ_FAILED", why, fetch=args.fetch_report.payload())

    try:  # integration 전후 — removed가 reset으로 사라진 pegging들이다
        added_i, added_it, added_itr = list_commits(
            integ, f"{old_sha}..{tip_sha}", args.limit)
        removed_i, removed_it, removed_itr = list_commits(
            integ, f"{tip_sha}..{old_sha}", args.limit)
    except RuntimeError:
        return fail("INTEGRATION_HISTORY_READ_FAILED",
                    "integration 커밋 열거 실패", 3,
                    fetch=args.fetch_report.payload())
    for c in added_i + removed_i:  # 각 커밋이 물고 있던 FTL sha를 함께 표시
        sha, _ = gitlink_at(integ, c["sha"], args.submodule)
        c["ftl_short"] = sha[:7] if sha else None

    notes = []
    if not removed_it:
        notes.append(f"{source}의 tip이 현재 tip의 ancestor — reset이 아니라 정상 전진 구간")

    ftl_payload = None  # FTL 커밋 목록은 best effort — 없어도 전후 sha는 보여준다
    ftl, why = ftl_repo_path(args, integ)
    if ftl is None:
        notes.append(f"FTL 커밋 목록 생략 — {why}")
    else:
        ftl_payload, why = ftl_diff(ftl, old["ftl_sha"], tip["ftl_sha"],
                                    args.fetch_report, args.limit)
        if ftl_payload is None:
            notes.append(f"FTL 커밋 목록 생략 — {why.message}")

    return emit({
        "ok": True,
        "mode": "reset",
        "branch": branch,
        "submodule": args.submodule,
        "old_tip": {**old, "source": source},   # (1) reset 이전 integration/FTL sha
        "tip": tip,                             # (2) 현재(이후) integration/FTL sha
        "reset_status": "detected" if removed_it else "not_detected",
        "reset_detected": bool(removed_it),
        "integration": {
            "removed": removed_i, "removed_total": removed_it,
            "removed_truncated": removed_itr,
            "added": added_i, "added_total": added_it, "added_truncated": added_itr,
        },
        "ftl": ftl_payload and {
            "before": old["ftl_short"], "after": tip["ftl_short"], **ftl_payload},
        "fetch": args.fetch_report.payload(),
        "notes": notes,
    })


def main() -> int:
    ap = logbook.JsonArgumentParser(
        description="integration pegging sha에서 반영된 FTL 커밋 및 동반 submodule "
                    "변경점(companions)을 해석 (reset 안전).",
        epilog="예: resolve_ftl.py --repo ~/integration_ftl a3f9c21..77d0e4f "
               "(ingest_run.py stdout의 ftl_range 그대로). sha 하나만 주면 그 "
               "pegging의 FTL sha만 해석한다. 구간 모드는 FTL 외에 이 구간에서 "
               "함께 pegging된 다른 submodule(FIL·HOME 등)도 companions[]로 "
               "보고한다 — --sub-repo FIL=~/work/FIL처럼 로컬 clone을 지정하면 "
               "커밋 목록까지 펼친다. --inspect-reset은 reset 전후의 "
               "integration/FTL tip 변화를 reflog 기반 best effort로 재구성한다.")
    ap.add_argument("range_or_sha", nargs="?",
                    help="pegging sha 하나 또는 '직전pegging..이번pegging' 구간")
    ap.add_argument("--repo", required=True, help="integration_ftl clone 경로")
    ap.add_argument("--submodule", default="FTL",
                    help="submodule 경로 (기본: FTL)")
    ap.add_argument("--ftl-repo",
                    help="FTL repo 경로 (기본: <repo>/<submodule> — 초기화된 submodule)")
    ap.add_argument("--sub-repo", action="append", default=[], metavar="PATH=DIR",
                    help="구간 모드에서 FTL 외 submodule의 동반 변경(companions)을 "
                         "펼쳐볼 로컬 clone 경로 (예: FIL=~/work/FIL). PATH는 "
                         "integration tree 안의 경로, DIR은 로컬 clone. 미지정 시 "
                         "<repo>/<PATH>의 초기화된 submodule을 시도하고, 그마저 없으면 "
                         "gitlink 전후 sha만 보고한다. 필요한 만큼 반복 지정")
    ap.add_argument("--fetch", action="store_true",
                    help="로컬에 없는 sha를 origin에서 fetch 시도 (best effort)")
    ap.add_argument("--limit", type=int, default=100,
                    help="커밋 목록 상한 (0=무제한, 기본 100). "
                         "초과 시 *_truncated=true, *_total은 전체 수")
    ap.add_argument("--inspect-reset", action="store_true",
                    help="reset 전후 tip 진단 모드 (구간 인자 대신 사용)")
    ap.add_argument("--branch", default="origin/HEAD",
                    help="--inspect-reset가 검사할 추적 브랜치 (기본: origin/HEAD)")
    ap.add_argument("--old",
                    help="--inspect-reset에서 reset 이전 tip으로 쓸 sha — reflog에 "
                         "기록이 없을 때 (예: 로그북 run 시퀀스의 마지막 pegging)")
    args = ap.parse_args()
    args.fetch_report = FetchReport(args.fetch)

    args.sub_repos = {}
    for spec in args.sub_repo:
        path, sep, repo = spec.partition("=")
        if not sep or not path or not repo:
            return fail("INVALID_ARGUMENT", f"--sub-repo 형식 오류: {spec!r} (PATH=DIR)",
                        fetch=args.fetch_report.payload())
        args.sub_repos[path] = Path(repo).resolve()

    integ = Path(args.repo).resolve()
    if not is_git_repo(integ):
        return fail("INTEGRATION_REPOSITORY_INVALID", "integration repo 아님", 3,
                    fetch=args.fetch_report.payload())

    if args.inspect_reset:
        if args.range_or_sha:
            return fail("INVALID_ARGUMENT",
                        "--inspect-reset은 구간/sha 인자와 함께 쓸 수 없음",
                        fetch=args.fetch_report.payload())
        return run_inspect(args, integ)
    if not args.range_or_sha:
        return fail("INVALID_ARGUMENT",
                    "pegging sha 또는 '직전..이번' 구간 필요 (--inspect-reset 아니면 필수)",
                    fetch=args.fetch_report.payload())

    if "..." in args.range_or_sha:
        return fail("INVALID_RANGE",
                    "'...'이 아니라 '..' 구간을 사용 (예: a3f9c21..77d0e4f)",
                    fetch=args.fetch_report.payload())
    if ".." in args.range_or_sha:
        prev_rev, _, cur_rev = args.range_or_sha.partition("..")
        if not prev_rev or not cur_rev:
            return fail("INVALID_RANGE",
                        f"구간 형식 오류: {args.range_or_sha!r} (양쪽 sha 필요)",
                        fetch=args.fetch_report.payload())
    else:
        prev_rev, cur_rev = None, args.range_or_sha

    def resolve_pegging(rev: str) -> tuple[dict | None, str | None]:
        sha = resolve_commit(integ, rev, args.fetch_report)
        if sha is None:
            return None, (f"pegging {rev!r}를 integration repo에서 해석 불가 — "
                          "reset으로 unreachable해도 object가 남아 있으면 해석된다. "
                          "--fetch 재시도 또는 전체 sha 사용, 그래도 없으면 gc로 "
                          "유실된 것 (직전 run 시점의 clone/기록 참조)")
        return pegging_info(integ, rev, sha, args.submodule)

    cur, why = resolve_pegging(cur_rev)
    if cur is None:
        return fail("PEGGING_REVISION_NOT_FOUND", why,
                    fetch=args.fetch_report.payload())

    if prev_rev is None:  # 단건 해석 — FTL repo 불필요
        return emit({"ok": True, "mode": "single", "submodule": args.submodule,
                     "pegging": cur, "fetch": args.fetch_report.payload()})

    prev, why = resolve_pegging(prev_rev)
    if prev is None:
        return fail("PEGGING_REVISION_NOT_FOUND", why,
                    fetch=args.fetch_report.payload())
    return run_range(args, integ, prev, cur)


if __name__ == "__main__":
    sys.exit(main())
