import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resolve_ftl.py"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


class GitFixture:
    def __init__(self, root: Path):
        self.root = root
        self.ftl = root / "ftl"
        self.integration = root / "integration"
        self._init_repo(self.ftl)
        self._init_repo(self.integration)

        self.f1 = self.commit(self.ftl, "ftl.txt", "one", "FTL one")
        self.f2 = self.commit(self.ftl, "ftl.txt", "two", "FTL two")

        # 별도 clone 없이 --ftl-repo로 지정하므로 integration에는 gitlink만 만든다.
        self.p1 = self.pegging(self.f1, "pegging one")
        self.p2 = self.pegging(self.f2, "pegging two")

    @staticmethod
    def _init_repo(repo: Path) -> None:
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.com")

    @staticmethod
    def commit(repo: Path, name: str, content: str, subject: str) -> str:
        (repo / name).write_text(content, encoding="utf-8")
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", subject)
        return git(repo, "rev-parse", "HEAD")

    def pegging(self, ftl_sha: str, subject: str) -> str:
        git(self.integration, "update-index", "--add", "--cacheinfo", f"160000,{ftl_sha},FTL")
        git(self.integration, "commit", "-q", "--allow-empty", "-m", subject)
        return git(self.integration, "rev-parse", "HEAD")

    def run(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.integration),
             "--ftl-repo", str(self.ftl), *args],
            capture_output=True, text=True,
        )
        return proc, json.loads(proc.stdout)


class ResolveFtlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = GitFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_single_resolves_gitlink_without_ftl_repo(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.fixture.integration),
             self.fixture.p2], capture_output=True, text=True
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(payload["mode"], "single")
        self.assertEqual(payload["pegging"]["ftl_sha"], self.fixture.f2)

    def test_forward_range_lists_added_commit(self) -> None:
        proc, payload = self.fixture.run(f"{self.fixture.p1}..{self.fixture.p2}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(payload["integration_forward"])
        self.assertTrue(payload["ftl_forward"])
        self.assertEqual([c["sha"] for c in payload["added"]], [self.fixture.f2])
        self.assertEqual(payload["removed"], [])

    def test_unchanged_gitlink_has_no_commit_difference(self) -> None:
        p3 = self.fixture.pegging(self.fixture.f2, "same FTL pegging")
        proc, payload = self.fixture.run(f"{self.fixture.p2}..{p3}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(payload["ftl_changed"])
        self.assertEqual(payload["added"], [])
        self.assertEqual(payload["removed"], [])
        self.assertTrue(any("FTL gitlink 변동 없음" in note for note in payload["notes"]))

    def test_non_forward_range_is_not_called_a_definite_reset(self) -> None:
        git(self.fixture.ftl, "reset", "--hard", "-q", self.fixture.f1)
        f3 = self.fixture.commit(self.fixture.ftl, "other.txt", "three", "FTL three")
        git(self.fixture.integration, "reset", "--hard", "-q", self.fixture.p1)
        p3 = self.fixture.pegging(f3, "pegging three")

        proc, payload = self.fixture.run(f"{self.fixture.p2}..{p3}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(payload["integration_forward"])
        self.assertFalse(payload["ftl_forward"])
        self.assertEqual([c["sha"] for c in payload["added"]], [f3])
        self.assertEqual([c["sha"] for c in payload["removed"]], [self.fixture.f2])
        self.assertIn("비전진 변경", payload["notes"][0])
        self.assertNotIn("reset 감지", payload["notes"][0])

    def test_pruned_unreachable_pegging_returns_resolution_error(self) -> None:
        git(self.fixture.integration, "reset", "--hard", "-q", self.fixture.p1)
        self.fixture.pegging(self.fixture.f1, "replacement pegging")
        git(self.fixture.integration, "reflog", "expire", "--expire=now", "--all")
        git(self.fixture.integration, "gc", "--prune=now", "--quiet")

        proc, payload = self.fixture.run(f"{self.fixture.p2}..HEAD")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("해석 불가", payload["error"])

    def test_inspect_reset_resolves_origin_head_and_detects_rewrite(self) -> None:
        git(self.fixture.integration, "config", "core.logAllRefUpdates", "always")
        git(self.fixture.integration, "update-ref", "refs/remotes/origin/develop", self.fixture.p2)
        git(self.fixture.integration, "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/develop")

        git(self.fixture.ftl, "reset", "--hard", "-q", self.fixture.f1)
        f3 = self.fixture.commit(self.fixture.ftl, "other.txt", "three", "FTL three")
        git(self.fixture.integration, "reset", "--hard", "-q", self.fixture.p1)
        p3 = self.fixture.pegging(f3, "pegging three")
        git(self.fixture.integration, "update-ref", "refs/remotes/origin/develop", p3)

        proc, payload = self.fixture.run("--inspect-reset")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["branch"], "origin/develop")
        self.assertEqual(payload["reset_status"], "detected")
        self.assertTrue(payload["reset_detected"])
        self.assertEqual(payload["old_tip"]["sha"], self.fixture.p2)
        self.assertEqual(payload["tip"]["sha"], p3)

    def test_inspect_reset_without_history_is_unknown(self) -> None:
        git(self.fixture.integration, "config", "core.logAllRefUpdates", "always")
        git(self.fixture.integration, "update-ref", "refs/remotes/origin/develop",
            self.fixture.p2)
        proc, payload = self.fixture.run(
            "--inspect-reset", "--branch", "origin/develop"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["reset_status"], "unknown")
        self.assertIsNone(payload["reset_detected"])
        self.assertIsNone(payload["old_tip"])

    def test_explicit_old_reports_normal_forward(self) -> None:
        proc, payload = self.fixture.run(
            "--inspect-reset", "--branch", "main", "--old", self.fixture.p1
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["reset_status"], "not_detected")
        self.assertFalse(payload["reset_detected"])
        self.assertEqual(payload["old_tip"]["source"], "--old")

    def test_uninitialized_submodule_returns_repo_error(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.fixture.integration),
             f"{self.fixture.p1}..{self.fixture.p2}"],
            capture_output=True, text=True,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 3)
        self.assertFalse(payload["ok"])
        self.assertIn("submodule", payload["error"])

    def test_limit_reports_total_and_truncation(self) -> None:
        f3 = self.fixture.commit(self.fixture.ftl, "three.txt", "three", "FTL three")
        p3 = self.fixture.pegging(f3, "pegging three")
        proc, payload = self.fixture.run("--limit", "1", f"{self.fixture.p1}..{p3}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["added_total"], 2)
        self.assertEqual(len(payload["added"]), 1)
        self.assertTrue(payload["added_truncated"])


if __name__ == "__main__":
    unittest.main()
