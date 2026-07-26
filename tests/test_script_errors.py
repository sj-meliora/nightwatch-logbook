import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScriptErrorContractTests(unittest.TestCase):
    def run_script(self, script: str, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *args],
            capture_output=True, text=True,
        )
        return proc, json.loads(proc.stdout)

    def test_all_cli_failure_payloads_include_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = [
                ("status.py", "--root", str(root), "--date", "invalid"),
                ("build_rollup.py", "--root", str(root), "--date", "2026-07-26"),
                ("render_reviews.py", "--root", str(root), "--date", "2026-07-26"),
                ("ingest_run.py", "--root", str(root), "--config", "cfg-a",
                 "--date", "invalid", "--facts", str(root / "facts.json")),
                ("apply_mapping.py", "--root", str(root), "--date", "2026-07-26",
                 "--mapping", str(root / "mapping.json")),
                ("resolve_ftl.py", "--repo", str(root), "HEAD"),
            ]
            for script, *args in cases:
                with self.subTest(script=script):
                    proc, payload = self.run_script(script, *args)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertFalse(payload["ok"])
                    self.assertIsInstance(payload["error_code"], str)
                    self.assertTrue(payload["error_code"])


if __name__ == "__main__":
    unittest.main()
