"""The design workflow's Architect: review a design without building anything, or hand a
request for a new design on to the build pipeline."""
import json
import tempfile
import unittest
from pathlib import Path

import autocode_design_job as design_job
import autocode_jobs as jobs
import autocode_run_view as run_view
import autocode_workflows as workflows
import autopilot
from units import autoreview


def state_for(workspace="/nowhere", task="Review the design in docs/design/kafka-events.md."):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "workflow": {"kind": "design", "then": "requirements_gather"},
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p", "engine": "codex"},
                "astra": {"model": "a", "engine": "codex"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def report(mode="review", **overrides):
    value = {"mode": mode, "design_under_review": "docs/design/kafka-events.md", "verdict": "request_changes",
             "summary": "Ordering is lost", "satisfied": ["Throughput: 24 partitions cover 5,000/s"],
             "concerns": [{"id": "F1", "area": "ordering", "severity": "blocking",
                           "summary": "Keyed by kind; per-domain order is lost", "evidence": "Producer paragraph"}],
             "questions": [{"id": "Q1", "question": "Is ordering per domain required?", "options": ["yes", "no"]}]}
    if mode == "propose":
        value.update(design_under_review="", verdict="not_applicable", satisfied=[], concerns=[], questions=[])
    value.update(overrides)
    return value


class RoutingTests(unittest.TestCase):
    def test_a_recognized_design_job_goes_to_the_architect(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "design", "reason": "", "signals": []}, {})
        self.assertEqual(design_job.STAGE, state["next_stage"])
        self.assertEqual("autoreview", autopilot.unit_for(design_job.STAGE))
        self.assertIn(design_job.STAGE, jobs.STAGES)


class PrepareTests(unittest.TestCase):
    def test_architect_inherits_the_plan_reviewer_model_on_its_own_route(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            request = autoreview.prepare(state, design_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "architect", True), (request.role, request.route_role, request.allow_write))
        roles = state["settings"]["roles"]
        self.assertEqual((roles["plan_reviewer"]["model"], roles["plan_reviewer"]["engine"]),
                         (roles["architect"]["model"], roles["architect"]["engine"]))
        self.assertEqual(design_job.SCHEMA, request.schema)
        self.assertIn("kafka-events.md", request.prompt)

    def test_architect_effort_is_capped_at_medium_but_never_raised(self):
        for given, expected in (("max", "medium"), ("xhigh", "medium"), ("high", "medium"),
                                ("medium", "medium"), ("low", "low"), (None, "medium"), ("weird", "medium")):
            roles = {"plan_reviewer": {"model": "p", "reasoning_effort": given}, "astra": {"model": "a"}}
            with self.subTest(given=given):
                self.assertEqual(expected, autoreview.architect_route(roles)["reasoning_effort"])
                self.assertEqual(given, roles["plan_reviewer"]["reasoning_effort"], "the Plan Reviewer is untouched")


class ApplyTests(unittest.TestCase):
    def apply(self, value, changed=()):
        workspace = tempfile.mkdtemp()
        state = state_for(workspace)
        autoreview.apply_job(design_job.STAGE, state, value, {"changed_files": list(changed), "output": "o"}, workspace)
        return state, Path(workspace)

    def test_a_review_writes_the_report_and_completes_without_building(self):
        state, workspace = self.apply(report())
        written = json.loads((workspace / "review" / "design-review.json").read_text())
        self.assertEqual(("request_changes", ["F1"], ["Q1"]),
                         (written["verdict"], [c["id"] for c in written["concerns"]], [q["id"] for q in written["questions"]]))
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertEqual("design", view["workflow"])
        self.assertIs(design_job, jobs.ended_in(state))
        self.assertIn("1 blocking, 0 advisory, 1 question(s) for you", jobs.render(state, lambda _: "build"))

    def test_a_request_for_a_new_design_is_handed_to_the_build_pipeline(self):
        state, workspace = self.apply(report("propose"))
        self.assertFalse((workspace / "review").exists())
        self.assertEqual(("RUNNING", "requirements_gather"), (state["status"], state["next_stage"]))
        self.assertIsNone(jobs.ended_in(state))

    def test_a_design_review_that_changed_the_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not change the repository.*docs/design/kafka-events.md"):
            self.apply(report(), changed=["docs/design/kafka-events.md"])

    def test_verdict_must_match_the_blocking_concerns(self):
        with self.assertRaisesRegex(ValueError, "verdict"):
            self.apply(report(verdict="approve"))
        with self.assertRaisesRegex(ValueError, "verdict"):
            self.apply(report(verdict="request_changes", concerns=[]))

    def test_a_sound_design_is_approved_with_no_blocking_concerns(self):
        advisory = [{"id": "S1", "area": "ops", "severity": "advisory", "summary": "retention", "evidence": "x"}]
        state, workspace = self.apply(report(verdict="approve", concerns=advisory))
        self.assertEqual("approve", json.loads((workspace / "review" / "design-review.json").read_text())["verdict"])
        self.assertEqual(0, state["design_review"]["blocking"])

    def test_propose_mode_may_not_smuggle_a_review(self):
        with self.assertRaisesRegex(ValueError, "handed on, not reviewed"):
            self.apply(report("propose", concerns=report()["concerns"]))


if __name__ == "__main__":
    unittest.main()
