"""Isolated tests for the unattended AutoCode wrapper: no model calls."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_unattended as unattended


class RefusalTests(unittest.TestCase):
    def test_operator_flags_and_abbreviations_are_refused(self):
        for argv in (["--approve-goal", "r1:abc"], ["--approve-g=r1:abc"], ["--resume-paused"],
                     ["--answer", "Q1=yes"], ["--delegate-all"], ["--accept-completion"],
                     ["--retry-failed-stage"], ["--feedback", "x"], ["--chat"]):
            with self.subTest(argv=argv):
                self.assertIsNotNone(unattended.refused(["--run-dir", "r", *argv]))

    def test_operator_subcommands_are_refused(self):
        self.assertIn("intervention", unattended.refused(["intervention", "submit"]))

    def test_run_and_status_arguments_are_allowed(self):
        for argv in (["Build a CLI", "--workspace", "/w", "--engine", "gocode"],
                     ["--run-dir", "r", "--status"], ["--no-chat", "--max-iterations", "5"],
                     ["task", "--", "--approve-goal"]):
            with self.subTest(argv=argv):
                self.assertIsNone(unattended.refused(argv))


class RunTests(unittest.TestCase):
    def run_wrapper(self, argv, exit_code):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls"
            fake = Path(tmp) / "fake_autocode.py"
            fake.write_text(textwrap.dedent(f"""
                import sys
                open({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
                if "--status" in sys.argv:
                    print('{{"status": "PAUSED"}}')
                    raise SystemExit(0)
                assert sys.stdin.read() == ""
                print("Run: /w/.autocode/runs/r1")
                raise SystemExit({exit_code})
            """))
            out = io.StringIO()
            env = {"AUTOCODE_UNATTENDED_COMMAND": f"{sys.executable} {fake}"}
            with patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                rc = unattended.run(argv)
            return rc, out.getvalue(), log.read_text().splitlines() if log.exists() else []

    def test_stop_reports_status_and_tells_caller_to_stop(self):
        rc, out, calls = self.run_wrapper(["Build it", "--workspace", "/w"], 2)
        self.assertEqual(rc, 2)
        self.assertEqual(calls[0], "Build it --workspace /w --no-chat")
        self.assertEqual(calls[1], "--run-dir /w/.autocode/runs/r1 --status --workspace /w")
        self.assertIn('"status": "PAUSED"', out)
        self.assertIn("AUTOCODE STOPPED FOR THE OPERATOR (exit 2)", out)

    def test_success_has_no_stop_notice(self):
        rc, out, _ = self.run_wrapper(["Build it"], 0)
        self.assertEqual(rc, 0)
        self.assertNotIn("STOPPED", out)

    def test_refused_call_launches_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, _, calls = self.run_wrapper(["--run-dir", "r", "--resume-paused"], 0)
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("refused", err.getvalue())


if __name__ == "__main__":
    unittest.main()
