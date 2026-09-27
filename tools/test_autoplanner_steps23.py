"""AutoPlanner steps 2 and 3 of the consolidated v1 scope on GitHub issue #62.

Step 2: runner-owned clarification episodes and the one bounded investigation
pass that keeps discoverable questions away from the user (E1-E3).
Step 3: remediation records, Plan Reviewer obligation decisions, and the
gates that keep an unresolved assumption rejection from reaching execution (E8).
Numbers in test names refer to the scope's regression list (section G).
"""
import copy
from pathlib import Path
import tempfile
import unittest

from . import autocode_goals as goals, autocode_support as support, autopilot
from .units import autoplanner as planner
from .goal_fixtures import body, approve_fixture
from .test_planner_invariants import state

TASK = "Build a greeting CLI"
TRACE = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]


def question(qid, kind="discoverable", category="technical", **extra):
    return {"id": qid, "question": f"What about {qid}?", "why": "It changes the plan",
            "options": [], "proposed_default": "", "kind": kind, "category": category,
            "delegable": False, **extra}


def assumption(aid="A1", category="technical", supports=("R1",)):
    return {"id": aid, "text": f"Assumption {aid} holds", "kind": "inferable", "category": category,
            "convention_ref": "greet.py:1", "rationale": "Existing CLI convention", "supports": list(supports)}


def requirements(questions=(), assumptions=(), **extra):
    return {"summary": "Requirements", "intended_outcome": "A greeting CLI",
            "required_behaviors": ["Print a greeting"], "constraints": [], "acceptance_tests": ["Run it"],
            "source_refs": ["greet.py:1"], "proposed_assumptions": list(assumptions),
            "open_questions": list(questions),
            "requirements": [{"id": "R1", "text": TASK, "source_quote": TASK}],
            "ignored_statements": [], "conflicts": [], "proposed_reframes": [], **extra}


def clarifying(questions, **extra):
    draft = body()
    draft.update(milestones=[], technical_approach=[], open_blocking_questions=list(questions))
    return {"contract": draft, "summary": "Need answers", "code_refs": ["greet.py:1"], "alternatives": [],
            "uncertainties": [], "contract_changes": [], "requirement_trace": copy.deepcopy(TRACE), **extra}


def plan(**extra):
    return {"contract": body(), "summary": "Plan", "code_refs": ["greet.py:1"], "alternatives": [],
            "uncertainties": [], "contract_changes": [], "requirement_trace": copy.deepcopy(TRACE), **extra}


def challenge(concerns=(), decisions=()):
    return {"summary": "Review", "concerns": list(concerns), "obligation_decisions": list(decisions)}


def revise(**extra):
    return {"contract": body(), "summary": "Revised", "code_refs": ["greet.py:1"], "responses": [],
            "contract_changes": [], "requirement_trace": copy.deepcopy(TRACE), **extra}


def finalize(decisions=(), questions=(), concerns=()):
    draft = body()
    draft["open_blocking_questions"] = list(questions)
    draft["initial_task"] = ({"objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                              "requirements": [], "acceptance_criteria": [], "validation_plan": []}
                             if questions else
                             {"objective": "Implement greeting CLI", "affected_paths": ["greet.py"],
                              "kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run the CLI"]})
    settled = [{"concern_id": concern_id, "decision": "Accepted", "rationale": "Addressed in revision",
                "acceptance_test": "Blank exits 2", "resolved": True} for concern_id in concerns]
    return {"contract": draft, "summary": "Final", "decisions": settled, "contract_changes": [],
            "requirement_trace": copy.deepcopy(TRACE), "obligation_decisions": list(decisions)}


def record(state_, obligation, **overrides):
    return {"obligation_id": obligation["id"], "assumption_id": obligation["assumption_id"],
            "approach": "Validate the name explicitly instead", "evidence_refs": ["greet.py:1"],
            "covered_requirements": list(obligation["supports"]),
            "episode_id": state_["clarification_episode"]["id"], **overrides}


