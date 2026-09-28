"""Model-free fix verification (autocode_verify) and bug-report intake (autocode_issue).

These tests execute real test suites in scratch Git worktrees; they never launch
a provider. Each negative control is a way a candidate could look fixed without
being fixed, and each must be rejected by execution, not by reading a report.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode_issue as issues  # noqa: E402
import autocode_verify as verify  # noqa: E402
import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402

REFERENCE = references.BUGFIX_REFERENCE
SEED = scenarios.BUGFIX_SEED


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Project:
    """A committed BUGFIX-01 seed; tests overlay candidate files on the working tree."""

    def __init__(self, files=SEED):
        self.temp = tempfile.TemporaryDirectory(prefix="fix-verify-")
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        references.write(files, self.root)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.evidence = Path(self.temp.name) / "evidence"

    def write(self, files):
        references.write(files, self.root)

    def verify(self, **options):
        framework = verify.detect_framework(self.root)
        suite = options.pop("suite_command", None) or framework.suite
        base_suite = verify.baseline(self.root, self.base, self.evidence, framework=framework,
                                     suite_command=suite, timeout=120)
        return verify.verify(self.root, self.base, self.evidence, framework=framework, base_suite=base_suite,
                             timeout=120, **options)

    def close(self):
        self.temp.cleanup()


class VerifyCase(unittest.TestCase):
    def project(self, files=SEED):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_reference_fix_is_proven_by_a_fail_to_pass_flip(self):
        project = self.project()
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("derived:unittest", result["commands"]["regression_source"])
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"])
        self.assertEqual(0, result["checks"]["regression_on_candidate"]["exit_code"])
        self.assertEqual(["test_greet.py"], result["test_files"])
        self.assertEqual(["greet.py"], result["source_files"])
        self.assertEqual(1, result["stats"]["source_files"])
        # Verification never runs in, or changes, the candidate workspace.
        self.assertEqual([], [line for line in git(project.root, "worktree", "list").splitlines()[1:]])
        self.assertEqual({"greet.py", "test_greet.py"}, set(result["changes"]))

    def test_fix_without_a_regression_test_fails(self):
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("No regression test" in reason for reason in result["failures"]))

    def test_fix_without_a_test_is_only_unverified_when_allowed(self):
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"]})
        result = project.verify(allow_no_test=True)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def test_a_test_that_passes_on_the_unfixed_code_proves_nothing(self):
        project = self.project()
        vacuous = SEED["test_greet.py"].replace(
            "    def test_ada(self):", "    def test_blank(self):\n        self.assertTrue(True)\n\n    def test_ada(self):")
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": vacuous})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("do not reproduce the bug" in reason for reason in result["failures"]), result)

    def test_only_tests_changed_is_not_a_fix(self):
        project = self.project()
        project.write({"test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("Only test files changed" in reason for reason in result["failures"]))
        self.assertTrue(any("fail on the candidate" in reason for reason in result["failures"]), result)

    def test_removing_an_existing_test_is_rejected(self):
        project = self.project()
        weakened = REFERENCE["test_greet.py"].replace("    def test_two_arg(self):", "    def two_arg_disabled(self):")
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": weakened})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("test_greet.py::test_two_arg" in reason for reason in result["failures"]), result)

    def test_deleting_a_test_file_is_rejected(self):
        files = {**SEED, "tests/test_extra.py": "import unittest\n\nclass T(unittest.TestCase):\n"
                                                "    def test_ok(self):\n        pass\n"}
        project = self.project(files)
        (project.root / "tests/test_extra.py").unlink()
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("deleted" in reason for reason in result["failures"]), result)

    def test_a_fix_that_breaks_another_test_fails_the_suite(self):
        project = self.project()
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        project.write({"greet.py": broken, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("fail on the candidate" in reason or "fails on the candidate" in reason
                            for reason in result["failures"]), result)

    def test_pre_existing_failures_do_not_block_when_nothing_new_fails(self):
        flaky = ("import unittest\n\nclass Env(unittest.TestCase):\n"
                 "    def test_needs_network(self):\n        self.fail('no network in CI')\n")
        project = self.project({**SEED, "test_env.py": flaky})
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("failing_tests", result["baseline"]["health"])
        self.assertTrue(any("already failed on base" in note for note in result["notes"]), result)

    def test_new_failures_are_named_even_when_base_already_fails(self):
        flaky = ("import unittest\n\nclass Env(unittest.TestCase):\n"
                 "    def test_needs_network(self):\n        self.fail('no network in CI')\n")
        project = self.project({**SEED, "test_env.py": flaky})
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        project.write({"greet.py": broken, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("test_ada" in reason for reason in result["failures"]), result)

    def test_pre_existing_failure_in_the_changed_test_file_is_not_counted(self):
        """The regression test shares a file with an environment-dependent failure (common upstream)."""
        needs_env = ("\n    def test_needs_secret(self):\n"
                     "        self.assertTrue(__import__('os').environ.get('NO_SUCH_SECRET_XYZ'))\n")
        seed = {**SEED, "test_greet.py": SEED["test_greet.py"].replace("\n\nif __name__", needs_env + "\n\nif __name__")}
        project = self.project(seed)
        project.write({"greet.py": REFERENCE["greet.py"],
                       "test_greet.py": REFERENCE["test_greet.py"].replace("\n\nif __name__", needs_env + "\n\nif __name__")})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], result["fail_to_pass"])
        self.assertTrue(any("already fail on base were not counted" in note for note in result["notes"]), result)

    def test_a_new_test_the_fix_does_not_fix_is_not_excused(self):
        still_broken = REFERENCE["test_greet.py"].replace(
            "\n\nif __name__",
            "\n    def test_tab_name(self):\n"
            "        proc = subprocess.run([sys.executable, 'greet.py', 'A\\tB'], capture_output=True)\n"
            "        self.assertEqual(3, proc.returncode)\n\n\nif __name__")
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": still_broken})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("fail on the candidate: test_greet.TestGreet.test_tab_name" in reason
                            for reason in result["failures"]), result)

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_pytest_projects_use_junit_results(self):
        files = {"pyproject.toml": "[tool.pytest.ini_options]\npythonpath = [\"src\"]\n",
                 "src/calc/__init__.py": "def mean(values):\n    return sum(values) / len(values)\n",
                 "tests/test_calc.py": "from calc import mean\n\n\ndef test_mean():\n    assert mean([1, 2, 3]) == 2\n"}
        project = self.project(files)
        project.write({"src/calc/__init__.py": "def mean(values):\n    if not values:\n"
                                               "        raise ValueError('empty')\n    return sum(values) / len(values)\n",
                       "tests/test_calc.py": files["tests/test_calc.py"] + "\n\ndef test_empty():\n"
                                             "    import pytest\n    with pytest.raises(ValueError):\n        mean([])\n"})
        framework = verify.detect_framework(project.root, python=sys.executable)
        self.assertEqual("pytest", framework.name)
        base_suite = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                                     suite_command=framework.suite, timeout=120)
        result = verify.verify(project.root, project.base, project.evidence, framework=framework,
                               base_suite=base_suite, timeout=120)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["tests.test_calc::test_empty"], result["fail_to_pass"])

    def test_untracked_new_test_file_counts_as_the_regression_test(self):
        project = self.project()
        new_test = ("import subprocess, sys, unittest\n\nclass Blank(unittest.TestCase):\n"
                    "    def test_blank(self):\n"
                    "        proc = subprocess.run([sys.executable, 'greet.py', ''], capture_output=True)\n"
                    "        self.assertEqual(2, proc.returncode)\n")
        project.write({"greet.py": REFERENCE["greet.py"], "tests/test_blank.py": new_test})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual({"greet.py": "modified", "tests/test_blank.py": "added"}, result["changes"])

    def test_candidate_workspace_changes_are_detected_after_commit_too(self):
        project = self.project()
        project.write(REFERENCE)
        git(project.root, "add", "-A")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "builder commit")
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_builder_regression_command_must_name_a_changed_test(self):
        chosen = verify.select_commands(None, ["tests/test_blank.py"],
                                        reported={"regression_command": "grep -q strip greet.py"})
        self.assertIsNone(chosen["regression"])
        self.assertTrue(chosen["notes"])
        chosen = verify.select_commands(None, ["tests/test_blank.py"],
                                        reported={"regression_command": "python -m pytest tests/test_blank.py"})
        self.assertEqual("builder", chosen["regression_source"])

    def test_explicit_commands_win_over_detection(self):
        framework = verify.Framework("pytest", "python -m pytest -q", python="python")
        chosen = verify.select_commands(framework, ["tests/test_a.py"], suite_command="make check",
                                        regression_command="make one")
        self.assertEqual(("make check", "explicit", "make one", "explicit"),
                         (chosen["suite"], chosen["suite_source"], chosen["regression"], chosen["regression_source"]))

    def test_test_path_classification(self):
        for path in ("tests/test_x.py", "pkg/test_x.py", "x_test.go", "src/a.test.ts", "spec/a_spec.rb",
                     "src/test/java/FooTest.java", "__tests__/a.js", "pkg/testdata/in.txt", "conftest.py"):
            self.assertTrue(verify.is_test_path(path), path)
        for path in ("greet.py", "src/contest.py", "latest.py", "src/protest/x.go", "attestation.rs"):
            self.assertFalse(verify.is_test_path(path), path)

    def test_framework_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            (root / "go.mod").write_text("module x\n")
            git(root, "add", "-A")
            framework = verify.detect_framework(root)
            self.assertEqual(("go", "go test ./..."), (framework.name, framework.suite))
            self.assertEqual("go test ./pkg/a", framework.targeted(["pkg/a/a_test.go"]))
            package = {"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}
            (root / "package.json").write_text(json.dumps(package))
            (root / "go.mod").unlink()
            git(root, "add", "-A")
            framework = verify.detect_framework(root)
            self.assertEqual(("jest", "npm test --silent"), (framework.name, framework.suite))
            self.assertIn("jest src/a.test.js", framework.targeted(["src/a.test.js"]))

    def test_unittest_failures_are_parsed_per_test(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "out.log"
            log.write_text("test_a (m.C.test_a) ... ok\nFAIL: test_b (m.C.test_b)\nERROR: test_c (m.C)\n"
                           "----\nRan 3 tests in 0.1s\n\nFAILED (failures=1, errors=1)\n")
            framework = verify.Framework("unittest", "python -m unittest", python="python")
            results = verify.per_test_results(framework, {"output": str(log)}, Path(temp) / "none.xml")
            self.assertEqual({"failed": ["m.C.test_b", "m.C::test_c"], "total": 3}, results)


class IssueCase(unittest.TestCase):
    def test_references(self):
        self.assertEqual({"owner": "psf", "repo": "requests", "number": 6100},
                         issues.parse_reference("https://github.com/psf/requests/issues/6100"))
        self.assertEqual({"owner": "a", "repo": "b.c", "number": 3}, issues.parse_reference("a/b.c#3"))
        self.assertIsNone(issues.parse_reference("The parser crashes on empty input"))
        with tempfile.TemporaryDirectory() as temp:
            git(temp, "init", "-q")
            git(temp, "remote", "add", "origin", "git@github.com:octo/widgets.git")
            self.assertEqual({"owner": "octo", "repo": "widgets", "number": 12}, issues.parse_reference("#12", temp))

    def test_fetch_uses_issue_and_comments_and_marks_pull_requests(self):
        pages = {
            "https://api.test/repos/o/r/issues/5": {"title": "Crash on empty", "body": "<!-- template -->Steps: run x",
                                                     "comments": 1, "labels": [{"name": "bug"}],
                                                     "html_url": "https://github.com/o/r/issues/5",
                                                     "user": {"login": "reporter"}},
            "https://api.test/repos/o/r/issues/5/comments?per_page=100": [
                {"user": {"login": "maint"}, "body": "Reproduced on 2.1", "created_at": "2026-01-01"}],
        }
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, request.get_header("Authorization")))
            return io.BytesIO(json.dumps(pages[request.full_url]).encode())

        issue = issues.fetch({"owner": "o", "repo": "r", "number": 5}, token="t0k", api="https://api.test",
                             opener=opener)
        self.assertEqual(("Crash on empty", ["bug"], "o/r", False),
                         (issue["title"], issue["labels"], issue["repository"], issue["is_pull_request"]))
        self.assertEqual("Bearer t0k", seen[0][1])
        text = issues.render(issue)
        self.assertIn("Reproduced on 2.1", text)
        self.assertNotIn("template", text)
        self.assertEqual("5-crash-on-empty", issues.slug(issue))

    def test_fetch_error_names_the_remedy(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with self.assertRaisesRegex(RuntimeError, "GITHUB_TOKEN"):
            issues.fetch({"owner": "o", "repo": "r", "number": 1}, api="https://api.test", opener=opener)

    def test_long_reports_and_discussions_are_bounded(self):
        issue = issues.from_text("Title\n\n" + "x" * (issues.BODY_LIMIT + 50))
        issue["comments"] = [{"author": "a", "body": "y" * 6000} for _ in range(5)]
        text = issues.render(issue)
        self.assertIn("truncated", text)
        self.assertIn("comments total", text)
        self.assertLess(len(text), issues.BODY_LIMIT + issues.COMMENTS_LIMIT + 2000)

    def test_json_issue_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "issue.json"
            path.write_text(json.dumps({"title": "Bad total", "body": "Totals are off by one",
                                        "labels": ["bug"], "number": 9, "repository": "o/r"}))
            issue = issues.load(issue_file=path)
            self.assertEqual(("Bad total", 9, ["bug"]), (issue["title"], issue["number"], issue["labels"]))


if __name__ == "__main__":
    unittest.main()
