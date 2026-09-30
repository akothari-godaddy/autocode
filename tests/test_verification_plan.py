"""Approved executable checks cannot be replaced; their scope must be possible."""
from pathlib import Path
import tempfile
import unittest

import autocode_verification_plan as plan
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body


class CommandsTests(unittest.TestCase):
    def test_explicit_commands_are_extracted_but_prose_and_test_names_are_not(self):
        for text, expected in (
            ("python3 -m unittest test_greet.py", ["python3 -m unittest test_greet.py"]),
            ("Run `go test ./...` and inspect `balance`.", ["go test ./..."]),
            ("Validator runs `python3 -m unittest` and reads `README.md`.", ["python3 -m unittest"]),
            ("Run `go test ./a` and `go test ./b`.", ["go test ./a", "go test ./b"]),
            ("`go test ./...`", ["go test ./..."]),
            ("Inspect README.md and verify the exact text `python3 -m temperature VALUE UNIT` "
             "without invoking the metavariable template as a command.", []),
            ("Run python3 -m unittest tests.test_greet", ["python3 -m unittest tests.test_greet"]),
            ("test: test_c1_hello", []), ("Execute CLI cases", []),
            # Prose after a plain command (live bugfix-trivial, 2026-09-30): replayed as a command, it never passes.
            ("python3 -m unittest -v passes; Validator reads the diff", []),
            ("Run python3 -m unittest -v via capture and read the diff", []),
            ("python3 -m unittest discover -s tests -t .", ["python3 -m unittest discover -s tests -t ."]),
            ("python3 -c 'assert f(1, 2) == 3'", ["python3 -c 'assert f(1, 2) == 3'"]),
            ("pytest --verify --output=out.xml tests", ["pytest --verify --output=out.xml tests"])):
            with self.subTest(text=text):
                self.assertEqual(expected, plan.commands(text))

    def test_later_milestone_commands_are_not_forced_on_the_current_task(self):
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "C1", "verification_method": "go test ./first"},
            {"id": "C2", "verification_method": "go test ./later"}]}},
            "current_task": {"acceptance_criteria": ["C1"], "validation_plan": ["go test ./first"]}}
        self.assertEqual(["go test ./first"], plan.approved_commands(state))

    def test_discovery_only_requires_packages_between_start_and_explicit_top(self):
        for command, expected in (
            ("python3 -m unittest discover -s tests -t .", ["tests/__init__.py"]),
            ("python3 -m unittest discover -s src/tests -t src", ["src/tests/__init__.py"]),
            ("python3 -m unittest discover --start-directory=a/b --top-level-directory=.",
             ["a/__init__.py", "a/b/__init__.py"]),
            ("python3 -m unittest discover -s tests", []),
            ("python3 -m unittest discover -s . -t .", [])):
            with self.subTest(command=command):
                self.assertEqual(expected, plan.package_markers(command))


class ScopeTests(unittest.TestCase):
    def test_missing_initializer_must_be_assigned_before_the_plan_is_approved(self):
        with tempfile.TemporaryDirectory() as workspace:
            value = body()
            value["acceptance_criteria"][0]["verification_method"] = "python3 -m unittest discover -s tests -t ."
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/test_greet.py"]
            state = {"workspace": workspace}
            with self.assertRaisesRegex(ValueError, "requires tests/__init__.py.*outside affected_paths"):
                lifecycle.validate_body(state, value)
            self.assertNotIn("goal_contract", state)
            value["milestones"][0]["affected_paths"].append("tests/__init__.py")
            lifecycle.validate_body(state, value)
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/"]
            lifecycle.validate_body(state, value)
            Path(workspace, "tests").mkdir()
            Path(workspace, "tests/__init__.py").write_text("")
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/test_greet.py"]
            lifecycle.validate_body(state, value)
