"""AutoPlanner step 2 (issue #62): the runner-owned clarification episode, the
single investigation pass for discoverable questions, and machine-resolution
validity. Discoverable questions must never reach the user."""
from pathlib import Path
import tempfile
import unittest

import autocode_goals as goals, autopilot
from units import autoplanner as planner
from goal_fixtures import body


def question(qid, kind="discoverable", category="technical", default=""):
    return {"id": qid, "question": f"What is {qid}?", "why": f"{qid} changes the design", "options": [],
            "proposed_default": default, "kind": kind, "category": category, "delegable": False}


def requirements(questions, **extra):
    report = {"summary": "Requirements", "intended_outcome": "Local greeting", "required_behaviors": ["Greet"],
              "constraints": [], "acceptance_tests": ["Run it"], "source_refs": ["config.py:1"],
              "proposed_assumptions": [], "open_questions": questions, "requirements": [],
              "ignored_statements": [], "conflicts": [], "proposed_reframes": [], "ignored_requirements": [],
              "machine_resolutions": [], "access_blockers": []}
    report.update(extra)
    return report


def discovery(contract, **extra):
    report = {"contract": contract, "summary": "Draft", "code_refs": ["config.py:1"], "alternatives": [],
              "uncertainties": [], "contract_changes": [], "requirement_trace": [],
              "machine_resolutions": [], "access_blockers": []}
    report.update(extra)
    return report


def clarification_only(questions):
    contract = body()
    contract.update(open_blocking_questions=questions, technical_approach=[], milestones=[])
    return contract


class EpisodeCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        (self.workspace / "config.py").write_text("PROVIDER = 'opencode'\nROUTE = 'default'\n")
        self.state = {"version": 3, "task_id": "task-1", "task": "Build a greeting tool",
                      "workspace": str(self.workspace), "answers": {}, "user_events": [],
                      "acceptance_criteria": [], "status": "RUNNING", "next_stage": "requirements_gather",
                      "settings": {"joint_planning": True,
                                   "roles": {"requirements": {}, "glm": {}, "plan_reviewer": {}}}}

    def apply(self, stage, value, output="report.json"):
        autopilot.apply_planning(self.state, stage, value, {"output": output})

    def queue_pass(self, questions):
        self.apply("requirements_gather", requirements(questions), "first.json")
        return self.state["investigation_request"]["handoff_hash"]

    def resolution(self, qid, handoff_hash, refs=("config.py:1",)):
        return {"question_id": qid, "resolution": "The provider is set in config.py", "source_refs": list(refs),
                "handoff_hash": handoff_hash}


