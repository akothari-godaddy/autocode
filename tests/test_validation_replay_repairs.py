"""Honest mixed outcomes replay in real clean copies; claimed success stays fail-closed."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import autocode_check_replay as replay
import autocode_verify as verify


class MixedReplayRepairsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name) / "project"
        self.workspace.mkdir()
        self.run_dir = Path(temporary.name) / "run"
        (self.workspace / "app.py").write_text("def a(): return 2\ndef b(): return 0\n")
        for name, function, expected in (("a", "a", 2), ("b", "b", 4)):
            (self.workspace / f"test_{name}.py").write_text(
                "import unittest\nimport app\nclass Check(unittest.TestCase):\n"
                f"    def test_behavior(self): self.assertEqual(app.{function}(), {expected})\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.test", "commit", "-qm", "behavioral fixture"],
                       check=True, capture_output=True)
        self.record = {"output": "sol.json", "source_revision": "current-source"}
        self.checks = [{"command": "python3 -m unittest test_a", "exit_code": 0, "evidence_ref": "event:a"},
                       {"command": "python3 -m unittest test_b", "exit_code": 1, "evidence_ref": "event:b"}]

    def run_checks(self, **options):
        return replay.replay(self.checks, self.workspace, self.run_dir, self.record,
                             verify.scratch_run, timeout=30, **options)

    def test_honest_mixed_failure_retains_both_actual_executions(self):
        result = self.run_checks(allow_reported_failures=True)
        self.assertEqual("FAIL", result["verdict"])
        self.assertEqual([0, 1], [row["exit_code"] for row in result["checks"]])
        self.assertEqual([0, 1], [row["reported_exit_code"] for row in result["checks"]])
        for row in result["checks"]:
            self.assertTrue(Path(row["output"]).is_file())
            self.assertTrue(row["output_sha256"])
        saved = json.loads((self.run_dir / "check-replay" / "sol" / "replay.json").read_text())
        self.assertEqual("FAIL", saved["verdict"])

    def test_default_pass_checkpoint_still_rejects_actual_failure(self):
        with self.assertRaisesRegex(ValueError, "test_b.*exited 1"):
            self.run_checks()

    def test_failure_mode_never_accepts_a_fabricated_successful_check(self):
        self.checks[1]["exit_code"] = 0
        with self.assertRaisesRegex(ValueError, "reported as exit 0.*exited 1"):
            self.run_checks(allow_reported_failures=True)

    def test_nonmatching_failure_code_is_not_rewritten_into_an_honest_failure(self):
        self.checks[1]["exit_code"] = 2
        with self.assertRaisesRegex(ValueError, "reported as exit 2.*exited 1"):
            self.run_checks(allow_reported_failures=True)