def decision(obligation, resolved=True, **overrides):
    return {"obligation_id": obligation["id"], "remediation_hash": obligation["remediation_hash"],
            "resolved": resolved, "rationale": "Checked the new approach", "evidence_refs": ["greet.py:1"],
            **overrides}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Path(self.temp.name, "greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n")
        self.state = state(TASK)
        self.state["workspace"] = self.temp.name
        self.state["settings"]["roles"]["requirements"] = {}

    def apply(self, stage, value):
        if stage in ("astra_challenge", "astra_finalize"):
            planner.charge(self.state, stage)
        autopilot.apply_planning(self.state, stage, value, {"output": f"{stage}.json"})

    def rejected(self, stage, value, pattern):
        # The runner applies a report to a copy of state; a rejection leaves no trace.
        with self.assertRaisesRegex(ValueError, pattern):
            autopilot.apply_planning(copy.deepcopy(self.state), stage, value, {"output": f"{stage}.json"})

    def obligation(self, index=-1):
        return self.state["deferred_obligations"][index]

    def reject(self, assumption_id):
        """Reach a clarification stop, then reject a handoff assumption."""
        self.apply("astra_discovery", clarifying([question("D1", kind="decision", category="behavior")]))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        return goals.reject_assumption(self.state, assumption_id)


class InvestigationTests(Base):
    def test_01_discoverable_question_is_resolved_without_a_user_event(self):
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.apply("astra_discovery", clarifying([question("Q1")]))
        request = self.state["investigation_request"]
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertEqual([], self.state["pending_questions"])
        self.assertTrue(self.state["clarification_episode"]["investigation_used"])
        resolution = {"question_id": "Q1", "resolution": "Arguments are read from sys.argv",
                      "source_refs": ["greet.py:1"], "handoff_hash": request["handoff_hash"]}
        self.apply("requirements_gather", requirements(machine_resolutions=[resolution]))
        self.assertNotIn("investigation_request", self.state)
        self.assertEqual("Q1", self.state["machine_resolutions"][0]["question_id"])
        self.apply("astra_discovery", plan())
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual({}, self.state["answers"])
        self.assertEqual([], [e for e in self.state["user_events"] if e.get("kind") == "answer"])

    def test_01_planner_may_resolve_a_handoff_question_in_place_of_an_answer(self):
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.rejected("astra_discovery", plan(), "dropped unresolved requirements questions: Q1")
        resolution = {"question_id": "Q1", "resolution": "sys.argv", "source_refs": ["greet.py:1"],
                      "handoff_hash": goals.handoff_hash(self.state)}
        self.apply("astra_discovery", plan(machine_resolutions=[resolution]))
        self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_02_machine_resolution_for_a_decision_question_is_rejected(self):
        self.apply("requirements_gather", requirements([question("Q1", kind="decision", category="behavior")]))
        resolution = {"question_id": "Q1", "resolution": "Pick CLI", "source_refs": ["greet.py:1"],
                      "handoff_hash": goals.handoff_hash(self.state)}
        self.rejected("astra_discovery", plan(machine_resolutions=[resolution]),
                      "does not name an outstanding discoverable question")

    def test_02_machine_resolution_needs_a_technical_category_source_and_current_hash(self):
        self.apply("requirements_gather", requirements([question("Q1"), question("Q2", category="cost")]))
        good = {"question_id": "Q1", "resolution": "sys.argv", "source_refs": ["greet.py:1"],
                "handoff_hash": goals.handoff_hash(self.state)}
        draft = clarifying([question("Q2", category="cost")])
        self.rejected("astra_discovery", {**draft, "machine_resolutions": [{**good, "question_id": "Q2"}]},
                      "not a technical fact")
        self.rejected("astra_discovery", {**draft, "machine_resolutions": [{**good, "source_refs": []}]},
                      "needs a resolution and the source")
        self.rejected("astra_discovery", {**draft, "machine_resolutions": [{**good, "source_refs": ["nope.py"]}]},
                      "not a file in the workspace")
        self.rejected("astra_discovery", {**draft, "machine_resolutions": [{**good, "handoff_hash": "stale"}]},
                      "different requirements handoff")
        self.apply("astra_discovery", {**draft, "machine_resolutions": [good]})
        # Q2 is still discoverable, so it goes to the investigation pass, not the user.
        self.assertEqual(["Q2"], [row["id"] for row in self.state["investigation_request"]["questions"]])

    def test_03_missing_source_becomes_a_decision_question_not_a_retry(self):
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.apply("astra_discovery", clarifying([question("Q1")]))
        blocker = {"question_id": "Q1", "reason": "The provider config lives outside the workspace"}
        self.apply("requirements_gather", requirements(access_blockers=[blocker]))
        [asked] = self.state["requirements_handoff"]["report"]["open_questions"]
        self.assertEqual(("Q1", "decision"), (asked["id"], asked["kind"]))
        self.assertIn("outside the workspace", asked["why"])
        self.apply("astra_discovery", clarifying([asked]))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(["Q1"], [row["id"] for row in self.state["pending_questions"]])
        self.assertNotIn("investigation_request", self.state)

    def test_investigation_must_settle_each_question_exactly_one_way(self):
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.apply("astra_discovery", clarifying([question("Q1")]))
        self.rejected("requirements_gather", requirements([question("Q1")]), "must be resolved, reclassified")
        self.rejected("requirements_gather", requirements(), "exactly one way")
        blocker = {"question_id": "Q1", "reason": "Unreadable"}
        self.rejected("requirements_gather", requirements([question("Q1", kind="decision")], access_blockers=[blocker]),
                      "exactly one way")
        self.apply("requirements_gather", requirements([question("Q1", kind="decision", why="Only the owner knows")]))
        self.assertEqual("astra_discovery", self.state["next_stage"])

    def test_04_spent_investigation_is_not_replenished_by_new_ids_or_rewording(self):
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.apply("astra_discovery", clarifying([question("Q1")]))
        episode = copy.deepcopy(self.state["clarification_episode"])
        reworded = question("Q9", why="Reworded: which module parses arguments?")
        self.apply("requirements_gather", requirements([reworded], access_blockers=[
            {"question_id": "Q1", "reason": "Unreadable"}]))
        blocked = {row["id"]: row for row in self.state["requirements_handoff"]["report"]["open_questions"]}["Q1"]
        self.apply("astra_discovery", clarifying([blocked, reworded]))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(episode, self.state["clarification_episode"])
        shown = {row["id"]: row for row in self.state["pending_questions"]}
        self.assertEqual("decision", shown["Q9"]["kind"])
        self.assertIn("one workspace investigation", shown["Q9"]["why"])
        # A non-delegated answer is new user intent: exactly one new pass.
        goals.answer(self.state, "Q1", "Use the local config")
        self.assertNotEqual(episode["id"], self.state["clarification_episode"]["id"])
        self.assertFalse(self.state["clarification_episode"]["investigation_used"])
        self.apply("astra_discovery", clarifying([question("Q9")]))
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertTrue(self.state["clarification_episode"]["investigation_used"])

    def test_planner_relabeling_a_discoverable_question_does_not_bypass_investigation(self):
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.apply("astra_discovery", clarifying([question("Q1", kind="decision")]))
        self.assertEqual("requirements_gather", self.state["next_stage"])

    def test_without_a_requirements_role_the_question_is_shown_as_a_labeled_decision(self):
        del self.state["settings"]["roles"]["requirements"]
        self.apply("requirements_gather", requirements([question("Q1")]))
        self.apply("astra_discovery", clarifying([question("Q1")]))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("decision", self.state["pending_questions"][0]["kind"])


