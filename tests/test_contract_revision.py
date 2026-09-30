"""Proof corrections in an unapproved plan do not change the agreed behavior."""
import copy
import unittest

from autocode_goals import revision_guard


class DraftVerificationRevisionTests(unittest.TestCase):
    def inputs(self, **contract_fields):
        body = {"acceptance_criteria": [{"id": "AC5", "criterion": "The complete project suite passes.",
                 "verification_method": "test: test_ac5_recursive_suite", "human_review": False}],
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
