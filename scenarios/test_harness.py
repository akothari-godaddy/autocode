"""Tests for the scenario harness and catalog.  python3 -m unittest scenarios/test_harness.py

Proves every oracle (seed fails, reference passes, broken variants fail), then
proves the full run path with the scripted model: a correct solution is judged
PASS and a plausible wrong one is judged FALSE_COMPLETE.
"""
import argparse
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import baseline, catalog, compare, oracle, routing, verdict  # noqa: E402


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


    def test_a_planned_fix_needs_plan_review_and_the_users_approval(self):
        planned = {"view": {"workflow": "bugfix"}, "cli_calls": ["start", "approve-plan", "resume"],
                   "stages": ["investigate_bug", "astra_discovery", "astra_challenge", "terra"], "answers": []}
        self.assertTrue(all(c.ok for c in oracle.run_checks(planned, workflow="bugfix", plan_approved=True)))
        small = {**planned, "cli_calls": ["start", "resume"], "stages": ["investigate_bug", "terra"]}
        failed = {c.name for c in oracle.run_checks(small, workflow="bugfix", plan_approved=True) if not c.ok}
        self.assertEqual({"plan_reviewed", "plan_approved_by_user"}, failed)


    def test_the_stage_budget_counts_only_model_stages(self):
        run_record = {"view": {"workflow": "bugfix"}, "cli_calls": ["start"], "answers": [],
                      "stages": ["recognize_workflow", "investigate_bug", "orchestrator", "terra", "regression_proof",
                                 "sol", "astra_review"],
                      "model_stages": ["recognize_workflow", "investigate_bug", "terra", "sol", "astra_review"]}
        check, = [c for c in oracle.run_checks(run_record, workflow="bugfix", max_model_stages=5) if c.name == "stage_budget"]
        self.assertTrue(check.ok, check.detail)
        del run_record["model_stages"]  # older records: everything but orchestration counts
        check, = [c for c in oracle.run_checks(run_record, workflow="bugfix", max_model_stages=5) if c.name == "stage_budget"]
        self.assertFalse(check.ok)


class FakeSchemaTests(unittest.TestCase):
    """The scripted model answers "none" for any required field its script does not know yet."""

    def test_missing_required_fields_get_empty_values_of_their_type(self):
        import importlib, json, os
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config.json"
            config.write_text(json.dumps({"check": "true", "paths": [], "brief": "x"}))
            os.environ["SCENARIO_FAKE_CONFIG"] = str(config)
            try:
                fake = importlib.import_module("harness.fake_codex")
            finally:
                del os.environ["SCENARIO_FAKE_CONFIG"]
        schema = {"type": "object", "required": ["kept", "rows", "note", "flag", "kind", "nested"], "properties": {
            "kept": {"type": "string"}, "rows": {"type": "array", "items": {"type": "object", "required": ["id", "extra"],
                "properties": {"id": {"type": "string"}, "extra": {"type": "array"}}}},
            "note": {"type": "string"}, "flag": {"type": "boolean"}, "kind": {"type": "string", "enum": ["none", "some"]},
            "nested": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}}}
        report = fake.complete({"kept": "yes", "rows": [{"id": "R1"}]}, schema)
        self.assertEqual({"kept": "yes", "rows": [{"id": "R1", "extra": []}], "note": "", "flag": False,
                          "kind": "none", "nested": {"n": 0}}, report)


class FakeRunTests(unittest.TestCase):
    """End to end through AutoCode's real CLI, with the scripted model (about 30 s each)."""

    def run_fake(self, solution, scenario="bugfix-iso-weeks"):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True, profile=None, fake_solution=solution, out=Path(out), autocode=None,
                max_steps=None, timeout_minutes=10)
            return run.run_one(catalog.load(scenario), args)

    def test_correct_solution_is_judged_pass(self):
        result = self.run_fake("reference")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("TASK_COMPLETE", result["runner_status"])

    def test_wrong_solution_that_autocode_accepts_is_judged_false_complete(self):
        result = self.run_fake("broken/special-case")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])

    def test_a_review_runs_only_the_reviewer_and_leaves_the_tree_alone(self):
        result = self.run_fake("reference", "review-clean-pr")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("review", result["workflow"])
        self.assertEqual(["recognize_workflow", "review_change"], result["metrics"]["stage_names"])

    def test_an_invented_blocker_in_a_review_is_judged_false_complete(self):
        result = self.run_fake("broken/invented-blocker", "review-clean-pr")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])


