"""Tests for the scenario harness and catalog.  python3 -m unittest scenarios/test_harness.py

Proves every oracle (seed fails, reference passes, broken variants fail), then
proves the full run path with the scripted model: a correct solution is judged
PASS and a plausible wrong one is judged FALSE_COMPLETE.
"""
import argparse
import ast
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import catalog, oracle, routing, verdict  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_every_oracle_rejects_the_seed_accepts_the_reference_and_rejects_broken_variants(self):
        for scenario in catalog.load_all():
            if scenario.missing_tools():
                continue
            with self.subTest(scenario=scenario.id):
                self.assertIsNotNone(scenario.reference, "every scenario needs a reference solution")
                for name, ok, summary in run.self_test(scenario):
                    self.assertTrue(ok, f"{name}: {summary}")

    def test_nothing_here_imports_autocode(self):
        """Oracles and the harness judge AutoCode from outside; importing it would let its bugs hide."""
        here = Path(__file__).resolve().parent
        autocode_modules = {path.stem for path in (here.parent / "tools").glob("*.py")} | {"tools", "autocode_cli"}
        autocode_modules -= {"__init__", "__main__"}
        for path in here.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                         [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
                for name in names:
                    self.assertNotIn(name.split(".")[0], autocode_modules, f"{path.relative_to(here)} imports {name}")

    def test_briefs_are_plain_text(self):
        for scenario in catalog.load_all():
            with self.subTest(scenario=scenario.id):
                self.assertTrue(scenario.brief)
                self.assertNotIn("\n#", scenario.brief, "headings would become part of the task text")

    def test_known_failures_say_why(self):
        for scenario in catalog.load_all():
            with self.subTest(scenario=scenario.id):
                self.assertIsInstance(scenario.known_failure, str)
                if scenario.known_failure:
                    self.assertGreater(len(scenario.known_failure), 20, "a known failure names what is missing")
                self.assertIn(scenario.expected, catalog.EXPECTED)

    def test_routing_table_loads_and_names_known_workflows(self):
        table = routing.load()
        catalog.load(table["seed"])
        self.assertGreaterEqual(len(table["prompts"]), 10)
        self.assertEqual(set(routing.WORKFLOWS), {p["workflow"] for p in table["prompts"]},
                         "every workflow needs at least one prompt")


class JudgeTests(unittest.TestCase):
    passing = verdict.OracleResult([verdict.Check("a", True)])
    failing = verdict.OracleResult([verdict.Check("a", False)])

    def test_verdicts(self):
        self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", self.passing)[0])
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", self.failing)[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("PAUSED_BUDGET", self.passing)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("RUNNING", self.passing)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("TASK_COMPLETE", verdict.OracleResult(error="boom"))[0])

    def test_a_scenario_that_expects_a_stop(self):
        self.assertEqual(verdict.PASS, verdict.judge("PAUSED_HUMAN", self.passing, "stop")[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("PAUSED_HUMAN", self.failing, "stop")[0])
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", self.passing, "stop")[0])
        self.assertEqual(verdict.PASS, verdict.judge("WAITING_FOR_USER", self.passing, "any")[0])
        self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", self.passing, "any")[0])

    def test_an_oracle_with_no_checks_does_not_pass(self):
        self.assertFalse(verdict.OracleResult([]).passed)


class RunChecksTests(unittest.TestCase):
    """Run-level checks judge how AutoCode worked; without a run there is nothing to judge."""

    def test_no_run_means_no_checks(self):
        self.assertEqual([], oracle.run_checks(None, workflow="review", no_build=True))

    def test_todays_build_pipeline_fails_a_review(self):
        run_record = {"view": {"workflow": None}, "cli_calls": ["start", "approve-plan", "resume"],
                      "stages": ["requirements_gather", "astra_discovery", "terra", "sol"], "answers": []}
        failed = {c.name for c in oracle.run_checks(run_record, workflow="review", no_build=True, no_requirements=True)
                  if not c.ok}
        self.assertEqual({"workflow_recognized", "no_builder_dispatched", "no_build_plan_approval_requested",
                          "no_requirements_gathering"}, failed)

    def test_a_recognized_read_only_review_passes(self):
        run_record = {"view": {"workflow": "review"}, "cli_calls": ["start", "resume"],
                      "stages": ["review", "sol"], "answers": []}
        self.assertTrue(all(c.ok for c in oracle.run_checks(run_record, workflow="review", no_build=True,
                                                            no_requirements=True, max_questions=0)))


class FakeRunTests(unittest.TestCase):
    """End to end through AutoCode's real CLI, with the scripted model (about 30 s each)."""

    def run_fake(self, solution):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True, profile=None, fake_solution=solution, out=Path(out), autocode=None,
                max_steps=None, timeout_minutes=10)
            return run.run_one(catalog.load("bugfix-iso-weeks"), args)

    def test_correct_solution_is_judged_pass(self):
        result = self.run_fake("reference")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("TASK_COMPLETE", result["runner_status"])

    def test_wrong_solution_that_autocode_accepts_is_judged_false_complete(self):
        result = self.run_fake("broken/special-case")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])


if __name__ == "__main__":
    unittest.main()