class InvestigationPassTests(EpisodeCase):
    def test_discoverable_question_queues_one_pass_instead_of_reaching_the_user(self):
        self.apply("requirements_gather", requirements([question("Q1"), question("Q2", "decision", "cost")]))
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertNotIn("requirements_handoff", self.state)
        request = self.state["investigation_request"]
        self.assertEqual(["Q1"], request["question_ids"])
        self.assertTrue(self.state["clarification_episode"]["investigation_used"])
        prompt, _ = planner.context(self.state, "requirements_gather", Path("/tmp/state.json"))
        self.assertIn("INVESTIGATION PASS", prompt)
        self.assertIn(request["handoff_hash"], prompt)
        other, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertNotIn("INVESTIGATION PASS", other)

    def test_code_evidenced_resolution_retires_the_question_without_a_user_event(self):
        handoff_hash = self.queue_pass([question("Q1"), question("Q2", "decision", "cost")])
        events = len(self.state["user_events"])
        self.apply("requirements_gather", requirements([question("Q2", "decision", "cost")],
                   machine_resolutions=[self.resolution("Q1", handoff_hash, ["config.py:1-2"])]))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual(["Q2"], [q["id"] for q in self.state["requirements_handoff"]["report"]["open_questions"]])
        self.assertEqual("Q1", self.state["machine_resolutions"][0]["question_id"])
        self.assertNotIn("investigation_request", self.state)
        self.assertEqual(events, len(self.state["user_events"]))

    def test_discovery_accepts_a_handoff_question_retired_by_its_own_investigation(self):
        self.apply("requirements_gather", requirements([question("Q3", "decision")]))
        self.apply("astra_discovery", discovery(clarification_only([question("Q3")])))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertNotIn("goal_contract", self.state)
        handoff_hash = self.state["investigation_request"]["handoff_hash"]
        self.apply("astra_discovery", discovery(body(), machine_resolutions=[self.resolution("Q3", handoff_hash)]))
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual([], self.state["goal_contract"]["body"]["open_blocking_questions"])

    def test_policy_choice_cannot_be_resolved_from_the_workspace(self):
        handoff_hash = self.queue_pass([question("Q1", category="cost")])
        with self.assertRaisesRegex(ValueError, "only a technical fact"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[self.resolution("Q1", handoff_hash)]))
        self.assertNotIn("requirements_handoff", self.state)

    def test_resolution_for_a_decision_question_is_rejected(self):
        handoff_hash = self.queue_pass([question("Q1"), question("Q2", "decision")])
        with self.assertRaisesRegex(ValueError, "does not name an outstanding discoverable question"):
            self.apply("requirements_gather", requirements(
                [question("Q1", "decision")], machine_resolutions=[self.resolution("Q2", handoff_hash)]))

    def test_resolution_outside_a_pass_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "only in the investigation pass"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[self.resolution("Q1", "x")]))

    def test_resolution_bound_to_another_report_is_rejected(self):
        self.queue_pass([question("Q1")])
        with self.assertRaisesRegex(ValueError, "bound to a different report"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[self.resolution("Q1", "stale")]))

    def test_resolution_must_cite_real_workspace_source(self):
        handoff_hash = self.queue_pass([question("Q1")])
        for refs in ([], ["missing.py:1"], ["../outside.py"], ["config.py:40"]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.apply("requirements_gather", requirements([], machine_resolutions=[
                    self.resolution("Q1", handoff_hash, refs)]))

    def test_missing_source_becomes_an_access_blocker_decision_not_a_retry(self):
        self.queue_pass([question("Q1")])
        blocked = {**question("Q1", "decision"), "why": "The provider config file is not in this workspace"}
        self.apply("requirements_gather", requirements(
            [blocked], access_blockers=[{"question_id": "Q1", "reason": "No provider config is checked in"}]))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("decision", self.state["requirements_handoff"]["report"]["open_questions"][0]["kind"])

    def test_pass_cannot_silently_drop_a_question(self):
        self.queue_pass([question("Q1")])
        with self.assertRaisesRegex(ValueError, "dropped questions"):
            self.apply("requirements_gather", requirements([]))

    def test_still_discoverable_after_the_pass_becomes_a_labelled_decision(self):
        self.queue_pass([question("Q1")])
        self.apply("requirements_gather", requirements([question("Q1")]))
        asked = self.state["requirements_handoff"]["report"]["open_questions"][0]
        self.assertEqual("decision", asked["kind"])
        self.assertIn("single investigation pass", asked["why"])
        self.assertNotIn("investigation_request", self.state)

    def test_glm_revise_pass_keeps_the_contract_and_review_concerns(self):
        self.state["workspace"] = "/absent-workspace"
        goals.install_draft(self.state, body(), origin="glm_draft")
        concerns = [{"id": "P1", "concern": "c", "evidence_refs": ["x"], "requested_change": "r",
                     "acceptance_test": "t", "blocking": True}]
        self.state["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": concerns}}
        revision = self.state["goal_contract"]["revision"]
        response = [{"concern_id": "P1", "response": "ok", "evidence_refs": ["x"], "change": "c", "acceptance_test": "t"}]
        revise = {"summary": "Revised", "code_refs": [], "responses": response, "contract_changes": [],
                  "requirement_trace": [], "machine_resolutions": [], "access_blockers": []}
        self.apply("glm_revise", {**revise, "contract": {**body(), "open_blocking_questions": [question("Q1")]}})
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertEqual(revision, self.state["goal_contract"]["revision"])
        self.assertEqual(concerns, self.state["planning"]["reports"]["astra_challenge"]["report"]["concerns"])
        self.assertEqual("glm_revise", self.state["investigation_request"]["stage"])


    def test_final_reviewer_question_labelled_discoverable_reaches_the_user_as_a_decision(self):
        self.state["workspace"] = "/absent-workspace"
        goals.install_draft(self.state, body(), origin="glm_draft")
        self.state["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": []}}
        final = {**body(), "open_blocking_questions": [question("Q1")],
                 "initial_task": {"objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                                  "requirements": [], "acceptance_criteria": [], "validation_plan": []}}
        self.apply("astra_finalize", {"contract": final, "summary": "Needs a decision", "decisions": [],
                                      "contract_changes": [], "requirement_trace": []})
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("decision", self.state["pending_questions"][0]["kind"])
        self.assertNotIn("investigation_request", self.state)


class EpisodeBudgetTests(EpisodeCase):
    def test_regenerated_ids_and_reworded_handoffs_do_not_replenish_the_pass(self):
        self.queue_pass([question("Q1")])
        episode = dict(self.state["clarification_episode"])
        self.apply("requirements_gather", requirements([question("Q1", "decision")]))
        self.apply("requirements_gather", {**requirements([question("Q9")]), "summary": "Reworded"})
        self.assertNotIn("investigation_request", self.state)
        self.assertEqual("decision", self.state["requirements_handoff"]["report"]["open_questions"][0]["kind"])
        self.assertEqual(episode["id"], self.state["clarification_episode"]["id"])

    def test_non_delegated_answer_starts_a_new_episode_with_one_new_pass(self):
        goals.install_draft(self.state, body(questions=True), origin="glm_draft")
        first = goals.clarification_episode(self.state)
        first["investigation_used"] = True
        goals.answer(self.state, "Q1", "CLI")
        episode = self.state["clarification_episode"]
        self.assertNotEqual(first["id"], episode["id"])
        self.assertFalse(episode["investigation_used"])
        self.assertTrue(self.state["answers"]["Q1"]["starts_episode"])

    def test_delegation_resumes_without_replenishing_the_pass(self):
        goals.install_draft(self.state, body(questions=True), origin="glm_draft")
        episode = goals.clarification_episode(self.state)
        episode["investigation_used"] = True
        goals.answer(self.state, "Q1", "accept default", delegated=True)
        self.assertEqual(episode["id"], self.state["clarification_episode"]["id"])
        self.assertTrue(self.state["clarification_episode"]["investigation_used"])
        self.assertNotIn("starts_episode", self.state["answers"]["Q1"])

    def test_feedback_and_edited_goal_start_new_episodes(self):
        goals.install_draft(self.state, body(questions=True), origin="glm_draft")
        first = goals.clarification_episode(self.state)["id"]
        goals.feedback(self.state, "Also support a web page")
        second = self.state["clarification_episode"]
        self.assertNotEqual(first, second["id"])
        self.assertEqual(self.state["brief_feedback"][-1]["id"], second["started_by"])
        goals.install_draft(self.state, body(), origin="user_cli_edit")
        self.assertNotEqual(second["id"], self.state["clarification_episode"]["id"])

    def test_new_episode_discards_a_pending_pass(self):
        self.queue_pass([question("Q1")])
        goals.start_clarification_episode(self.state, "feedback-x")
        self.assertNotIn("investigation_request", self.state)


class LegacyCompatibilityTests(EpisodeCase):
    def test_pre_structured_string_assumptions_are_still_accepted(self):
        self.apply("requirements_gather", requirements([], proposed_assumptions=["A CLI may suffice"]))
        self.assertEqual(["A CLI may suffice"], self.state["requirements_handoff"]["report"]["proposed_assumptions"])

    def test_generation_schema_gives_the_model_a_structured_assumption(self):
        schema = planner.SCHEMAS["requirements_gather"]["properties"]["proposed_assumptions"]
        self.assertEqual("object", schema["items"]["type"])
        self.assertIn("convention_ref", schema["items"]["required"])

    def test_legacy_run_without_an_episode_gets_one_lazily(self):
        self.assertNotIn("clarification_episode", self.state)
        episode = goals.clarification_episode(self.state)
        self.assertEqual("initial_task", episode["started_by"])
        self.assertFalse(episode["investigation_used"])


if __name__ == "__main__":
    unittest.main()
