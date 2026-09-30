"""The example Claude provider (examples/claude-provider) returns the report as structured output.

AutoCode's command-provider contract tells a stage to write its report to a file. The wrapper
replaces that instruction, so the model is not told both things: a live Validator (2026-09-29)
wrote a complete report with Bash, then returned a structured output without its checks.
"""
import importlib.util
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from providers import command

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "claude-provider"
spec = importlib.util.spec_from_file_location("claude_stage", EXAMPLE / "claude_stage.py")
claude_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude_stage)
batch_spec = importlib.util.spec_from_file_location("batch", EXAMPLE / "batch.py")
batch = importlib.util.module_from_spec(batch_spec)
batch_spec.loader.exec_module(batch)


class PromptTests(unittest.TestCase):
    def test_autocode_s_write_a_file_instruction_is_replaced(self):
        config = tomllib.loads((EXAMPLE / "claude.toml").read_text())
        provider = command.CommandProvider(config, EXAMPLE / "claude.toml")
        prompt = provider.prompt_for_schema("Validate the work.\nCURRENT HANDOFF DATA\n{}", {"type": "object"},
                                            "/run/iterations/001/sol-01.jsonl")
        self.assertIn("to this file: /run/iterations/001/sol-01.json", prompt)  # the contract as AutoCode writes it
        adapted = claude_stage.adapt(prompt)
        self.assertNotIn("to this file", adapted)
        self.assertIn(claude_stage.STRUCTURED, adapted)
        self.assertIn("CURRENT HANDOFF DATA", adapted)

    def test_a_prompt_without_the_instruction_is_unchanged(self):
        self.assertEqual("Answer the question.", claude_stage.adapt("Answer the question."))


class BatchTests(unittest.TestCase):
    """batch.py restarts only what a container restart killed: runs without a result.json."""

    def test_only_the_runs_still_missing_are_started_again(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for name, verdict in (("20260930T0759Z-bugfix-trivial-claude-tiers-a1", "PASS"),
                                  ("20260930T0800Z-bugfix-trivial-claude-tiers-b2", None),  # killed mid-run
                                  ("20260930T0801Z-review-then-fix-claude-tiers-c3", "FALSE_COMPLETE")):
                (out / name).mkdir()
                if verdict:
                    (out / name / "result.json").write_text(json.dumps(
                        {"verdict": verdict, "checks": [{"ok": True}], "wall_seconds": 60}))
            ids = ["bugfix-trivial", "review-then-fix", "discuss-cache-choice"]
            self.assertEqual(["bugfix-trivial", "review-then-fix", "discuss-cache-choice", "discuss-cache-choice"],
                             batch.missing(ids, out, 2))
            summary = batch.status(out)
            self.assertIn("finished 2 (1 FALSE_COMPLETE, 1 PASS), running 1", summary)
            self.assertIn("bugfix-trivial                 PASS 1/1 60s $0.00 | running", summary)

    def test_the_qualification_list_names_real_scenarios(self):
        ids = batch.scenarios(EXAMPLE / "qualification.txt")
        self.assertEqual(17, len(ids))
        catalog = EXAMPLE.parents[1] / "scenarios" / "catalog"
        self.assertEqual([], [scenario for scenario in ids if not (catalog / scenario / "scenario.toml").is_file()])


if __name__ == "__main__":
    unittest.main()
