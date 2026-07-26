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

    def test_argparse_failures_use_json_error_contract(self) -> None:
        for script in ("status.py", "build_rollup.py", "render_reviews.py",
                       "ingest_run.py", "apply_mapping.py", "resolve_ftl.py"):
            with self.subTest(script=script):
                proc, payload = self.run_script(script, "--unknown-secret-option")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(payload["error_code"], "ARGUMENT_PARSING_FAILED")
                self.assertIn("error", payload)
                self.assertNotIn("unknown-secret-option", proc.stdout)

    def test_mapping_validation_has_summary_and_detail_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mapping = root / "mapping.json"
            mapping.write_text(json.dumps([{
                "config": "unknown-config", "tc": "TC_X",
                "suspect_sha": "abcdef0", "confidence": "high",
            }]), encoding="utf-8")
            proc, payload = self.run_script(
                "apply_mapping.py", "--root", str(ROOT), "--date", "2026-07-26",
                "--mapping", str(mapping),
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(payload["error_code"], "MAPPING_VALIDATION_FAILED")
            self.assertIn("error", payload)
            self.assertTrue(payload["errors"])


if __name__ == "__main__":
    unittest.main()