class EpisodeTests(Base):
    def test_05_delegation_after_a_finalize_question_does_not_reset_investigation(self):
        self.apply("requirements_gather", requirements())
        self.apply("astra_discovery", plan())
        self.apply("astra_challenge", challenge())
        self.apply("glm_revise", revise())
        episode = goals.start_episode(self.state, "earlier stop")
        episode.update(investigation_used=True, used_stage="astra_discovery")
        blocking = question("P2", kind="decision", category="behavior", proposed_default="Reject", delegable=True)
        self.apply("astra_finalize", finalize(questions=[blocking]))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        goals.delegate_all(self.state)
        self.assertEqual(episode, self.state["clarification_episode"])
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("RUNNING", self.state["status"])

    def test_feedback_and_edit_goal_start_a_new_episode_but_rejection_does_not(self):
        self.apply("requirements_gather", requirements(assumptions=[assumption()]))
        self.apply("astra_discovery", clarifying([question("D1", kind="decision")]))
        first = self.state["clarification_episode"]["id"]
        goals.reject_assumption(self.state, "A1")
        self.assertEqual(first, self.state["clarification_episode"]["id"])
        self.state["status"] = "WAITING_FOR_USER"
        goals.feedback(self.state, "Keep it offline")
        second = self.state["clarification_episode"]
        self.assertNotEqual(first, second["id"])
        self.assertEqual(self.state["brief_feedback"][-1]["id"], second["started_by"])
        goals.install_draft(self.state, body(), origin="user_cli_edit")
        self.assertEqual("edit_goal", self.state["clarification_episode"]["started_by"])


