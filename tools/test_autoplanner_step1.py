"""AutoPlanner step 1: question/assumption classification schema, requirement
preservation on excluded/superseded and on a refreshed handoff, and the
--delegate-all / --reject-assumption mutators. See the design discussion on
GitHub issue #62 for the full rationale; this covers only the schema,
validator and CLI-mutator layer (no obligation-discharge gating yet)."""
import unittest

from . import autocode_goals as goals
from .goal_fixtures import body
from .test_planner_invariants import state


class QuestionSchemaTests(unittest.TestCase):
    def test_kind_category_delegable_are_optional_on_question(self):
        self.assertEqual(["id", "question", "why", "options", "proposed_default"], goals.QUESTION["required"])
        self.assertIn("kind", goals.QUESTION["properties"])
        self.assertIn("category", goals.QUESTION["properties"])
        self.assertIn("delegable", goals.QUESTION["properties"])
        self.assertEqual(["discoverable", "inferable", "decision"], goals.QUESTION["properties"]["kind"]["enum"])


class AssumptionValidationTests(unittest.TestCase):
    def test_legacy_string_assumption_normalizes_without_structural_checks(self):
        rows = goals.validate_assumptions(["Use a CLI"], known_requirement_ids=set())
        self.assertEqual([{"id": None, "text": "Use a CLI", "kind": "inferable", "category": "requested_outcome",
                           "convention_ref": "", "rationale": "", "supports": [], "legacy": True}], rows)

    def test_inferable_assumption_needs_convention_ref_and_rationale(self):
        base = {"id": "A1", "text": "A CLI is sufficient", "kind": "inferable", "category": "technical", "supports": []}
        with self.assertRaisesRegex(ValueError, "convention_ref"):
            goals.validate_assumptions([{**base, "convention_ref": "", "rationale": ""}], set())
        with self.assertRaisesRegex(ValueError, "rationale"):
            goals.validate_assumptions([{**base, "convention_ref": "tools/x.py:10", "rationale": ""}], set())
        rows = goals.validate_assumptions(
            [{**base, "convention_ref": "tools/x.py:10", "rationale": "Existing CLI convention"}], set())
        self.assertEqual("A1", rows[0]["id"])
        self.assertFalse(rows[0]["legacy"])

    def test_non_inferable_category_cannot_be_marked_inferable(self):
        row = {"id": "A1", "text": "Prefer the cheaper model", "kind": "inferable", "category": "cost",
               "convention_ref": "tools/x.py:1", "rationale": "r", "supports": []}
        with self.assertRaisesRegex(ValueError, "cannot be inferable"):
            goals.validate_assumptions([row], set())

    def test_assumption_cannot_support_an_unknown_requirement(self):
        row = {"id": "A1", "text": "x", "kind": "decision", "category": "cost", "supports": ["R9"]}
        with self.assertRaisesRegex(ValueError, "unknown requirement"):
            goals.validate_assumptions([row], {"R1"})

    def test_duplicate_assumption_id_is_rejected(self):
        rows = [{"id": "A1", "text": "x", "kind": "decision", "category": "cost", "supports": []},
                {"id": "A1", "text": "y", "kind": "decision", "category": "cost", "supports": []}]
        with self.assertRaisesRegex(ValueError, "Duplicate assumption id"):
            goals.validate_assumptions(rows, set())


