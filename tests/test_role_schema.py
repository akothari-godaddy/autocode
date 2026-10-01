"""Cycle-free role schema extraction preserves ordinary reports."""
import copy
import json
from pathlib import Path
import unittest

import autocode_goals as goals
import autocode_role_schema as reports
import autocode_util as util


class RoleSchemaTests(unittest.TestCase):
    def legacy(self):
        return {"type": "object", "additionalProperties": False,
                "required": ["status"], "properties": {"status": {"type": "string", "enum": ["CONTINUE"]}}}

    def test_existing_schema_api_is_preserved_and_input_is_not_mutated(self):
        self.assertIs(goals.role_schema, reports.role_schema)
        self.assertIs(goals.USER_REQUEST, reports.USER_REQUEST)
        schema = self.legacy()
        before = copy.deepcopy(schema)
        result = goals.role_schema(schema, "astra")
        self.assertEqual(before, schema)
        self.assertIn("COMPLETE", result["properties"]["status"]["enum"])
        self.assertNotIn("progressive_checkpoint", result["properties"])
        self.assertNotIn("progressive_checkpoint", result["required"])

    def test_ordinary_report_remains_valid_without_new_fields(self):
        schema = goals.role_schema(self.legacy(), "astra")
        value = {"status": "CONTINUE", "contract_revision": 1, "contract_hash": "hash", "task_id": "T1",
                 "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                                  "options": [], "proposed_delta": ""}, "deferred_backlog": [],
                 "next_task": {"kind": "none", "milestone_id": "", "requirements": [],
                               "acceptance_criteria": [], "validation_plan": []}, "agreed_limitations": []}
        util.validate_schema(value, schema)
        with self.assertRaises(ValueError):
            util.validate_schema({**value, "progressive_checkpoint": True}, schema)

    def test_builder_and_validator_schema_extensions_do_not_mutate_legacy(self):
        root = Path(__file__).resolve().parents[1] / "tools" / "autocode-schemas" / "v2"
        for role in ("terra", "sol"):
            with self.subTest(role=role):
                legacy = json.loads((root / f"{role}-report.schema.json").read_text())
                before = copy.deepcopy(legacy)
                result = reports.role_schema(legacy, role)
                self.assertEqual(before, legacy)
                for key in ("contract_revision", "contract_hash", "task_id", "user_request", "deferred_backlog"):
                    self.assertIn(key, result["required"])
                if role == "terra":
                    for key in ("addressed_requirements", "untested_behavior", "recommended_checks"):
                        self.assertIn(key, result["required"])
                else:
                    self.assertIn("end_to_end_result", result["required"])
                    self.assertIn("blocking", result["properties"]["findings"]["items"]["required"])
                    self.assertEqual(["PASS", "FAIL", "NOT_VERIFIED"],
                                     result["properties"]["criterion_results"]["items"]["properties"]["status"]["enum"])

    def test_product_permission_request_enum_is_unchanged(self):
        self.assertEqual(["none", "clarification", "contradiction", "infeasible", "permission", "goal_change", "blocker"],
                         reports.USER_REQUEST["properties"]["kind"]["enum"])
