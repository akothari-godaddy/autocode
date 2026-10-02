"""Validator prompts distinguish shell receipts from inner application results."""
import unittest

import autocode_validator_evidence_instructions as instructions


class ValidatorEvidenceInstructionsTests(unittest.TestCase):
    def test_execution_uses_direct_asserting_checks_and_exact_outer_receipts(self):
        text = instructions.instruction()
        for fragment in ("checks[].command", "entire outer shell command", "workdir",
                         "cd", "tee", "echo", "outer shell event", "subprocess",
                         "assert", "nonzero", "completed shell event"):
            self.assertIn(fragment, text)
        self.assertNotIn("original_executed_checks", text)

    def test_report_repair_copies_saved_receipts_without_rerunning_or_reinterpreting(self):
        text = instructions.instruction(report_only=True)
        for fragment in ("original_executed_checks", "command", "exit_code", "evidence_ref",
                         "verbatim", "outer shell event", "Do not rerun"):
            self.assertIn(fragment, text)
        self.assertNotIn("Prefer direct checks", text)


if __name__ == "__main__":
    unittest.main()
