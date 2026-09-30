"""The attack reporter must never turn missing/failed outcomes into success."""
import io
import unittest

from .adversarial import Result, group_passed


class ReporterTests(unittest.TestCase):
    def run_cases(self, cases):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(cases)
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(suite)

    def test_failed_subtest_remains_visible_beside_a_passing_case(self):
        class Cases(unittest.TestCase):
            def test_pass(self):
                self.assertEqual(2, 1 + 1)

            def test_subtest(self):
                with self.subTest(attack="bad evidence"):
                    self.fail("invariant broken")

        result = self.run_cases(Cases)
        self.assertEqual(2, result.testsRun)
        self.assertCountEqual(["FAIL", "PASS"], [row["status"] for row in result.rows])
        self.assertFalse(group_passed({"tests_run": result.testsRun, "rows": result.rows, "exit_code": 1}))

    def test_cleanup_error_and_assertion_failure_are_one_failed_case(self):
        class Cases(unittest.TestCase):
            def test_bad(self):
                self.fail("bad completion")

            def tearDown(self):
                raise RuntimeError("orphan cleanup failed")

        result = self.run_cases(Cases)
        self.assertEqual(1, len(result.rows))
        self.assertEqual("ERROR", result.rows[0]["status"])
        self.assertIn("bad completion", result.rows[0]["detail"])
        self.assertIn("orphan cleanup failed", result.rows[0]["detail"])

    def test_failed_worker_or_missing_outcome_cannot_pass(self):
        passed = {"tests_run": 1, "rows": [{"status": "PASS"}], "exit_code": 0}
        self.assertTrue(group_passed(passed))
        self.assertFalse(group_passed({**passed, "exit_code": 1}))
        self.assertFalse(group_passed({**passed, "tests_run": 2}))
        self.assertFalse(group_passed({"tests_run": 0, "rows": [], "exit_code": 0}))