class AssumptionTests(Base):
    def test_06_assumption_without_a_category_cannot_be_inferred(self):
        row = assumption()
        del row["category"]
        with self.assertRaisesRegex(ValueError, "cannot be inferable"):
            goals.validate_assumptions([row], {"R1"})
        self.assertEqual("requested_outcome", goals.normalize_assumption(row)["category"])

    def test_08_refresh_cannot_rely_on_a_legacy_mention_or_mismatched_basis(self):
        self.state["brief_feedback"] = [{"id": "feedback-1", "text": "Drop the greeting"}]
        self.state["user_events"] = list(self.state["brief_feedback"])
        self.state["requirements_handoff"] = {"report": requirements(), "output": "prev.json"}
        dropped = requirements(ignored_statements=[TASK + " (no longer needed)"])
        dropped["requirements"] = []
        with self.assertRaisesRegex(ValueError, "dropped requirement R1"):
            goals.check_requirement_handoff(self.state, dropped)
        dropped["ignored_requirements"] = [{"requirement_id": "R1", "reason": "User dropped it",
                                            "basis": "user_answer", "event_id": "feedback-1"}]
        with self.assertRaisesRegex(ValueError, "saved user answer or feedback"):
            goals.check_requirement_handoff(self.state, dropped)

    def test_09_rejected_assumption_requirements_must_stay_covered(self):
        self.apply("requirements_gather", requirements(assumptions=[assumption()]))
        self.reject("A1")
        self.rejected("requirements_gather", requirements(assumptions=[assumption()]), "rejected by the user")
        self.apply("requirements_gather", requirements())
        uncovered = plan()
        uncovered["requirement_trace"] = [{"requirement_id": "R1", "disposition": "superseded", "evidence": "gone"}]
        self.rejected("astra_discovery", uncovered, "saved user event|covered another way")
        readopted = plan()
        readopted["contract"]["accepted_assumptions"] = [
            {"text": "Assumption A1 holds", "basis": "agent_proposed", "answer_id": ""}]
        self.rejected("astra_discovery", readopted, "re-adopts an assumption the user rejected")