class RequirementPreservationTests(unittest.TestCase):
    def test_excluded_requirement_needs_more_than_a_scope_exclusion_match(self):
        current = state()
        current["requirements_handoff"] = {
            "report": {"requirements": [{"id": "R1", "text": "Print greeting", "source_quote": "Print greeting"}]},
            "output": "x.json"}
        contract = body()
        contract["scope_exclusions"] = ["Print greeting"]
        trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": "Print greeting"}]
        with self.assertRaisesRegex(ValueError, "cannot be excluded without a saved user event"):
            goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)

    def test_excluded_requirement_with_a_saved_feedback_citation_is_accepted(self):
        current = state()
        current["requirements_handoff"] = {
            "report": {"requirements": [{"id": "R1", "text": "Print greeting", "source_quote": "Print greeting"}]},
            "output": "x.json"}
        evidence = "Print greeting; dropped per feedback-1"
        current["brief_feedback"] = [{"id": "feedback-1", "text": "Drop the greeting requirement"}]
        current["user_events"] = list(current["brief_feedback"])
        contract = body()
        contract["scope_exclusions"] = [evidence]
        trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": evidence}]
        goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)  # does not raise

    def test_refreshed_handoff_dropping_a_requirement_without_citation_is_rejected(self):
        current = state("Print greeting. Reject empty input.")
        current["requirements_handoff"] = {"report": {"requirements": [
            {"id": "R1", "text": "Print a greeting", "source_quote": "Print greeting."},
            {"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}]}, "output": "prev.json"}
        new_report = {"requirements": [{"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}],
                      "ignored_statements": [], "proposed_assumptions": [], "open_questions": []}
        with self.assertRaisesRegex(ValueError, "dropped requirement R1"):
            goals.check_requirement_handoff(current, new_report)

    def test_refreshed_handoff_dropping_a_requirement_with_a_saved_citation_is_accepted(self):
        current = state("Print greeting. Reject empty input.")
        current["requirements_handoff"] = {"report": {"requirements": [
            {"id": "R1", "text": "Print a greeting", "source_quote": "Print greeting."},
            {"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}]}, "output": "prev.json"}
        current["brief_feedback"] = [{"id": "feedback-1", "text": "Drop the greeting requirement"}]
        current["user_events"] = list(current["brief_feedback"])
        new_report = {"requirements": [{"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}],
                      "ignored_statements": [], "proposed_assumptions": [], "open_questions": [],
                      "ignored_requirements": [{"requirement_id": "R1", "reason": "User asked to drop it",
                                                "basis": "user_feedback", "event_id": "feedback-1"}]}
        goals.check_requirement_handoff(current, new_report)  # does not raise

    def test_refreshed_handoff_omission_with_an_unresolvable_event_is_rejected(self):
        current = state("Print greeting. Reject empty input.")
        current["requirements_handoff"] = {"report": {"requirements": [
            {"id": "R1", "text": "Print a greeting", "source_quote": "Print greeting."},
            {"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}]}, "output": "prev.json"}
        new_report = {"requirements": [{"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}],
                      "ignored_statements": [], "proposed_assumptions": [], "open_questions": [],
                      "ignored_requirements": [{"requirement_id": "R1", "reason": "Agent decided it was unnecessary",
                                                "basis": "user_feedback", "event_id": "feedback-nonexistent"}]}
        with self.assertRaisesRegex(ValueError, "needs a saved user answer or feedback event"):
            goals.check_requirement_handoff(current, new_report)


def shown(state):
    state.setdefault("status", "WAITING_FOR_USER")  # hand-built fixtures; rendering needs a status
    goals.present(state)
    return goals.token(state["goal_contract"])


def _contract(**overrides):
    contract = {"task_id": "t", "revision": 1, "body": {"open_blocking_questions": []}, "hash": "h",
               "approval_status": "draft", "approval_event": None}
    contract.update(overrides)
    return contract


class DelegateAllTests(unittest.TestCase):
    def test_blocks_atomically_when_any_pending_question_cannot_be_bulk_delegated(self):
        current = state()
        current["goal_contract"] = _contract()
        current["pending_questions"] = [
            {"id": "Q1", "question": "q1", "why": "w", "options": [], "proposed_default": "yes", "delegable": True},
            {"id": "Q2", "question": "q2", "why": "w", "options": [], "proposed_default": "", "delegable": True}]
        with self.assertRaisesRegex(ValueError, "Q2"):
            goals.delegate_all(current, shown(current))
        self.assertEqual({}, current.get("answers", {}))

    def test_blocks_when_delegable_is_absent_even_with_a_default(self):
        current = state()
        current["goal_contract"] = _contract()
        current["pending_questions"] = [
            {"id": "Q1", "question": "q1", "why": "w", "options": [], "proposed_default": "yes"}]
        with self.assertRaisesRegex(ValueError, "Q1"):
            goals.delegate_all(current, shown(current))

    def test_delegates_every_pending_question_and_invalidates_approval(self):
        current = state()
        question = {"id": "Q1", "question": "q1", "why": "w", "options": [], "proposed_default": "yes",
                    "delegable": True, "category": "technical"}
        current["goal_contract"] = _contract(body={"open_blocking_questions": [question]},
                                             approval_status="approved", approval_event={"kind": "goal_approval"})
        current["pending_questions"] = [question]
        goals.delegate_all(current, shown(current))
        self.assertEqual("yes", current["answers"]["Q1"]["text"])
        self.assertEqual("delegated", current["answers"]["Q1"]["kind"])
        self.assertEqual([], current["pending_questions"])
        self.assertEqual("draft", current["goal_contract"]["approval_status"])
        self.assertIsNone(current["goal_contract"]["approval_event"])


class RejectAssumptionTests(unittest.TestCase):
    def test_cost_category_becomes_a_human_decision_obligation(self):
        current = state()
        current["status"] = "WAITING_FOR_USER"
        current["goal_contract"] = _contract(approval_status="approved", approval_event={"kind": "goal_approval"})
        current["requirements_handoff"] = {"report": {"proposed_assumptions": [
            {"id": "A1", "text": "Use the cheaper model", "kind": "inferable", "category": "cost",
             "convention_ref": "", "rationale": "", "supports": ["R1"]}]}, "output": "x.json"}
        obligation = goals.reject_assumption(current, "A1", shown(current))
        self.assertEqual("human_decision", obligation["kind"])
        self.assertEqual(["R1"], obligation["supports"])
        self.assertEqual("reject_assumption", current["user_events"][-1]["kind"])
        self.assertEqual("draft", current["goal_contract"]["approval_status"])
        self.assertIsNone(current["goal_contract"]["approval_event"])
        # Rejection moved status to RUNNING; simulate reaching a later checkpoint
        # with the same handoff still current, and confirm the duplicate is caught.
        current["status"] = "WAITING_FOR_USER"
        with self.assertRaisesRegex(ValueError, "already has an open rejection"):
            goals.reject_assumption(current, "A1", shown(current))

    def test_technical_category_becomes_a_remediation_obligation(self):
        current = state()
        current["status"] = "AWAITING_GOAL_APPROVAL"
        current["goal_contract"] = _contract()
        current["requirements_handoff"] = {"report": {"proposed_assumptions": [
            {"id": "A2", "text": "A CLI is sufficient", "kind": "inferable", "category": "technical",
             "convention_ref": "tools/x.py:1", "rationale": "existing pattern", "supports": []}]}, "output": "x.json"}
        obligation = goals.reject_assumption(current, "A2", shown(current))
        self.assertEqual("remediation", obligation["kind"])
        self.assertEqual([], obligation["supports"])

    def test_legacy_or_unknown_assumption_id_cannot_be_rejected(self):
        current = state()
        current["status"] = "WAITING_FOR_USER"
        current["goal_contract"] = _contract()
        current["requirements_handoff"] = {"report": {"proposed_assumptions": ["Use a local CLI"]}, "output": "x.json"}
        with self.assertRaisesRegex(ValueError, "Unknown or legacy"):
            goals.reject_assumption(current, "A1", shown(current))

    def test_rejection_requires_an_open_conversation_checkpoint(self):
        current = state()
        current["status"] = "RUNNING"
        current["goal_contract"] = _contract()
        current["requirements_handoff"] = {"report": {"proposed_assumptions": [
            {"id": "A1", "text": "x", "kind": "inferable", "category": "technical",
             "convention_ref": "tools/x.py:1", "rationale": "r", "supports": []}]}, "output": "x.json"}
        with self.assertRaisesRegex(ValueError, "open conversation checkpoint"):
            goals.reject_assumption(current, "A1", shown(current))


if __name__ == "__main__":
    unittest.main()
