"""Pure product-check retention without runtime writers or replay integration."""
from copy import deepcopy
import unittest

import autocode_verification_plan as plan


class ProductChecksTests(unittest.TestCase):
    def setUp(self):
        self.body = {"acceptance_criteria": [
            {"id": "C1", "verification_method": "python3 future_product.py"},
            {"id": "C2", "verification_method": "python3 product_journey.py"}]}
        self.required = [
            {"id": "A", "method": "python3 check_a.py", "relation": "contributes_to",
             "criterion_ids": ["C1"], "origin": "S1"},
            {"id": "B", "method": "python3 check_b.py", "relation": "fully_verify",
             "criterion_ids": ["C2"], "origin": "S2"}]

    def test_full_targets_preserve_original_product_commands_not_future_contributions(self):
        before = deepcopy((self.body, self.required))
        obligations = plan.product_checks(self.body, self.required)
        self.assertEqual(["C2"], [criterion for row in obligations for criterion in row["criterion_ids"]])
        self.assertEqual("python3 product_journey.py", obligations[0]["method"])
        self.assertEqual("fully_verify", obligations[0]["relation"])
        self.assertEqual("product_contract", obligations[0]["origin"])
        self.assertEqual(obligations, plan.product_checks(self.body, self.required))
        self.assertEqual(before, (self.body, self.required))

    def test_represented_product_commands_are_not_duplicated(self):
        retained = plan.product_checks(self.body, self.required)
        self.assertEqual([], plan.product_checks(self.body, self.required + retained))
        self.required[1]["method"] = "python3 product_journey.py"
        self.assertEqual([], plan.product_checks(self.body, self.required))

    def test_unrelated_criterion_command_does_not_discharge_product_obligation(self):
        self.required[0]["method"] = "python3 product_journey.py"
        self.assertEqual(1, len(plan.product_checks(self.body, self.required)))

    def test_prose_human_review_and_not_due_methods_do_not_become_commands(self):
        for changes in ({"verification_method": "Inspect the complete learning journey"},
                        {"verification_method": "test: test_c2_complete"}, {"human_review": True}):
            body = deepcopy(self.body)
            body["acceptance_criteria"][1].update(changes)
            with self.subTest(changes=changes):
                self.assertEqual([], plan.product_checks(body, self.required))
        self.assertEqual([], plan.product_checks(self.body, []))

    def test_partial_representation_retains_entire_prescribed_method(self):
        self.body["acceptance_criteria"][1]["verification_method"] = "Run `go test ./a` and `go test ./b`."
        self.required[1]["method"] = "go test ./a"
        obligations = plan.product_checks(self.body, self.required)
        self.assertEqual(["go test ./a", "go test ./b"], plan.commands(obligations[0]["method"]))
        self.required.append({"id": "C", "method": "go test ./b", "relation": "contributes_to",
                              "criterion_ids": ["C2"]})
        self.assertEqual([], plan.product_checks(self.body, self.required))

    def test_product_identity_changes_with_prescribed_obligation(self):
        original = plan.product_checks(self.body, self.required)[0]["id"]
        self.body["acceptance_criteria"][1]["verification_method"] = "python3 new_journey.py"
        self.assertNotEqual(original, plan.product_checks(self.body, self.required)[0]["id"])


if __name__ == "__main__":
    unittest.main()
