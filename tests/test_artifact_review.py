"""Public CLI regression for a technically validated artifact awaiting human acceptance."""
import copy
import json
import unittest

from . import test_subprocess, test_goals
import autocode_completion as completion
import autocode_goals as goals
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body


class ArtifactReviewCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_unverified_human_result_presents_review_without_repeating_validation(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "human-pending"
        probe = self.root / "launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run, _ = self.saved()
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        need = status["view"]["needs"]
        self.assertEqual("review", need["kind"])
        self.assertEqual(["C1"], need["criteria"])
        self.assertTrue(need["token"])
        self.assertFalse(status["view"]["done"])
        # The public review token approves only this current artifact; resume then completes.
        self.launch(["--run-dir", str(run), "--approve-review", "C1", "--review-token", need["token"]], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 0)
        done = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertTrue(done["view"]["done"])
        stages = [json.loads(line) for line in probe.read_text().splitlines()]
        self.assertEqual(1, sum(row["stage"] == "sol" for row in stages))
        self.assertEqual(1, sum(row["stage"] == "terra" for row in stages))


class ArtifactReviewGateTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    decision = test_goals.GoalTests.decision
    validation = test_goals.GoalTests.validation

    def fixture(self):
        draft = body(human=True)
        draft["acceptance_criteria"].append({"id": "C2", "criterion": "Technical behavior",
            "verification_method": "Execute the CLI", "human_review": False})
        draft["milestones"][0]["acceptance_criteria"].append("C2")
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        current = self.validation()
        validation = self.state["validation"]
        validation.update(verdict="BLOCKED", unverified_criteria=["C1 human acceptance pending"])
        validation["criterion_results"][0]["status"] = "NOT_VERIFIED"
        decision = self.decision()
        decision["next_task"]["kind"] = "validate"
        decision["acceptance_criteria"][0]["status"] = "unverified"
        return self.state, decision, current

    def test_review_request_never_marks_human_acceptance_or_changes_model_reports(self):
        state, decision, current = self.fixture()
        before = copy.deepcopy((state, decision))
        request = completion.artifact_review_request(state, decision, current)
        self.assertEqual(["C1"], request["criteria"])
        self.assertEqual(before, (state, decision))
        self.assertFalse(completion.completion_ready(state, {**decision, "status": "TASK_COMPLETE"}, current))

    def test_review_does_not_hide_any_technical_gap_or_requested_correction(self):
        state, decision, current = self.fixture()
        cases = {
            "implementation requested": lambda s, d: d["next_task"].update(kind="implement"),
            "rework requested": lambda s, d: d.update(status="REWORK"),
            "unverified technical decision": lambda s, d: d["acceptance_criteria"][1].update(status="unverified"),
            "missing decision evidence": lambda s, d: d["acceptance_criteria"][0].update(evidence=""),
            "changed criterion": lambda s, d: d["acceptance_criteria"][0].update(criterion="Different"),
            "failed technical outcome": lambda s, d: s["validation"]["criterion_results"][1].update(status="FAIL"),
            "missing technical evidence": lambda s, d: s["validation"]["criterion_results"][1].update(evidence_refs=[]),
            "failed check": lambda s, d: s["validation"]["checks"][0].update(exit_code=1),
            "failed replay": lambda s, d: s["validation"]["check_replay"].update(verdict="FAIL"),
            "missing replay": lambda s, d: s["validation"].pop("check_replay"),
            "stale replay": lambda s, d: s["validation"]["check_replay"].update(source_revision="old"),
            "stale validation": lambda s, d: s["validation"].update(source_revision="old"),
            "stale contract": lambda s, d: s["validation"].update(contract_hash="old"),
            "blocking decision": lambda s, d: d.update(findings=[{"blocking": True}]),
            "blocking validation": lambda s, d: s["validation"].update(findings=[{"blocking": True}]),
            "failed flow": lambda s, d: s["validation"]["end_to_end_result"].update(status="FAIL"),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                candidate, report = copy.deepcopy((state, decision))
                change(candidate, report)
                self.assertIsNone(completion.artifact_review_request(candidate, report, current))
        evidence = next(iter(state["validation"]["evidence_hashes"]))
        from pathlib import Path
        Path(evidence).write_text("Changed evidence")
        self.assertIsNone(completion.artifact_review_request(state, decision, current))
