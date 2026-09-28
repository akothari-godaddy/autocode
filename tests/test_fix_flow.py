"""`autocode fix` end to end through the real CLI with a scripted, offline agent.

The fake agent (tools/fake_fix_agent.py) stands in for `codex exec`. Each test
scripts what the Builder and Reviewer "do"; the runner's statuses come only
from executing tests, and the scenario's independent oracle re-scores READY
deliveries. The call-count assertions are the small-job cost guard for #15:
a clear bug with a correct fix must not grow beyond one model call.
"""
from __future__ import annotations

import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402

REFERENCE = references.BUGFIX_REFERENCE
SEED = scenarios.BUGFIX_SEED
REPORT = {"status": "FIXED", "summary": "Reject blank and whitespace-only names",
          "diagnosis": {"observed": "greet.py '' prints a greeting", "reproduction": "python3 greet.py ''",
                        "root_cause": "main() checks only the argument count, not whether NAME is blank",
                        "affected_paths": ["greet.py"], "invariant": "a blank NAME is a usage error (exit 2)"},
          "regression_tests": ["test_greet.py"], "regression_command": "", "test_command": "", "question": ""}
APPROVE = {"verdict": "APPROVE", "summary": "Fixes the cause", "findings": []}
BLOCK = {"verdict": "REQUEST_CHANGES", "summary": "Tabs are still accepted",
         "findings": [{"severity": "high", "file": "greet.py", "line": 14, "problem": "Only spaces are rejected",
                       "suggestion": "Use str.strip()"}]}
TASK = "Blank names are greeted: python3 greet.py '' prints 'Hello, ' and exits 0; it must exit 2 with usage."


