"""Saved requirement references preserve provenance without model transcription."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import autocode as runner
from autocode_report_schema import review_generation_schema, review_validation_schema, hydrate_review_report
from autocode_util import validate_schema


class RequirementsReportTests(unittest.TestCase):
    def setUp(self):
        self.schema = {"type": "object", "additionalProperties": False,
                       "required": ["requirements"], "properties": {"requirements": {
                           "type": "array", "items": {"type": "object", "additionalProperties": False,
                           "required": ["id", "text", "source_quote"], "properties": {
                               key: {"type": "string"} for key in ("id", "text", "source_quote")}}}}}
        self.saved = [{"id": "R1", "text": "Keep original text", "source_quote": "partial quote"}]
        self.state = {"requirements_handoff": {"report": {"requirements": self.saved}}}
        self.report = {"requirements": [{"id": "R1"},
                       {"id": "R2", "text": "New intent", "source_quote": "new saved answer"}]}
        self.record = {"stage": "requirements_gather"}

    def test_generation_advertises_existing_ids_and_keeps_new_requirement_fields(self):
        before = copy.deepcopy((self.schema, self.state))
        bound = review_generation_schema(self.schema, self.state, "requirements_gather")
        validate_schema(self.report, bound)
        item = bound["properties"]["requirements"]["items"]
        self.assertEqual(["id"], item["required"])
        self.assertIn("R1", item["description"])
        self.assertIn("source_quote", item["properties"])
        self.assertEqual(before, (self.schema, self.state))

    def test_initial_and_other_stage_schemas_are_unchanged(self):
        self.assertEqual(self.schema, review_generation_schema(self.schema, {}, "requirements_gather"))
        self.assertEqual(self.schema, review_generation_schema(self.schema, self.state, "astra_discovery"))
        self.assertEqual(self.report, hydrate_review_report(self.report, self.state, {"stage": "astra_discovery"}))

    def test_validation_accepts_reference_and_full_rows_against_legacy_schema(self):
        schema = review_validation_schema(self.schema, self.state, self.record, self.report)
        validate_schema(self.report, schema)
        for report in ({"requirements": copy.deepcopy(self.saved)}, {"requirements": []}):
            validate_schema(report, review_validation_schema(self.schema, self.state, self.record, report))

    def test_validation_rejects_unknown_duplicate_partial_and_malformed_references(self):
        cases = [[{"id": "UNKNOWN"}], [{"id": "R1"}, {"id": "R1"}],
                 [{"id": "R1", "text": "changed"}], [{"id": "R1", "source_quote": "changed"}],
                 [{"id": "R2", "text": "new"}], [{"id": "R1", "extra": "hidden"}], ["R1"], [{}]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                value = {"requirements": rows}
                schema = review_validation_schema(self.schema, self.state, self.record, value)
                validate_schema(value, schema)

    def test_hydration_copies_only_references_and_never_repairs_explicit_quote(self):
        before = copy.deepcopy((self.report, self.state))
        hydrated = hydrate_review_report(self.report, self.state, self.record)
        self.assertEqual(self.saved[0], hydrated["requirements"][0])
        self.assertEqual(self.report["requirements"][1], hydrated["requirements"][1])
        self.assertEqual(before, (self.report, self.state))
        explicit = {"requirements": [{**self.saved[0], "source_quote": "rewritten quote"}]}
        self.assertEqual(explicit, hydrate_review_report(explicit, self.state, self.record))

    def test_loader_preserves_raw_reference_and_saves_canonical_for_normal_and_repair(self):
        for repair in (False, True):
            with self.subTest(repair=repair), tempfile.TemporaryDirectory() as directory:
                output, schema = Path(directory) / "report.json", Path(directory) / "schema.json"
                output.write_text(json.dumps(self.report))
                schema.write_text(json.dumps(self.schema))
                record = {**self.record, "output": str(output), "schema": str(schema)}
                if repair:
                    record.update(stage="requirements_gather_report_repair", original_stage="requirements_gather")
                result = runner.load_stage_report(record, state=self.state)
                self.assertEqual(self.saved[0], result["requirements"][0])
                self.assertEqual(result, json.loads(output.read_text()))
                self.assertEqual(self.report, json.loads(Path(record["reported_output"]).read_text()))
                self.assertEqual(result, runner.load_stage_report(record, state=self.state))

    def test_canonical_report_still_rejects_explicit_quote_change_and_unapproved_omission(self):
        import autocode_goals as goals
        state = {**self.state, "task": "partial quote and rewritten quote"}
        for raw in ({"requirements": []}, {"requirements": [{**self.saved[0], "source_quote": "rewritten quote"}]}):
            canonical = hydrate_review_report(raw, state, self.record)
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "without a user-backed omission"):
                goals.check_requirement_handoff(state, canonical)


class RequirementsLaunchTests(unittest.TestCase):
    from .test_autocode import RetrofitTest
    setUp = RetrofitTest.setUp

    def test_normal_and_repair_launch_advertise_references_without_overwriting_saved_schema(self):
        self.state["next_stage"] = "requirements_gather"
        self.state["settings"]["roles"]["requirements"] = {"model": "model-requirements", "reasoning_effort": "high"}
        self.state["requirements_handoff"] = {"report": {"requirements": [
            {"id": "R1", "text": "Keep", "source_quote": "Keep"}]}}
        schema = self.run / "requirements.schema.json"
        schema.write_text(json.dumps(runner.planning.SCHEMAS["requirements_gather"]))
        before = schema.read_bytes()
        for repair in (False, True):
            with self.subTest(repair=repair):
                _, record = runner.run_role(role="requirements", prompt="Gather", sandbox="read-only",
                    workspace=self.root, run_dir=self.run, state=self.state, schema=schema,
                    model="model-glm", allow_write=False, dry_run=True, report_only=repair)
                bound = json.loads(Path(record["schema"]).read_text())
                self.assertEqual(["id"], bound["properties"]["requirements"]["items"]["required"])
                self.assertEqual(before, schema.read_bytes())
