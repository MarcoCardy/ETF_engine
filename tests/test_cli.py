import json
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase


PYTHON = Path(".venv/Scripts/python.exe")


class CliTests(TestCase):
    def test_vi_shadow_run_fails_closed_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    str(PYTHON),
                    "-m",
                    "perpetual_engine",
                    "evaluate",
                    "--config",
                    "config/policy_v1.json",
                    "--snapshot",
                    "data/fixtures/vi_2026-08-19.json",
                    "--out-dir",
                    directory,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = Path(directory) / "result.json"
            summary_path = Path(directory) / "summary.csv"
            self.assertTrue(result_path.exists())
            self.assertTrue(summary_path.exists())
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["policy"]["state"], "DATA_INCOMPLETE")
            self.assertEqual(result["policy"]["target_gross_real"], "0.00")
            self.assertEqual(result["funding"]["delivered_net_real"], "0.00")
            self.assertEqual(len(result["input_hashes"]["config"]), 64)
            self.assertEqual(result["funding"]["trades"], [])

    def test_malformed_input_returns_two_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"
            bad.write_text("{not-json", encoding="utf-8")
            output = Path(directory) / "out"
            completed = subprocess.run(
                [
                    str(PYTHON),
                    "-m",
                    "perpetual_engine",
                    "evaluate",
                    "--config",
                    "config/policy_v1.json",
                    "--snapshot",
                    str(bad),
                    "--out-dir",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("bad.json", completed.stderr)
            self.assertNotIn("{not-json", completed.stderr)
            self.assertFalse((output / "result.json").exists())