class BaselineTests(unittest.TestCase):
    """The plain agent AutoCode is compared against: how it is launched and what its exit means."""

    def test_presets_and_templates(self):
        self.assertEqual(["opencode", "run", "--dir", "/p", "--model", "openai/gpt-6-sol"],
                         baseline.command("opencode", None, Path("/p"), "openai/gpt-6-sol"))
        self.assertEqual(["codex", "exec", "-C", "/p", "--sandbox", "workspace-write", "-"],
                         baseline.command("codex", None, Path("/p"), None))
        self.assertEqual(["agent", "--cwd", "/p", "--model", "m"],
                         baseline.command("codex", "agent --cwd {project} --model {model}", Path("/p"), "m"))
        with self.assertRaisesRegex(ValueError, "unknown baseline"):
            baseline.command("nope", None, Path("/p"), None)

    def test_only_exit_zero_claims_completion(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            ran = baseline.run([sys.executable, "-c", "import sys; sys.exit(sys.stdin.read() != 'brief')"], root,
                               "brief", root / "ok.log", env={}, timeout_seconds=60)
            self.assertEqual((0, baseline.CLAIMED), (ran["exit"], ran["status"]))
            failed = baseline.run([sys.executable, "-c", "raise SystemExit(3)"], root, "", root / "x.log",
                                  env={}, timeout_seconds=60)
            self.assertEqual((3, baseline.STOPPED), (failed["exit"], failed["status"]))
            missing = baseline.run(["no-such-agent-binary"], root, "", root / "m.log", env={}, timeout_seconds=60)
            self.assertEqual((127, baseline.STOPPED), (missing["exit"], missing["status"]))
            self.assertIn("no-such-agent-binary", (root / "m.log").read_text())


class CompareSummaryTests(unittest.TestCase):
    @staticmethod
    def row(name, auto, base, auto_ok, base_ok, auto_s=10.0, base_s=1.0):
        return {"scenario": name, "autocode": {"verdict": auto, "deliverable_passed": auto_ok, "seconds": auto_s,
                                               "model_stages": 5},
                "baseline": {"verdict": base, "deliverable_passed": base_ok, "seconds": base_s}}

    def test_summary_counts_each_side(self):
        rows = [self.row("a", verdict.PASS, verdict.FALSE_COMPLETE, True, False, 30.0, 2.0),
                self.row("b", verdict.PASS, verdict.PASS, True, True, 10.0, 4.0),
                self.row("c", verdict.HONEST_BLOCKER, verdict.PASS, False, True, 20.0, 6.0),
                {"scenario": "d", "skipped": "requires go"}]
        summary = compare.summarize(rows)
        self.assertEqual((4, 3, 1), (summary["scenarios"], summary["compared"], summary["skipped"]))
        self.assertEqual({"autocode only": 1, "both": 1, "baseline only": 1}, summary["outcomes"])
        self.assertEqual({"deliverable_passed": 2, "verdicts": {"PASS": 2, "HONEST_BLOCKER": 1},
                          "false_completions": 0, "total_seconds": 60.0, "median_seconds": 20.0},
                         summary["autocode"])
        self.assertEqual((2, 1, 4.0), (summary["baseline"]["deliverable_passed"],
                                       summary["baseline"]["false_completions"], summary["baseline"]["median_seconds"]))
        text = compare.markdown({"baseline": "opencode (one call)", "mode": "fake", "started_at": "t", "out": "/o",
                                 "autocode": {"commit": "0123456789abcdef", "dirty": False}}, rows, summary)
        self.assertIn("| Deliverable accepted by the oracle | 2/3 | 2/3 |", text)
        self.assertIn("| a | PASS | FALSE_COMPLETE | 30.0 | 2.0 | 5 | autocode only |", text)
        self.assertIn("| d | skipped: requires go |", text)


class CompareRunTests(unittest.TestCase):
    """AutoCode and the scripted agent on one scenario, through run.py compare (a few seconds each)."""

    def compare(self, *extra):
        with tempfile.TemporaryDirectory(prefix="compare-test-") as out:
            self.assertEqual(0, run.main(["compare", "bugfix-iso-weeks", "--fake", "--out", out, *extra]))
            [report] = Path(out).glob("*-compare-fake/comparison.json")
            self.assertTrue((report.parent / "comparison.md").is_file())
            return json.loads(report.read_text())

    def test_both_sides_deliver_the_reference(self):
        [row] = self.compare()["rows"]
        self.assertEqual((verdict.PASS, verdict.PASS), (row["autocode"]["verdict"], row["baseline"]["verdict"]))
        self.assertEqual("both", compare.outcome(row))

    def test_a_wrong_baseline_is_a_false_completion_on_the_same_oracle(self):
        report = self.compare("--fake-baseline-solution", "broken/special-case")
        [row] = report["rows"]
        self.assertEqual(verdict.PASS, row["autocode"]["verdict"])
        self.assertEqual(verdict.FALSE_COMPLETE, row["baseline"]["verdict"], row["baseline"]["summary"])
        self.assertEqual(1, report["summary"]["baseline"]["false_completions"])
        self.assertEqual("autocode only", compare.outcome(row))


if __name__ == "__main__":
    unittest.main()