class RemediationTests(Base):
    def setUp(self):
        super().setUp()
        self.apply("requirements_gather", requirements(assumptions=[assumption()]))
        self.remediation = self.reject("A1")
        self.apply("requirements_gather", requirements())

    def test_10_remediation_record_must_name_an_open_remediation_of_this_episode(self):
        good = record(self.state, self.remediation)
        self.rejected("astra_discovery", plan(remediation_records=[{**good, "obligation_id": "obligation-x"}]),
                      "does not name an open remediation")
        self.rejected("astra_discovery", plan(remediation_records=[{**good, "episode_id": "episode-old"}]),
                      "another clarification episode")
        self.rejected("astra_discovery", plan(remediation_records=[{**good, "covered_requirements": []}]),
                      "must cover exactly")
        self.rejected("astra_discovery", plan(remediation_records=[{**good, "evidence_refs": []}]),
                      "needs an approach and evidence")
        human = {**self.remediation, "id": "obligation-human", "kind": "human_decision"}
        self.state["deferred_obligations"].append(human)
        self.rejected("astra_discovery", clarifying([question("D2", kind="decision")], remediation_records=[
            {**good, "obligation_id": human["id"]}]), "does not name an open remediation")
        self.state["deferred_obligations"].pop()
        self.remediation.update(status="resolved")
        self.rejected("astra_discovery", plan(remediation_records=[good]), "does not name an open remediation")

    def test_11_reviewer_must_decide_every_pending_remediation_with_a_current_hash(self):
        self.apply("astra_discovery", plan(remediation_records=[record(self.state, self.remediation)]))
        self.assertEqual("pending_review", self.remediation["status"])
        self.rejected("astra_challenge", challenge(), "must decide every remediation")
        self.rejected("astra_challenge", challenge(decisions=[decision(self.remediation, remediation_hash="stale")]),
                      "stale remediation")
        self.rejected("astra_challenge", challenge(decisions=[{**decision(self.remediation),
                                                                "obligation_id": "obligation-x"}]),
                      "names no remediation awaiting review")
        concern = {"id": "P1", "concern": f"Rework {self.remediation['id']}", "evidence_refs": ["greet.py:1"],
                   "requested_change": "Reject blank names too", "acceptance_test": "Blank exits 2", "blocking": True}
        self.apply("astra_challenge", challenge([concern], [decision(self.remediation, resolved=False)]))
        response = {"concern_id": "P1", "response": "Done", "evidence_refs": ["greet.py:1"],
                    "change": "Reject blank names", "acceptance_test": "Blank exits 2"}
        self.apply("glm_revise", revise(responses=[response], remediation_records=[
            record(self.state, self.remediation, approach="Reject blank names too")]))
        self.rejected("astra_finalize", finalize(concerns=["P1"]), "must decide every remediation")
        self.rejected("astra_finalize", finalize(decisions=[decision(self.remediation, remediation_hash="stale")],
                                                 concerns=["P1"]), "stale remediation")

    def test_14_discovery_remediation_reviewed_at_challenge_then_real_task_needs_approval(self):
        self.apply("astra_discovery", plan(remediation_records=[record(self.state, self.remediation)]))
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.apply("astra_challenge", challenge(decisions=[decision(self.remediation)]))
        self.assertEqual("resolved", self.remediation["status"])
        self.assertTrue(self.remediation["resolved_by"].startswith("astra_challenge:"))
        self.apply("glm_revise", revise())
        self.apply("astra_finalize", finalize())
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertEqual("implement", self.state["goal_contract"]["body"]["initial_task"]["kind"])
        self.assertFalse(goals.approved(self.state))
        self.assertFalse([e for e in self.state["user_events"] if e.get("kind") == "goal_approval"])

    def test_15_revise_emitted_remediation_must_be_decided_at_finalize(self):
        self.apply("astra_discovery", plan())
        self.apply("astra_challenge", challenge())
        self.apply("glm_revise", revise(remediation_records=[record(self.state, self.remediation)]))
        self.assertEqual("pending_review", self.remediation["status"])
        self.rejected("astra_finalize", finalize(), "must decide every remediation")
        self.apply("astra_finalize", finalize(decisions=[decision(self.remediation)]))
        self.assertTrue(self.remediation["resolved_by"].startswith("astra_finalize:"))

    def test_16_challenge_rejects_revise_repairs_finalize_decides_within_budget(self):
        self.apply("astra_discovery", plan(remediation_records=[record(self.state, self.remediation)]))
        first_hash = self.remediation["remediation_hash"]
        no_concern = challenge(decisions=[decision(self.remediation, resolved=False)])
        self.rejected("astra_challenge", no_concern, "blocking concern naming it")
        concern = {"id": "P1", "concern": f"{self.remediation['id']} still assumes a web server",
                   "evidence_refs": ["greet.py:1"], "requested_change": "Use argv validation",
                   "acceptance_test": "Blank name exits 2", "blocking": True}
        self.apply("astra_challenge", challenge([concern], [decision(self.remediation, resolved=False)]))
        self.assertEqual("open", self.remediation["status"])
        response = {"concern_id": "P1", "response": "Switched to argv validation", "evidence_refs": ["greet.py:1"],
                    "change": "Validate argv", "acceptance_test": "Blank name exits 2"}
        self.apply("glm_revise", revise(responses=[response], remediation_records=[
            record(self.state, self.remediation, approach="Validate argv before greeting")]))
        self.assertNotEqual(first_hash, self.remediation["remediation_hash"])
        self.assertEqual("pending_review", self.remediation["status"])
        final = finalize(decisions=[decision(self.remediation)], concerns=["P1"])
        self.rejected("astra_finalize", {**final, "obligation_decisions": [
            decision(self.remediation, remediation_hash=first_hash)]}, "stale remediation")
        self.apply("astra_finalize", final)
        self.assertEqual("resolved", self.remediation["status"])
        self.assertEqual(2, self.state["planning"]["astra_calls"])

    def test_17_finalize_unresolved_needs_a_blocking_question_and_no_task(self):
        self.apply("astra_discovery", plan())
        self.apply("astra_challenge", challenge())
        self.apply("glm_revise", revise(remediation_records=[record(self.state, self.remediation)]))
        self.rejected("astra_finalize", finalize(decisions=[decision(self.remediation, resolved=False)]),
                      "must return to the user")
        ask = question(self.remediation["id"], kind="decision", category="technical")
        self.apply("astra_finalize", finalize(decisions=[decision(self.remediation, resolved=False)],
                                              questions=[ask]))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("none", self.state["goal_contract"]["body"]["initial_task"]["kind"])
        self.assertEqual("human_decision", self.remediation["kind"])
        goals.answer(self.state, self.remediation["id"], "Validate argv; no web server")
        self.assertEqual("resolved", self.remediation["status"])

    def test_unresolved_remediation_blocks_a_real_initial_task(self):
        self.apply("astra_discovery", plan())
        self.apply("astra_challenge", challenge())
        self.apply("glm_revise", revise())
        self.rejected("astra_finalize", finalize(), "block a real initial_task")


