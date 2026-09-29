"""The example Claude provider (examples/claude-provider) returns the report as structured output.

AutoCode's command-provider contract tells a stage to write its report to a file. The wrapper
replaces that instruction, so the model is not told both things: a live Validator (2026-09-29)
wrote a complete report with Bash, then returned a structured output without its checks.
"""
import importlib.util
import tomllib
import unittest
from pathlib import Path

from providers import command

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "claude-provider"
spec = importlib.util.spec_from_file_location("claude_stage", EXAMPLE / "claude_stage.py")
claude_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude_stage)


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


if __name__ == "__main__":
    unittest.main()