class FixFlow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="fix-flow-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        references.write(SEED, self.project)
        for command in (["init", "-q"], ["add", "-A"],
                        ["-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed"]):
            subprocess.run(["git", *command], cwd=self.project, check=True, capture_output=True)
        self.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, check=True,
                                   capture_output=True, text=True).stdout.strip()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        shutil.copy2(TOOLS / "fake_fix_agent.py", bin_dir / "codex")
        (bin_dir / "codex").chmod(0o755)
        self.script = self.root / "script.json"
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_FAKE_FIX_SCRIPT": str(self.script),
                    "XDG_CONFIG_HOME": str(self.root / "xdg"), "CODEX_HOME": str(self.root / "codex-home"),
                    "AUTOCODE_HOME": str(self.root / "registry")}
        self.env.pop("AUTOCODE_PROVIDER", None)
        self.env.pop("GITHUB_TOKEN", None)
        self.env.pop("GH_TOKEN", None)

    def fix(self, calls, *args, expected=0, issue=TASK):
        self.script.write_text(json.dumps({"calls": calls}))
        command = [sys.executable, str(TOOLS / "autocode.py"), "fix", *([issue] if issue else []),
                   "--workspace", str(self.project), "--engine", "codex", *args]
        result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        records = list((self.project / ".autocode/fix").glob("*/fix.json"))
        self.assertEqual(1, len(records), result.stdout + result.stderr)
        return json.loads(records[0].read_text()), result

    def calls(self):
        log = self.script.with_suffix(".log")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_clear_bug_is_ready_after_one_model_call(self):
        record, result = self.fix([{"role": "builder", "write": REFERENCE, "report": REPORT}])
        self.assertEqual("READY", record["status"], result.stdout)
        # Cost guard (#15): one Builder call; the tiny single-file fix skips review.
        self.assertEqual({"builder": 1, "reviewer": 0}, record["cost"]["model_calls_by_role"])
        self.assertEqual({"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 200},
                         record["cost"]["tokens"])
        workspace = Path(record["workspace"])
        self.assertTrue(workspace.is_relative_to(self.project / ".autocode/worktrees"))
        oracle = scenarios.bugfix01_oracle(workspace)
        self.assertEqual("PASS", oracle.status, oracle.failed)
        # The fix is committed on its own branch; the user's checkout is untouched.
        log = subprocess.run(["git", "log", "--format=%s", f"{self.base}..{record['branch']}"], cwd=self.project,
                             capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(1, len(log))
        self.assertTrue(log[0].startswith("Fix: Blank names are greeted"))
        self.assertEqual(SEED["greet.py"], (self.project / "greet.py").read_text())
        pr = Path(record["pr_text"]).read_text()
        self.assertIn("main() checks only the argument count", pr)
        self.assertIn("fail on the original code", pr)
        self.assertNotIn(sys.executable, pr)
        self.assertIn("READY", result.stdout)
        self.assertIn("push -u origin", result.stdout)
        # --status reads the saved record without launching anything.
        status = subprocess.run([sys.executable, str(TOOLS / "autocode.py"), "fix", "--status",
                                 str(Path(record["run_dir"]))], env=self.env, capture_output=True, text=True)
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertIn("attempt 1: model=gpt-5.6-terra verify=PASS", status.stdout)

    def test_missing_regression_test_is_repaired_in_the_same_session(self):
        record, _ = self.fix([
            {"role": "builder", "write": {"greet.py": REFERENCE["greet.py"]}, "report": REPORT},
            {"role": "builder", "write": {"test_greet.py": REFERENCE["test_greet.py"]}, "report": REPORT},
        ])
        self.assertEqual("READY", record["status"])
        first, second = self.calls()
        self.assertNotIn("resume", first["argv"])
        self.assertIn("resume", second["argv"])
        self.assertIn("No regression test was added", second["prompt"])
        self.assertNotIn("THE REPORT", second["prompt"])  # a resumed session gets only the feedback
        self.assertEqual(["FAIL", "PASS"], [a["verification"]["verdict"] for a in record["attempts"]])

    def test_vacuous_tests_never_become_ready(self):
        vacuous = SEED["test_greet.py"].replace(
            "    def test_ada(self):", "    def test_blank(self):\n        self.assertTrue(True)\n\n    def test_ada(self):")
        step = {"role": "builder", "write": {"greet.py": REFERENCE["greet.py"], "test_greet.py": vacuous},
                "report": REPORT}
        record, _ = self.fix([step, step], "--max-attempts", "2", expected=1)
        self.assertEqual("FAILED", record["status"])
        self.assertIn("do not reproduce the bug", record["reason"])
        self.assertNotIn("commit", record)
        self.assertTrue(Path(record["patch"]).read_text().startswith("diff --git"))
        self.assertEqual(2, record["cost"]["model_calls"])

    def test_blocking_review_findings_go_back_to_the_builder(self):
        record, _ = self.fix([
            {"role": "builder", "write": REFERENCE, "report": REPORT},
            {"role": "reviewer", "report": BLOCK},
            {"role": "builder", "write": {}, "report": {**REPORT, "summary": "strip() already rejects tabs"}},
            {"role": "reviewer", "report": APPROVE},
        ], "--review", "always")
        self.assertEqual("READY", record["status"])
        calls = self.calls()
        self.assertEqual(["workspace-write", "read-only", "workspace-write", "read-only"],
                         [c["argv"][c["argv"].index("--sandbox") + 1] for c in calls])
        self.assertIn("Only spaces are rejected", calls[2]["prompt"])
        self.assertIn("strip() already rejects tabs", calls[3]["prompt"])
        self.assertEqual({"builder": 2, "reviewer": 2}, record["cost"]["model_calls_by_role"])

    def test_unresolved_review_findings_are_not_ready(self):
        record, _ = self.fix([
            {"role": "builder", "write": REFERENCE, "report": REPORT},
            {"role": "reviewer", "report": BLOCK},
        ], "--review", "always", "--max-attempts", "1", expected=2)
        self.assertEqual("NEEDS_REVIEW", record["status"])
        self.assertNotIn("commit", record)
        self.assertIn("Tabs are still accepted", Path(record["pr_text"]).read_text())

    def test_a_reviewer_that_edits_files_is_refused(self):
        record, _ = self.fix([
            {"role": "builder", "write": REFERENCE, "report": REPORT},
            {"role": "reviewer", "write": {"greet.py": "tampered\n"}, "report": APPROVE},
        ], "--review", "always", expected=1)
        self.assertEqual("ERROR", record["status"])
        self.assertIn("Reviewer changed the workspace", record["reason"])

    def test_a_builder_commit_is_verified_and_kept(self):
        record, _ = self.fix([{"role": "builder", "write": REFERENCE, "report": REPORT, "commit": True}])
        self.assertEqual("READY", record["status"])
        subjects = subprocess.run(["git", "log", "--format=%s", f"{self.base}..{record['branch']}"], cwd=self.project,
                                  capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(["agent commit"], subjects)
        self.assertEqual(subjects and record["commit"], subprocess.run(
            ["git", "rev-parse", record["branch"]], cwd=self.project, capture_output=True, text=True).stdout.strip())

    def test_a_silent_builder_is_stopped_and_never_counted_as_success(self):
        record, _ = self.fix([{"role": "builder", "write": {}, "report": REPORT, "sleep": 30}],
                             "--idle-timeout", "1", expected=1)
        self.assertEqual("ERROR", record["status"])
        self.assertIn("no output for 1 seconds", record["reason"])
        self.assertLess(record["model_calls"][0]["duration_seconds"], 20)

    def test_provider_route_uses_the_configured_tool_and_its_role_models(self):
        """The default route: a config-registered tool with report files and no sessions."""
        providers = self.root / "xdg" / "autocode" / "providers"
        providers.mkdir(parents=True)
        roles = "\n".join(f'{role} = {{ model = "fake/{role}", effort = "low" }}'
                          for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer"))
        (providers / "fixfake.toml").write_text(
            'name = "fixfake"\n'
            f'command = ["{sys.executable}", "{TOOLS / "fake_fix_agent.py"}", "exec", "-C", "{{workspace}}", '
            '"--sandbox", "{sandbox}", "--model", "{model}", "-o", "{report}"]\n'
            'output = "report_file"\n\n[roles]\n' + roles + "\n")
        self.env["AUTOCODE_PROVIDER"] = "fixfake"
        self.script.write_text(json.dumps({"calls": [
            {"role": "builder", "write": {"greet.py": REFERENCE["greet.py"]}, "report": REPORT},
            {"role": "builder", "write": {"test_greet.py": REFERENCE["test_greet.py"]}, "report": REPORT}]}))
        result = subprocess.run([sys.executable, str(TOOLS / "autocode.py"), "fix", TASK, "--workspace",
                                 str(self.project)], cwd=self.root, env=self.env, capture_output=True, text=True,
                                timeout=180)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        record = json.loads(next((self.project / ".autocode/fix").glob("*/fix.json")).read_text())
        self.assertEqual(("READY", "fixfake", "fake/terra", "fake/sol"),
                         (record["status"], record["settings"]["engine"], record["settings"]["builder_model"],
                          record["settings"]["reviewer_model"]))
        first, second = self.calls()
        self.assertIn("OUTPUT CONTRACT", first["prompt"])
        self.assertNotIn("CURRENT HANDOFF DATA", first["prompt"])
        # Without sessions a repair restates the whole task plus the runner's feedback.
        self.assertIn("THE REPORT", second["prompt"])
        self.assertIn("No regression test was added", second["prompt"])

    def test_cannot_reproduce_stops_without_guessing(self):
        record, _ = self.fix([{"role": "builder", "write": {}, "report": {
            **REPORT, "status": "CANNOT_REPRODUCE", "question": "Which Python version shows this?"}}], expected=2)
        self.assertEqual("NEEDS_INPUT", record["status"])
        self.assertEqual("Which Python version shows this?", record["question"])
        self.assertEqual(1, record["cost"]["model_calls"])

    def test_unreadable_report_does_not_cost_a_repair_call(self):
        record, _ = self.fix([{"role": "builder", "write": REFERENCE, "report": "garbage"}])
        self.assertEqual("READY", record["status"])
        self.assertEqual(1, record["cost"]["model_calls"])
        self.assertIn("no usable report", record["model_calls"][0]["problem"])

    def test_escalation_model_only_on_the_final_attempt_after_a_failure(self):
        record, _ = self.fix([
            {"role": "builder", "write": {"greet.py": REFERENCE["greet.py"]}, "report": REPORT},
            {"role": "builder", "write": {"test_greet.py": REFERENCE["test_greet.py"]}, "report": REPORT},
        ], "--max-attempts", "2", "--model", "gpt-cheap", "--escalate-model", "gpt-strong")
        self.assertEqual("READY", record["status"])
        first, second = self.calls()
        self.assertEqual("gpt-cheap", first["argv"][first["argv"].index("--model") + 1])
        self.assertEqual("gpt-strong", second["argv"][second["argv"].index("--model") + 1])
        self.assertNotIn("resume", second["argv"])  # a different model starts a fresh session
        self.assertIn("THE REPORT", second["prompt"])
        self.assertIn("PREVIOUS ATTEMPT", second["prompt"])

    def test_broken_test_environment_stops_before_any_model_call(self):
        (self.project / "test_greet.py").write_text("import missing_dependency_xyz\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qam", "dep"],
                       cwd=self.project, check=True)
        record, result = self.fix([], expected=2)
        self.assertEqual("ENV_BROKEN", record["status"])
        self.assertEqual([], self.calls())
        self.assertEqual(0, record["cost"]["model_calls"])
        self.assertIn("--test-command", result.stdout)

    def test_in_place_needs_a_clean_tree(self):
        (self.project / "greet.py").write_text("dirty\n")
        self.script.write_text(json.dumps({"calls": []}))
        result = subprocess.run([sys.executable, str(TOOLS / "autocode.py"), "fix", TASK, "--workspace",
                                 str(self.project), "--engine", "codex", "--in-place"], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(2, result.returncode)
        self.assertIn("clean working tree", result.stderr)

    def test_github_issue_is_fetched_and_recorded(self):
        issue = {"title": "Blank names are greeted", "body": "`greet.py ''` prints `Hello, `", "comments": 0,
                 "labels": [{"name": "bug"}], "html_url": "https://github.com/octo/greet/issues/7",
                 "user": {"login": "reporter"}}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(issue if self.path == "/repos/octo/greet/issues/7" else {}).encode()
                self.send_response(200 if self.path == "/repos/octo/greet/issues/7" else 404)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Server(ThreadingHTTPServer):
            def server_bind(self):
                # Skip HTTPServer's reverse-DNS getfqdn(): ~35s per bind on macOS CI runners.
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = self.server_address[:2]

        server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.env["AUTOCODE_GITHUB_API"] = f"http://127.0.0.1:{server.server_address[1]}"
        record, _ = self.fix([{"role": "builder", "write": REFERENCE, "report": REPORT}],
                             issue="https://github.com/octo/greet/issues/7")
        self.assertEqual("READY", record["status"])
        self.assertEqual(("Blank names are greeted", 7, "octo/greet"),
                         (record["issue"]["title"], record["issue"]["number"], record["issue"]["repository"]))
        self.assertIn("Fixes octo/greet#7", Path(record["pr_text"]).read_text())
        self.assertIn("`greet.py ''` prints `Hello, `", self.calls()[0]["prompt"])


class Wiring(unittest.TestCase):
    def test_codex_defaults_mirror_the_runner(self):
        import autocode
        import autocode_fix
        self.assertEqual(autocode.DEFAULT_ROLE_MODELS["terra"], autocode_fix.CODEX_MODELS["builder"])
        self.assertEqual(autocode.DEFAULT_ROLE_MODELS["sol"], autocode_fix.CODEX_MODELS["reviewer"])

    def test_unattended_wrapper_refuses_fix_subcommands(self):
        import autocode_unattended
        for subcommand in ("fix", "verify-fix"):
            self.assertIn(subcommand, autocode_unattended.refused([subcommand, "x"]))


if __name__ == "__main__":
    unittest.main()
