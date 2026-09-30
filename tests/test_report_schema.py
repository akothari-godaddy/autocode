"""Approved criterion literals must also guide review report generation."""
import copy
import json
from pathlib import Path
import unittest

from autocode_report_schema import review_generation_schema
from autocode_util import validate_schema


class ReviewReportSchemaTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/astra-decision.schema.json"
        self.schema = json.loads(path.read_text())
        self.criterion = "Given three pending events, when pending(2) runs, then exactly two events are returned."
        self.state = {"acceptance_criteria": [{"id": "G2", "criterion": self.criterion}]}
        self.report = {"status": "TASK_COMPLETE", "acceptance_criteria": [
            {"id": "G2", "criterion": self.criterion, "status": "verified", "evidence": "event:check"}],
            "evidence": ["event:check"], "next_objective": "", "blocker": "", "plan": [], "affected_paths": []}

    def test_completion_and_checkpoint_accept_saved_literals_without_mutating_inputs(self):
        before = copy.deepcopy((self.schema, self.state))
        for stage in ("astra_review", "astra_checkpoint"):
            validate_schema(self.report, review_generation_schema(self.schema, self.state, stage))
        self.assertEqual(before, (self.schema, self.state))

    def test_review_generation_rejects_a_retyped_word_or_an_unknown_criterion_id(self):
        schema = review_generation_schema(self.schema, self.state, "astra_review")
        for key, value in (("criterion", self.criterion.replace("pending events", "pending events for orders")),
                           ("id", "unknown")):
            with self.subTest(key=key):
                report = copy.deepcopy(self.report)
                report["acceptance_criteria"][0][key] = value
                with self.assertRaisesRegex(ValueError, rf"acceptance_criteria\[0\].{key}: invalid enum"):
                    validate_schema(report, schema)

    def test_planning_and_builder_schemas_are_not_bound_to_review_literals(self):
        changed = copy.deepcopy(self.report)
        changed["acceptance_criteria"][0]["criterion"] = "A new draft criterion"
        for stage in ("astra_discovery", "glm_revise", "terra"):
            validate_schema(changed, review_generation_schema(self.schema, self.state, stage))
