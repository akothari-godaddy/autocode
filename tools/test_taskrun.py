"""The task-run interface: the status view and the CLI client. See docs/task-run.md."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from . import autocode_run_view as run_view
from . import autocode_taskrun as taskrun

HERE = Path(__file__).resolve().parent
# The offline fixture provider plans and builds exactly this greeting task.
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
         "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
         "Python standard library only.")
FIXTURE_OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")


class RunViewTests(unittest.TestCase):
    def test_contract_fields(self):
        self.assertEqual({"schema", "status", "done", "needs", "phase", "next_stage", "iteration", "stop_reason",
                          "current_task"}, set(run_view.view({"status": "RUNNING"})))

    def test_complete_needs_nothing(self):
        view = run_view.view({"status": "TASK_COMPLETE"})
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])

    def test_human_review_comes_before_other_questions(self):
        state = {"status": "WAITING_FOR_USER", "pending_questions": [
            {"id": "d-1", "question": "Accept C2?", "review_criteria": ["C2"], "review_token": "r-9"}]}
        self.assertEqual({"kind": "review", "criteria": ["C2"], "token": "r-9", "question": "Accept C2?"},
                         run_view.needs(state))

    def test_questions_carry_their_defaults(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "permission"},
                 "pending_questions": [{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"],
                                        "proposed_default": "no", "internal": "x"}]}
        need = run_view.needs(state)
        self.assertEqual(("answer", "permission"), (need["kind"], need["request_kind"]))
        self.assertEqual([{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"],
                           "proposed_default": "no"}], need["questions"])

    def test_plan_approval_needs_the_displayed_token(self):
        self.assertEqual({"kind": "approve_plan", "token": "g-1"},
                         run_view.needs({"status": "AWAITING_GOAL_APPROVAL", "displayed_goal": "g-1"}))
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "AWAITING_GOAL_APPROVAL"}))

    def test_pauses(self):
        self.assertEqual({"kind": "planning_budget", "reason": "two calls"},
                         run_view.needs({"status": "PAUSED_PLANNING_BUDGET", "stop_reason": "two calls"}))
        self.assertEqual({"kind": "resume", "reason": "quota"},
                         run_view.needs({"status": "PAUSED_BUDGET", "stop_reason": "quota"}))
        self.assertEqual("resume", run_view.needs({"status": "PLAN_REWORK_REQUIRED"})["kind"])

    def test_running_continues(self):
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "RUNNING", "pending_questions": []}))


class TaskRunTests(unittest.TestCase):
    """End to end through the real CLI with the offline fixture provider (a few seconds)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="taskrun-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(root / "registry"),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def test_start_approve_and_complete(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            run.approve_plan("not-the-displayed-token")
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertTrue(view["done"], view)
        self.assertTrue((self.workspace / "greet.py").is_file())
        # A new caller can reattach to the saved run.
        again = taskrun.TaskRun(self.workspace, run.run_dir, options=FIXTURE_OPTIONS, env=self.env)
        self.assertEqual("TASK_COMPLETE", again.status()["status"])

    def test_usage_errors_are_not_mistaken_for_a_pause(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        broken = taskrun.TaskRun(self.workspace, run.run_dir, options=("--no-such-flag",), env=self.env)
        with self.assertRaisesRegex(taskrun.TaskRunError, "unrecognized arguments"):
            broken.advance()


if __name__ == "__main__":
    unittest.main()
