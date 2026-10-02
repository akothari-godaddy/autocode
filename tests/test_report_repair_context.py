"""The report repair handoff retains human clarification provenance."""
import unittest
import copy

import autocode_report_repair_context as context


class ClarificationContextTests(unittest.TestCase):
    def test_revision_and_finalization_repairs_get_only_the_current_planning_exchange(self):
        reports = {
            "astra_discovery": {"output": "current-discovery.json", "report": {"contract": {"scope": ["greet.py"]}}},
            "astra_challenge": {"output": "current-review.json", "report": {"concerns": [{
                "id": "C1", "concern": "Guard test is missing", "requested_change": "Plan the guard test"}]}},
            "glm_revise": {"output": "current-revision.json", "report": {"responses": [{"concern_id": "C1"}]}},
            "astra_finalize": {"report": {"summary": "unrelated final"}},
        }
        state = {"planning": {"reports": reports},
                 "planning_history": [{"reports": {"astra_challenge": {"report": {"concerns": [{"id": "OLD"}]}}}}]}
        before = copy.deepcopy(state)
        for stage, names in (("glm_revise", ("astra_discovery", "astra_challenge")),
                             ("astra_finalize", ("astra_discovery", "astra_challenge", "glm_revise"))):
            with self.subTest(stage=stage):
                result = context.clarification_context(state, stage)
                self.assertEqual({name: reports[name] for name in names}, result["planning_exchange"])
                result["planning_exchange"]["astra_challenge"]["report"]["concerns"][0]["id"] = "changed"
                self.assertEqual(before, state)
        self.assertEqual({}, context.clarification_context(state, "requirements_gather")["planning_exchange"])
        self.assertEqual({}, context.clarification_context({}, "glm_revise")["planning_exchange"])

    def test_repair_instruction_requires_actual_saved_concern_ids_and_substantive_responses(self):
        text = context.instruction("glm_revise")
        self.assertIn("planning_exchange.astra_challenge.report.concerns", text)
        self.assertIn("every saved concern ID exactly once", text)
        self.assertIn("Do not invent a replacement concern ID", text)
        self.assertIn("existing evidence_refs", text)

    def test_planning_repair_gets_only_matching_answers_and_current_investigation(self):
        question = {"id": "Q1", "question": "Which behavior?"}
        handoff = {"report": {"open_questions": [question], "requirements": [
            {"id": "preserve", "source_quote": "Preserve current output", "text": "Keep it stable."}]}}
        request = {"stage": "requirements_gather", "questions": [question],
                   "question_ids": ["Q1"], "handoff_hash": "h1"}
        state = {
            "requirements_handoff": handoff,
            "investigation_request": request,
            "clarification_episode": {"id": "episode-1", "investigation_used": True},
            "answers": {"Q1": {"text": "Use option A"}, "Q9": {"text": "Unrelated"}},
            "brief_feedback": [{"id": "feedback-1", "actor": "user_cli", "text": "Keep output stable",
                                "contract_token": "private-token"}],
        }

        result = context.clarification_context(state, "requirements_gather")

        self.assertEqual(handoff, result["requirements_handoff"])
        self.assertEqual(request, result["investigation_request"])
        self.assertEqual(state["clarification_episode"], result["clarification_episode"])
        self.assertEqual({"preserve": "Preserve current output"}, result["required_source_quotes"])
        self.assertEqual({"Q1": state["answers"]["Q1"]}, result["saved_answers"])
        self.assertEqual([{"id": "feedback-1", "actor": "user_cli", "text": "Keep output stable"}],
                         result["saved_feedback"])
        self.assertNotIn("Q9", result["saved_answers"])

    def test_human_clarification_is_not_a_machine_resolution(self):
        text = context.instruction("astra_discovery")
        self.assertIn("Human answers and feedback are never machine_resolutions", text)
        self.assertIn("only when clarification_context has an investigation_request", text)
        self.assertIn("Without that matching request, machine_resolutions and access_blockers must be []", text)
        self.assertIn("Historical resolutions in a prior handoff are not new resolutions", text)
        self.assertIn("ignored_statements using its exact checklist text", text)
        self.assertIn("Do not expand a prior source_quote to cover a checklist sentence", text)

    def test_nonplanning_report_repair_has_no_clarification_payload_or_rule(self):
        state = {"requirements_handoff": {"sensitive": "unrelated"}}
        self.assertEqual({}, context.clarification_context(state, "terra"))
        self.assertEqual("", context.instruction("terra"))

    def test_original_planning_draft_is_not_an_immutable_answer_baseline(self):
        text = context.baseline_instruction("requirements_gather")
        self.assertIn("original_report is historical planning context", text)
        self.assertIn("Do not restore invalid machine_resolutions", text)
        self.assertNotIn("immutable execution-history baseline", text)
        self.assertIn("immutable execution-history baseline", context.baseline_instruction("terra"))


if __name__ == "__main__":
    unittest.main()
