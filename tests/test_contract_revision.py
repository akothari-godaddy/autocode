"""Proof corrections in an unapproved plan do not change the agreed behavior."""
import copy
import unittest

from autocode_goals import revision_guard


class DraftVerificationRevisionTests(unittest.TestCase):
    def inputs(self, **contract_fields):
        body = {"acceptance_criteria": [{"id": "AC5", "criterion": "The complete project suite passes.",
                 "verification_method": "Run python3 -m unittest nonexistent_test.py", "human_review": False}],
                "required_behaviors": ["Preserve the current output exactly"], "scope_exclusions": [],
                "constraints": [], "important_failure_cases": [], "permission_boundaries": ["No network"]}
        contract = {"body": body, "approval_status": "draft", "approval_event": None, **contract_fields}
        state = {"goal_contract": contract}
        after = copy.deepcopy(body)
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover -s tests -t ."
        return state, after

    def test_unapproved_proof_can_be_corrected_without_changing_the_behavior(self):
        state, after = self.inputs()
        before = copy.deepcopy(state)
        revision_guard(state, after, [], "glm_revise")
        self.assertEqual(before, state)

    def test_a_declared_agent_proof_correction_is_accepted_before_approval(self):
        state, after = self.inputs()
        changes = [{"item": "AC5", "change": "reworded", "basis": "agent_proposed", "answer_id": "",
                    "replacement": after["acceptance_criteria"][0]["verification_method"]}]
        revision_guard(state, after, changes, "astra_finalize")

    def test_current_or_invalidated_user_approval_keeps_verification_protected(self):
        for fields in ({"approval_status": "approved"},
                       {"approval_status": "draft", "approval_event": {"kind": "goal_approval"}}):
            with self.subTest(fields=fields):
                state, after = self.inputs(**fields)
                with self.assertRaisesRegex(ValueError, "without a user-backed"):
                    revision_guard(state, after, [], "glm_revise")

    def test_clearing_the_current_receipt_does_not_erase_saved_user_approval(self):
        state, after = self.inputs()
        state["user_events"] = [{"kind": "goal_approval", "token": "r1:previously-approved"}]
        state["contract_history"] = [dict(copy.deepcopy(state["goal_contract"]), revision=1, hash="previously-approved")]
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            revision_guard(state, after, [], "glm_revise")

    def test_proof_correction_does_not_authorize_a_changed_behavior_or_permission(self):
        for change in ("behavior", "permission", "required_behavior", "remove", "human_review"):
            with self.subTest(change=change):
                state, after = self.inputs()
                if change == "behavior":
                    after["acceptance_criteria"][0]["criterion"] = "Only one test must pass"
                elif change == "permission":
                    after["permission_boundaries"] = ["Network allowed"]
                elif change == "required_behavior":
                    after["required_behaviors"] = []
                elif change == "human_review":
                    after["acceptance_criteria"][0]["human_review"] = True
                else:
                    after["acceptance_criteria"] = []
                with self.assertRaisesRegex(ValueError, "without a user-backed"):
                    revision_guard(state, after, [], "glm_revise")

    def test_unrelated_prior_turn_approval_does_not_freeze_a_new_draft(self):
        state, after = self.inputs()
        state["user_events"] = [{"kind": "goal_approval", "token": "r1:old"}]
        old = copy.deepcopy(state["goal_contract"])
        old.update(revision=1, hash="old")
        old["body"]["acceptance_criteria"][0]["id"] = "PREVIOUS"
        state["contract_history"] = [old]
        revision_guard(state, after, [], "glm_revise")

    def test_user_set_methods_remain_protected(self):
        for origin in ("user_cli_edit", "user_answer", "user_feedback"):
            state, after = self.inputs()
            prior = copy.deepcopy(state["goal_contract"])
            prior["origin"] = origin
            event = {"id": "F1", "kind": "brief_feedback", "text": "Use this proof"}
            state.update(answers={"Q1": {}}, brief_feedback=[event], user_events=[event])
            if origin != "user_cli_edit":
                prior["declared_changes"] = [{"item": "AC5", "basis": origin,
                                              "answer_id": "Q1" if origin == "user_answer" else "F1"}]
            state["contract_history"] = [prior]
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def test_runner_enforced_proof_cannot_be_replaced_by_prose(self):
        for mark in ("test:", "guard:"):
            state, after = self.inputs()
            state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = mark + " test_ac5_behavior"
            with self.subTest(mark=mark), self.assertRaises(ValueError):
                revision_guard(state, after, [], "glm_revise")

    def test_approved_human_review_cannot_be_removed_with_unchanged_proof(self):
        state, _ = self.inputs(approval_status="approved")
        before = state["goal_contract"]["body"]
        before["acceptance_criteria"][0]["human_review"] = True
        after = copy.deepcopy(before)
        after["acceptance_criteria"][0]["human_review"] = False
        with self.assertRaises(ValueError):
            revision_guard(state, after, [], "glm_revise")

    def test_an_explicit_user_proof_change_is_consumed_once(self):
        state, after = self.inputs()
        state["answers"] = {"Q1": {"text": "Use this proof"}}
        revision_guard(state, after, [{"item": "AC5", "change": "reworded", "basis": "user_answer",
                       "answer_id": "Q1", "replacement": after["acceptance_criteria"][0]["verification_method"]}], "glm_revise")

    def test_draft_can_add_human_review_but_approved_or_user_set_review_stays_protected(self):
        for kind in ("draft", "approved", "user_cli_edit", "previously_approved"):
            state, _ = self.inputs(approval_status="approved" if kind == "approved" else "draft")
            if kind == "user_cli_edit":
                state["goal_contract"]["origin"] = "user_cli_edit"
            if kind == "previously_approved":
                prior = dict(copy.deepcopy(state["goal_contract"]), revision=1, hash="old", approval_status="approved")
                state["contract_history"] = [prior]
            after = copy.deepcopy(state["goal_contract"]["body"])
            after["acceptance_criteria"][0]["human_review"] = True
            with self.subTest(kind=kind):
                if kind == "draft":
                    revision_guard(state, after, [], "glm_revise")
                else:
                    with self.assertRaisesRegex(ValueError, "without a user-backed"):
                        revision_guard(state, after, [], "glm_revise")
