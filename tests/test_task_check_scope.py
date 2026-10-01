"""Tasks prove reviewed targets, not unknown work disguised by command spelling."""
import unittest

import autocode_task_check_scope as scope


def check(method):
    return {"id": "A", "method": method}


class TaskCheckScopeTests(unittest.TestCase):
    def test_current_unittest_target_accepts_presentation_and_interpreter_variants(self):
        checks = [check("python3 -m unittest test_greeting.Greeting.test_hello")]
        for command in ("python -m unittest --verbose test_greeting.Greeting.test_hello",
                        "python3.14 -m unittest -q test_greeting.Greeting.test_hello"):
            with self.subTest(command=command):
                scope.require_reviewed_targets([command], checks)

    def test_narrow_selection_is_allowed_but_whole_module_is_not(self):
        scope.require_reviewed_targets(["python -m unittest tests.test_a.Case.test_one"],
                                       [check("python3 -m unittest tests.test_a")])
        with self.assertRaisesRegex(ValueError, "not authorized"):
            scope.require_reviewed_targets(["python3 -m unittest -v test_greeting.py"],
                                           [check("python -m unittest test_greeting.Case.test_hello")])

    def test_unknown_and_shell_wrapped_targets_do_not_inherit_approval(self):
        for command in ("python -m unittest tests.test_future", "python -c 'print(1)'",
                        "bash -c 'python3 -m unittest tests.test_a'"):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, "not authorized|no executable"):
                    scope.require_reviewed_targets([command], [check("python3 -m unittest tests.test_a")])

    def test_python_script_arguments_are_not_treated_as_presentation_flags(self):
        with self.assertRaisesRegex(ValueError, "not authorized"):
            scope.require_reviewed_targets(["python check.py -v"], [check("python check.py")])

    def test_prose_cannot_be_used_as_positive_authority(self):
        with self.assertRaisesRegex(ValueError, "explicit reviewed command"):
            scope.require_reviewed_targets(["Run the tests and continue"], [check("python3 -m unittest tests.test_a")])

    def test_filter_values_and_discovery_arguments_are_not_test_names(self):
        for reviewed, proposed in (
                ("python3 -m unittest -k test_greeting.py test_greeting",
                 "python3 -m unittest -k test_greeting test_greeting"),
                ("python3 -m unittest discover -p test_greeting.py",
                 "python3 -m unittest discover -p test_greeting"),
                ("python3 -m pytest -k '-v' test_greeting.py",
                 "python3 -m pytest -k test_greeting.py")):
            with self.subTest(reviewed=reviewed):
                scope.require_reviewed_targets([reviewed], [check(reviewed)])
                with self.assertRaisesRegex(ValueError, "not authorized"):
                    scope.require_reviewed_targets([proposed], [check(reviewed)])
