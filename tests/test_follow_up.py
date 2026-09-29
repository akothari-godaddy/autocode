"""A follow-up to a finished run: the next turn of the same conversation (issue #51).

The whole conversation (review, then "Fix them.") runs end to end in the
review-then-fix scenario; these are the pure rules behind it.
"""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_follow_up as follow_up
import autocode_workflows as workflows
from units import autoplanner

FINDINGS = [
    {"id": "F1", "severity": "blocking", "file": "regclient/client.py", "lines": [26, 28],
     "summary": "Timeout resends without asking the registry", "evidence": "README Retries", "proven_by": []},
    {"id": "F2", "severity": "blocking", "file": "regclient/policies.py", "lines": [33, 39],
     "summary": ".de is retried", "evidence": "README .de", "proven_by": []},
    {"id": "S1", "severity": "advisory", "file": "regclient/policies.py", "lines": [18, 25],
     "summary": "_errors is clumsy", "evidence": "reading"},
]


def finished_review(workspace: Path) -> dict:
    (workspace / "review").mkdir()
    (workspace / "review" / "findings.json").write_text(json.dumps({"verdict": "request_changes",
                                                                    "findings": FINDINGS}))
    return {"status": "TASK_COMPLETE", "phase": "COMPLETE", "next_stage": None, "completed_at": "t0",
            "task": "Review pr-184.patch before I merge it.", "settings": {}, "workspace": str(workspace),
            "workflow": {"kind": "review", "reason": "a patch", "signals": [], "source": "model",
                         "then": "requirements_gather"},
            "review": {"report_path": "review/findings.json", "verdict": "request_changes",
                       "change_under_review": "pr-184.patch", "change_patch": "pr-184.patch"}}


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.state = finished_review(self.workspace)

    def test_a_follow_up_to_a_review_reopens_the_run_to_recognize_the_new_message(self):
        follow_up.accept(self.state, "  Fix them.  ", self.workspace, "t1")
        self.assertEqual(("RUNNING", workflows.STAGE, None), (self.state["status"], self.state["next_stage"],
                                                              workflows.kind(self.state)))
        self.assertNotIn("completed_at", self.state)
        self.assertTrue(self.state["task"].startswith("Fix them.\n"))
        self.assertIn("Review pr-184.patch before I merge it.", self.state["task"])
        turn = follow_up.current(self.state)
        self.assertEqual(("Fix them.", "review", "Review pr-184.patch before I merge it."),
                         (turn["say"], turn["previous"]["workflow"], turn["previous"]["task"]))
        self.assertEqual((["F1", "F2"], ["S1"]), ([f["id"] for f in turn["previous"]["review"]["blocking"]],
                                                  [f["id"] for f in turn["previous"]["review"]["advisory"]]))
        # The findings stand in for requirements: a build recognized next goes straight to the Planner.
        self.assertEqual(workflows.planner_stage(self.state), self.state["workflow"]["then"])

    def test_the_recognizer_reads_the_new_message_with_the_earlier_turn_as_context(self):
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        packet = workflows.packet(self.state)
        self.assertEqual("Fix them.", packet["task"])
        self.assertEqual({"message": "Fix them.", "previous_workflow": "review",
                          "previous_request": "Review pr-184.patch before I merge it.",
                          "previous_review": {"verdict": "request_changes", "blocking": 2, "advisory": 1}},
                         packet["follow_up"])
        workflows.apply(self.state, {"workflow": "build", "reason": "act on the findings", "signals": []},
                        {"output": "r.json"})
        self.assertEqual(workflows.planner_stage(self.state), self.state["next_stage"])
        self.assertNotIn("follow_up", workflows.packet(self.state))

    def test_the_planner_gets_the_findings_only_when_the_next_job_builds_or_fixes(self):
        self.assertIsNone(follow_up.review_findings(self.state))
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        self.assertIsNone(follow_up.review_findings(self.state))  # not recognized yet
        for kind, wanted in (("build", True), ("bugfix", True), ("discuss", False), ("review", False)):
            state = copy.deepcopy(self.state)
            workflows.apply(state, {"workflow": kind, "reason": "", "signals": []}, {})
            findings = follow_up.review_findings(state)
            with self.subTest(kind=kind):
                self.assertEqual(wanted, findings is not None)
                if findings:
                    self.assertEqual(("review/findings.json", "pr-184.patch", ["F1", "F2"]),
                                     (findings["report_path"], findings["change_patch"],
                                      [f["id"] for f in findings["blocking"]]))

    def test_the_planner_plans_from_the_findings(self):
        subprocess.run(["git", "init", "-q"], cwd=self.workspace, check=True)
        self.state.update(version=3, stages=[], task_id="t", iteration=1, answers={}, user_events=[], history=[],
                          sessions={}, settings={"joint_planning": True, "roles": {
                              role: {"model": role[0]} for role in ("requirements", "glm", "plan_reviewer", "terra", "sol")}
                              | {"astra": {"model": "a", "engine": "codex"}}})
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        workflows.apply(self.state, {"workflow": "build", "reason": "", "signals": []}, {})
        prompt, _ = autoplanner.context(self.state, "astra_discovery", self.workspace / "state.json")
        self.assertIn(autoplanner.REVIEW_FINDINGS_RULE, prompt)
        self.assertIn('"review_findings"', prompt)
        self.assertIn("Timeout resends without asking the registry", prompt)
        workflows.apply(self.state, {"workflow": "discuss", "reason": "", "signals": []}, {})
        other, _ = autoplanner.context(self.state, "astra_discovery", self.workspace / "state.json")
        self.assertNotIn(autoplanner.REVIEW_FINDINGS_RULE, other)

    def test_only_a_finished_run_takes_a_follow_up_and_the_message_must_say_something(self):
        for status in ("RUNNING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL", "PAUSED_INVALID_OUTPUT"):
            state = {**copy.deepcopy(self.state), "status": status}
            before = copy.deepcopy(state)
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "continues a finished run"):
                follow_up.accept(state, "Fix them.", self.workspace, "t1")
            self.assertEqual(before, state)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            follow_up.accept(self.state, "   ", self.workspace, "t1")

    def test_a_follow_up_to_another_job_keeps_the_run_s_first_stage(self):
        state = {**copy.deepcopy(self.state), "workflow": {"kind": "discuss", "then": "requirements_gather"}}
        state.pop("review")
        follow_up.accept(state, "Now build it.", self.workspace, "t1")
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        self.assertNotIn("review", follow_up.current(state)["previous"])

    def test_a_later_follow_up_goes_back_to_the_run_s_first_stage(self):
        follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        workflows.apply(self.state, {"workflow": "build", "reason": "", "signals": []}, {})
        self.state.update(status="TASK_COMPLETE", completed_at="t2")
        follow_up.accept(self.state, "Now add a retry for .org.", self.workspace, "t3")
        self.assertEqual("requirements_gather", self.state["workflow"]["then"])
        self.assertEqual(2, len(self.state["turns"]))

    def test_an_unreadable_review_report_is_refused(self):
        (self.workspace / "review" / "findings.json").write_text("{not json")
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "cannot be read"):
            follow_up.accept(self.state, "Fix them.", self.workspace, "t1")
        self.assertEqual(before, self.state)


if __name__ == "__main__":
    unittest.main()
