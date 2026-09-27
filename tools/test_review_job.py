"""The review workflow: a recognized review goes straight to the Reviewer, which
changes nothing and leaves review/findings.json behind."""
import json
import tempfile
import unittest
from pathlib import Path

from . import autocode_review_job as review_job
from . import autocode_run_view as run_view
from . import autocode_workflows as workflows
from . import autopilot
from .units import autoreview


def state_for(task="Review pr-184.patch before I merge it.", workspace="/nowhere"):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p"},
                "astra": {"model": "a"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def report(verdict="request_changes", findings=None, delivered_tests=()):
    return {"verdict": verdict, "summary": "one regression", "change_under_review": "pr-184.patch",
            "findings": findings if findings is not None else [
                {"id": "F1", "severity": "blocking", "file": "regclient/client.py", "lines": [26, 28],
                 "summary": "resends without reconciling", "evidence": "README Retries"}],
            "tests_run": ["python3 -m unittest"], "delivered_tests": list(delivered_tests)}


class RoutingTests(unittest.TestCase):
    def test_a_recognized_review_goes_to_the_reviewer_not_requirements(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "review", "reason": "", "signals": []}, {"output": "o"})
        self.assertEqual(review_job.STAGE, state["next_stage"])
        self.assertEqual("autoreview", autopilot.unit_for(review_job.STAGE))

    def test_kinds_without_their_own_first_stage_continue_into_the_build_pipeline(self):
        for kind in ("build", "design", "discuss"):
            state = state_for()
            workflows.begin(state, "requirements_gather")
            workflows.apply(state, {"workflow": kind, "reason": "", "signals": []}, {"output": "o"})
            self.assertEqual("requirements_gather", state["next_stage"], kind)


class PrepareTests(unittest.TestCase):
    def test_reviewer_runs_on_the_validator_route_with_write_access_for_its_scratch_copy(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            request = autoreview.prepare(state, review_job.STAGE, "/run/state.json", None)
        self.assertEqual(("sol", "sol", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual(review_job.SCHEMA, request.schema)
        self.assertIn("Review pr-184.patch", request.prompt)
        self.assertIn("review/findings.json", request.prompt)
        self.assertEqual("REVIEWING", state["phase"])


class ApplyTests(unittest.TestCase):
    def test_writes_the_findings_file_and_completes_the_run(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            state["workflow"] = {"kind": "review"}
            review_job.apply(state, report(), {"changed_files": [], "output": "/run/review_change-01.json"}, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        self.assertEqual("request_changes", written["verdict"])
        self.assertEqual(["F1"], [f["id"] for f in written["findings"]])
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertEqual("review", view["workflow"])
        self.assertEqual({"blocking": 1, "advisory": 0}, {k: state["review"][k] for k in ("blocking", "advisory")})
        self.assertIn("1 blocking, 0 advisory", review_job.render(state))

    def test_a_review_that_changed_the_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            with self.assertRaisesRegex(ValueError, "must not change the repository.*regclient/client.py"):
                review_job.apply(state, report(), {"changed_files": ["regclient/client.py", "review/notes.md"]}, workspace)
            self.assertFalse((Path(workspace) / "review" / "findings.json").exists())
        self.assertEqual("RUNNING", state["status"])

    def test_writing_under_review_is_allowed(self):
        self.assertEqual([], review_job.stray_changes(["review/findings.json", "review/tests/test_x.py"]))
        self.assertEqual(["tests/test_x.py"], review_job.stray_changes(["review/a.json", "tests/test_x.py"]))

    def test_approve_with_a_blocking_finding_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "cannot approve"):
                review_job.apply(state_for(workspace=workspace), report(verdict="approve"), {"changed_files": []}, workspace)

    def test_delivered_targeted_tests_are_recorded_when_they_exist(self):
        with tempfile.TemporaryDirectory() as workspace:
            test = Path(workspace) / "review" / "tests" / "test_at_policy.py"
            test.parent.mkdir(parents=True)
            test.write_text("import unittest\n")
            (Path(workspace) / "review" / "tests" / "test_forgotten.py").write_text("import unittest\n")
            state = state_for(workspace=workspace)
            record = {"changed_files": ["review/tests/test_at_policy.py", "review/tests/test_forgotten.py"]}
            review_job.apply(state, report(delivered_tests=["review/tests/test_at_policy.py"]), record, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        # The one the stage wrote but the report forgot is recorded too.
        self.assertEqual(["review/tests/test_at_policy.py", "review/tests/test_forgotten.py"], written["delivered_tests"])
        self.assertIn("Targeted test delivered: review/tests/test_at_policy.py", review_job.render(state))

    def test_a_report_that_claims_an_undelivered_or_misplaced_test_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "not delivered"):
                review_job.apply(state_for(workspace=workspace),
                                 report(delivered_tests=["review/tests/test_missing.py"]), {"changed_files": []}, workspace)
            with self.assertRaisesRegex(ValueError, "must live under review/tests/"):
                review_job.apply(state_for(workspace=workspace),
                                 report(delivered_tests=["tests/test_x.py"]), {"changed_files": []}, workspace)

    def test_a_clean_approval_completes_with_no_findings(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            review_job.apply(state, report(verdict="approve", findings=[]), {"changed_files": []}, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        self.assertEqual(("approve", []), (written["verdict"], written["findings"]))
        self.assertEqual("TASK_COMPLETE", state["status"])


if __name__ == "__main__":
    unittest.main()