class HumanDecisionTests(Base):
    def test_18_cost_rejection_under_the_cap_blocks_a_real_plan_until_the_user_decides(self):
        self.apply("requirements_gather", requirements(assumptions=[assumption(category="behavior")]))
        cost = {"id": "A2", "text": "Use the paid tier", "kind": "decision", "category": "cost", "supports": ["R1"]}
        self.state["requirements_handoff"]["report"]["proposed_assumptions"].append(cost)
        three = [question(f"D{i}", kind="decision", category="behavior") for i in range(1, 4)]
        self.apply("astra_discovery", clarifying(three))
        obligation = goals.reject_assumption(self.state, "A2")
        self.assertEqual("human_decision", obligation["kind"])
        self.apply("requirements_gather", requirements(assumptions=[assumption(category="behavior")]))
        self.rejected("astra_discovery", plan(), "blocks a real plan")
        self.rejected("astra_discovery", plan(remediation_records=[record(self.state, obligation)]),
                      "blocks a real plan")
        self.apply("astra_discovery", clarifying(three))
        self.assertEqual(4, len(self.state["pending_questions"]))
        self.assertIn(obligation["id"], [row["id"] for row in self.state["pending_questions"]])
        with self.assertRaisesRegex(ValueError, obligation["id"]):
            goals.delegate_all(self.state)
        for row in three:
            goals.answer(self.state, row["id"], "Yes")
        self.assertEqual("open", obligation["status"])
        goals.answer(self.state, obligation["id"], "Stay on the free tier")
        self.assertEqual("resolved", obligation["status"])
        self.apply("astra_discovery", plan())
        self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_feedback_citing_the_obligation_discharges_it(self):
        self.apply("requirements_gather", requirements(assumptions=[
            {"id": "A2", "text": "Use the paid tier", "kind": "decision", "category": "cost", "supports": []}]))
        obligation = self.reject("A2")
        self.state["status"] = "WAITING_FOR_USER"
        goals.feedback(self.state, f"Re {obligation['id']}: free tier only")
        self.assertEqual("resolved", obligation["status"])


class ApprovalTests(Base):
    def test_12_new_commands_never_approve_and_unresolved_obligations_block_approval(self):
        self.apply("requirements_gather", requirements(assumptions=[assumption()]))
        remediation = self.reject("A1")
        self.assertIsNone(self.state["goal_contract"]["approval_event"])
        self.assertFalse([e for e in self.state["user_events"] if e.get("kind") == "goal_approval"])
        self.apply("requirements_gather", requirements())
        self.apply("astra_discovery", plan())
        self.apply("astra_challenge", challenge())
        self.apply("glm_revise", revise())
        # A finalize that sneaks past the gate still cannot be approved or executed.
        contract = self.state["goal_contract"]
        contract["body"]["initial_task"] = finalize()["contract"]["initial_task"]
        contract["hash"] = support.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        self.state.update(status="AWAITING_GOAL_APPROVAL")
        self.state["planning"]["final_token"] = goals.token(self.state["goal_contract"])
        goals.present(self.state)
        with self.assertRaisesRegex(ValueError, "Unresolved assumption rejections"):
            goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual("open", remediation["status"])

    def test_13_legacy_run_loads_and_keeps_its_approval(self):
        legacy = state()
        legacy["settings"]["joint_planning"] = False
        approve_fixture(legacy, goals)
        token = goals.token(legacy["goal_contract"])
        self.assertNotIn("clarification_episode", legacy)
        self.assertNotIn("deferred_obligations", legacy)
        goals.execution_guard(legacy)
        self.assertTrue(goals.approved(legacy))
        self.assertEqual(token, goals.token(legacy["goal_contract"]))
        report = requirements(assumptions=["Use a local CLI"])
        del report["source_refs"]
        report["source_refs"] = []
        schema = planner.SCHEMAS["requirements_gather"]
        support.validate_schema({k: v for k, v in report.items()}, schema)
        self.assertEqual([], goals.unresolved_obligations(legacy))


if __name__ == "__main__":
    unittest.main()
