"""Harness tests for the live-trial driver (step 1).

These run offline with the fixture profile. They prove workspace setup, CLI
driving, gate serving, oracle independence and evidence bundles. They do not
evaluate model quality.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HERE))

import live_profiles as profiles  # noqa: E402
import live_scenarios as scenarios  # noqa: E402
import live_trial  # noqa: E402
from autopilot_testkit import Bundle, artifacts_root  # noqa: E402


class ProfilesTest(unittest.TestCase):
    def test_resolve_rejects_unknown_profile(self):
        with self.assertRaisesRegex(ValueError, "unknown live profile"):
            profiles.resolve("nope")

    def test_fixture_profile_needs_no_spend_flag(self):
        profile = profiles.resolve("fixture")
        self.assertEqual("fixture", profile["provider"])
        self.assertEqual([], profiles.cli_overrides(profile))

    def test_live_profiles_pin_model_and_effort(self):
        profile = profiles.resolve("glm53")
        flags = profiles.cli_overrides(profile)
        self.assertIn("--terra-model", flags)
        self.assertIn("zai-coding-plan/glm-5.3", flags)
        self.assertIn("--glm-reasoning-effort", flags)
        self.assertIn("max", flags)

    def test_glm53_openai_uses_subscription_models_only(self):
        profile = profiles.resolve("glm53-openai")
        flags = profiles.cli_overrides(profile)
        joined = " ".join(flags)
        self.assertIn("--glm-model", flags)
        self.assertIn("zai-coding-plan/glm-5.3", flags)
        self.assertIn("--terra-model", flags)
        self.assertIn("openai/gpt-6-sol", flags)
        self.assertNotIn("mimo", joined)
        self.assertNotIn("-free", joined)
        self.assertNotIn("flash", joined)
        self.assertNotIn("mimo-token-plan/", joined)

    def test_glm53_openai_verifier_never_equals_producer(self):
        profile = profiles.resolve("glm53-openai")
        models = profile["role_models"]
        self.assertNotEqual(models["planner"].split("/")[0], models["reviewer"].split("/")[0])
        self.assertNotEqual(models["builder"].split("/")[0], models["validator"].split("/")[0])
        self.assertNotEqual(models["builder"].split("/")[0], models["completion"].split("/")[0])

    def test_glm53_openai_ladder_efforts_match_docs(self):
        profile = profiles.resolve("glm53-openai")
        effort = profile["effort"]
        self.assertEqual("medium", effort["requirements"])
        self.assertEqual("high", effort["planner"])
        self.assertEqual("high", effort["reviewer"])
        self.assertEqual("medium", effort["builder"])
        self.assertEqual("high", effort["validator"])
        self.assertEqual("medium", effort["completion"])
        self.assertEqual("high", effort["resolver"])
        flags = profiles.cli_overrides(profile)
        i = flags.index("--requirements-reasoning-effort")
        self.assertEqual("medium", flags[i + 1])
        i = flags.index("--reasoning-effort")
        self.assertEqual("medium", flags[i + 1])
        i = flags.index("--sol-reasoning-effort")
        self.assertEqual("high", flags[i + 1])


class ScenarioTest(unittest.TestCase):
    def test_unknown_scenario_lists_known_ids(self):
        with self.assertRaisesRegex(ValueError, "LIVE-01"):
            scenarios.scenario("LIVE-99")

    def test_classifier_never_calls_a_pause_complete(self):
        self.assertEqual("complete", scenarios.classify_runner_status("TASK_COMPLETE"))
        self.assertEqual("paused", scenarios.classify_runner_status("PAUSED_REPORT_REPAIR_LIMIT"))
        self.assertEqual("paused", scenarios.classify_runner_status("AWAITING_GOAL_APPROVAL"))
        self.assertEqual("stopped", scenarios.classify_runner_status("RUNNING"))


class Fx01OracleTest(unittest.TestCase):
    """The oracle is independent: score known-good and known-bad deliveries."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="fx01-")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)

    def _write(self, name: str, body: str):
        (self.project / name).write_text(body)

    def test_missing_delivery_fails(self):
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        self.assertTrue(result.failed)

    def test_reference_delivery_scores_12_of_12(self):
        # Same reference bytes the fixture provider ships.
        import live_fixture_provider as fixture
        self._write("greet.py", fixture.GREET_PY)
        self._write("test_greet.py", fixture.TEST_GREET_PY)
        self._write("README.md", fixture.README_MD)
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.PASS, result.status, result.summary)
        self.assertEqual(12, len(result.checks))
        self.assertEqual([], result.failed)

    def test_wrong_exit_code_is_detected(self):
        import live_fixture_provider as fixture
        self._write("greet.py", fixture.GREET_PY.replace("return 2", "return 1"))
        self._write("test_greet.py", fixture.TEST_GREET_PY)
        self._write("README.md", fixture.README_MD)
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        names = {row["name"] for row in result.failed}
        self.assertIn("no-arg.exit", names)

    def test_non_stdlib_import_is_detected(self):
        import live_fixture_provider as fixture
        self._write("greet.py", fixture.GREET_PY + "\nimport requests\n")
        self._write("test_greet.py", fixture.TEST_GREET_PY)
        self._write("README.md", fixture.README_MD)
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        self.assertIn("stdlib_only", {row["name"] for row in result.failed})


class TrialVerdictTest(unittest.TestCase):
    def test_harness_timeout_preserves_both_reports_and_oracle_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            oracle = scenarios.OracleResult(scenarios.FAIL, "not delivered", [{"name": "artifact", "ok": False}])
            spec = {"title": "Fixture", "task": "fixture", "oracle_name": "FX01",
                    "oracle": mock.Mock(return_value=oracle)}
            with (mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(root / "evidence")}),
                  mock.patch.object(live_trial, "make_workspace", return_value=root),
                  mock.patch.object(scenarios, "scenario", return_value=spec),
                  mock.patch.object(live_trial, "drive", side_effect=live_trial.TrialError("deadline exhausted"))):
                self.assertEqual(1, live_trial.main(["LIVE-01", "--workspace", str(root)]))
            evidence = root / "evidence/LIVE-01/01"
            self.assertEqual("ERROR", json.loads((evidence / "result.json").read_text())["status"])
            report = json.loads((evidence / "live-trial.json").read_text())
            self.assertEqual("ERROR", report["verdict"])
            self.assertEqual(oracle.checks, report["checks"])

    def test_verdict_matrix(self):
        cases = [
            ("TASK_COMPLETE", scenarios.PASS, scenarios.PASS),
            ("COMPLETE", scenarios.FAIL, scenarios.FALSE_COMPLETE),
            ("TASK_COMPLETE", scenarios.FAIL, scenarios.FALSE_COMPLETE),
            ("PAUSED_USAGE_UNKNOWN", scenarios.FAIL, scenarios.HONEST_BLOCKER),
            ("AWAITING_GOAL_APPROVAL", scenarios.PASS, scenarios.HONEST_BLOCKER),
            ("RUNNING", scenarios.PASS, scenarios.ERROR),
            ("", scenarios.FAIL, scenarios.ERROR),
            ("TASK_COMPLETE", scenarios.ERROR, scenarios.ERROR),
            ("PAUSED_REQUESTED", scenarios.ERROR, scenarios.ERROR),
            ("TASK_COMPLETE", scenarios.DEFERRED, scenarios.DEFERRED),
        ]
        for status, oracle_status, expected in cases:
            with self.subTest(status=status, oracle=oracle_status):
                checks = [{"name": "acceptance", "ok": oracle_status == scenarios.PASS}]
                oracle = scenarios.OracleResult(oracle_status, "independent check", checks)
                spec = {"oracle": mock.Mock(return_value=oracle), "oracle_name": "FX01"}
                result = live_trial.judge({"state": {"status": status}}, spec, HERE, mock.Mock())
                self.assertEqual(expected, result.status)
                self.assertEqual(checks, result.checks)

    def test_failed_check_cannot_be_hidden_by_oracle_pass_label(self):
        oracle = scenarios.OracleResult(scenarios.PASS, "incorrect label", [{"ok": False}])
        spec = {"oracle": mock.Mock(return_value=oracle), "oracle_name": "FX01"}
        result = live_trial.judge({"state": {"status": "TASK_COMPLETE"}}, spec, HERE, mock.Mock())
        self.assertEqual(scenarios.FALSE_COMPLETE, result.status)

    def test_exit_code_and_both_reports_preserve_verdict(self):
        for status, oracle_status, expected, exit_code in [
            ("TASK_COMPLETE", scenarios.PASS, scenarios.PASS, 0),
            ("TASK_COMPLETE", scenarios.FAIL, scenarios.FALSE_COMPLETE, 1),
            ("PAUSED_USAGE_UNKNOWN", scenarios.FAIL, scenarios.HONEST_BLOCKER, 2),
            ("RUNNING", scenarios.PASS, scenarios.ERROR, 1),
        ]:
            with self.subTest(status=status, oracle=oracle_status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                oracle = scenarios.OracleResult(oracle_status, "oracle result", [
                    {"name": "acceptance", "ok": oracle_status == scenarios.PASS}])
                spec = {"title": "Fixture", "task": "fixture", "oracle_name": "FX01",
                        "oracle": mock.Mock(return_value=oracle)}
                with (mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(root / "evidence")}),
                      mock.patch.object(live_trial, "make_workspace", return_value=root),
                      mock.patch.object(scenarios, "scenario", return_value=spec),
                      mock.patch.object(live_trial, "drive", return_value={"state": {"status": status}})):
                    code = live_trial.main(["LIVE-01", "--workspace", str(root)])
                self.assertEqual(exit_code, code)
                evidence = root / "evidence" / "LIVE-01" / "01"
                self.assertEqual(expected, json.loads((evidence / "result.json").read_text())["status"])
                self.assertEqual(expected, json.loads((evidence / "live-trial.json").read_text())["verdict"])


class LiveTrialSmokeTest(unittest.TestCase):
    """End-to-end offline smoke: the known-PASS scenario under the fixture profile."""

    def test_live01_fixture_profile_reaches_pass(self):
        with tempfile.TemporaryDirectory(prefix="live-trial-smoke-") as temp:
            code = live_trial.main([
                "LIVE-01", "--profile", "fixture", "--workspace", temp,
                "--budget-stages", "40", "--timeout", "120",
            ])
        self.assertEqual(0, code)

    def test_live_profile_requires_authorization(self):
        for flags in ([], ["--authorize-deployment"]):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(prefix="live-trial-auth-") as temp:
                code = live_trial.main([
                    "LIVE-01", "--profile", "glm53", "--workspace", temp, *flags,
                ])
            self.assertEqual(2, code)

    def test_bundle_records_profile_and_oracle_checks(self):
        with tempfile.TemporaryDirectory(prefix="live-trial-bundle-") as temp:
            live_trial.main([
                "LIVE-01", "--profile", "fixture", "--workspace", temp,
                "--budget-stages", "40", "--timeout", "120",
            ])
        results = sorted(artifacts_root().glob("LIVE-01/*/live-trial.json"))
        self.assertTrue(results, "live-trial.json was not written")
        payload = json.loads(results[-1].read_text())
        self.assertEqual("LIVE-01", payload["scenario"])
        self.assertEqual("fixture", payload["profile"])
        self.assertEqual("FX01", payload["oracle"])
        self.assertEqual(scenarios.PASS, payload["verdict"])
        self.assertEqual("TASK_COMPLETE", payload["runner_status"])
        self.assertTrue(payload["checks"])
        self.assertTrue(all(check["ok"] for check in payload["checks"]))


class FixModeTest(unittest.TestCase):
    """`--mode fix` drives `autocode fix` and is judged by the same independent oracle."""

    def test_bugfix01_fixture_fix_mode_reaches_pass_with_cost(self):
        with tempfile.TemporaryDirectory(prefix="live-trial-fix-") as temp, \
                mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(Path(temp) / "evidence")}):
            code = live_trial.main(["BUGFIX-01", "--mode", "fix", "--profile", "fixture",
                                    "--workspace", str(Path(temp) / "trial"), "--timeout", "120"])
            payload = json.loads(next(Path(temp, "evidence").glob("BUGFIX-01/*/live-trial.json")).read_text())
        self.assertEqual(0, code)
        self.assertEqual((scenarios.PASS, "READY", "fix"),
                         (payload["verdict"], payload["runner_status"], payload["mode"]))
        self.assertEqual(1, payload["cost"]["model_calls"])

    def test_fix_status_classification(self):
        self.assertEqual("complete", live_trial.classify_fix_status("READY"))
        for status in ("NEEDS_REVIEW", "NEEDS_INPUT", "UNVERIFIED", "ENV_BROKEN"):
            self.assertEqual("paused", live_trial.classify_fix_status(status))
        for status in ("FAILED", "ERROR", ""):
            self.assertEqual("stopped", live_trial.classify_fix_status(status))

    def test_fix_command_maps_profile_roles(self):
        command = live_trial.fix_command(Path("/p"), profiles.resolve("glm53-openai"), "Fix it")
        self.assertEqual("openai/gpt-6-sol", command[command.index("--model") + 1])
        self.assertEqual("zai-coding-plan/glm-5.3", command[command.index("--reviewer-model") + 1])
        self.assertEqual("opencode", command[command.index("--provider") + 1])

    def test_fixture_fix_mode_refuses_scenarios_without_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(live_trial.TrialError, "reference delivery"):
                live_trial.install_fix_fixture(Path(temp), "LIVE-01")


class DrivingBoundsTest(unittest.TestCase):
    def test_deadline_is_shared_across_cli_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            states = [{"status": "WAITING_FOR_USER", "pending_questions": [{"id": "Q1", "options": ["yes"]}]},
                      {"status": "TASK_COMPLETE"}, {"status": "TASK_COMPLETE"}]
            with (mock.patch.object(live_trial.time, "monotonic", side_effect=[0, 2, 5]),
                  mock.patch.object(live_trial, "_discover_run_dir", return_value=root),
                  mock.patch.object(live_trial, "load_state", side_effect=states),
                  mock.patch.object(live_trial, "invoke", return_value=subprocess.CompletedProcess([], 0, "", "")) as invoke):
                live_trial.drive(root, root, profiles.resolve("fixture"), "task", 8, 20, mock.Mock())
            self.assertEqual([18, 15], [call.args[-1] for call in invoke.call_args_list])

    def test_unhandled_pause_stops_without_blind_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (mock.patch.object(live_trial, "_discover_run_dir", return_value=root),
                  mock.patch.object(live_trial, "load_state", return_value={"status": "PAUSED_USAGE_UNKNOWN"}),
                  mock.patch.object(live_trial, "invoke", return_value=subprocess.CompletedProcess([], 2, "", "")) as invoke):
                result = live_trial.drive(root, root, profiles.resolve("fixture"), "task", 8, 20, mock.Mock())
            self.assertEqual("PAUSED_USAGE_UNKNOWN", result["state"]["status"])
            self.assertEqual(1, invoke.call_count)

    def test_review_uses_public_cli_and_artifact_token(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "human_review", "criteria": ["C1"]},
                 "displayed_review": "artifact-token"}
        step = mock.Mock()
        self.assertTrue(live_trial._serve_gate(state, HERE, HERE, profiles.resolve("fixture"), step))
        self.assertEqual(["--approve-review", "C1", "--review-token", "artifact-token"], step.call_args.args[1][-4:])
        del state["displayed_review"]
        with self.assertRaises(live_trial.TrialError):
            live_trial._serve_gate(state, HERE, HERE, profiles.resolve("fixture"), step)

    def test_timeout_stops_provider_in_separate_process_group(self):
        import psutil
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "child.pid"
            script = ("import subprocess,sys,time; from pathlib import Path; "
                      "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                      f"Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(60)")
            with self.assertRaisesRegex(live_trial.TrialError, "budget exhausted"):
                live_trial.invoke([sys.executable, "-c", script], dict(os.environ), root, 1)
            pid = int(marker.read_text())
            self.assertFalse(psutil.pid_exists(pid))


class ProgramModeTest(unittest.TestCase):
    """`--mode program` drives `autocode program run` and serves each child run's gates."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="program-mode-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(self.root / "artifacts")})
        env.start()
        self.addCleanup(env.stop)
        self.project = self.root / "project"
        self.project.mkdir()
        self.integration = self.root / "integration"
        self.integration.mkdir()
        self.child_run = self.root / "child-run"
        self.child_run.mkdir()
        self.calls: list[list[str]] = []
        self.program_status = ["WAITING", "COMPLETE"]

    def fake_invoke(self, cmd, env, cwd, timeout):
        self.calls.append(cmd)
        if cmd[2:4] == ["program", "run"]:
            status = self.program_status.pop(0)
            child_status = "AWAITING_GOAL_APPROVAL" if status == "WAITING" else "TASK_COMPLETE"
            (self.child_run / "state.json").write_text(json.dumps(
                {"status": child_status, "displayed_goal": "r1:abc"}))
            summary = {"status": status, "state_file": str(self.root / "state.json"),
                       "integration_workspace": str(self.integration),
                       "workstreams": [{"id": "contracts", "status": "WAITING" if status == "WAITING" else "MERGED",
                                        "run_dir": str(self.child_run), "workspace": str(self.root / "wt")}]}
            return subprocess.CompletedProcess(cmd, 2 if status != "COMPLETE" else 0, json.dumps(summary), "")
        if "--approve-goal" in cmd:
            (self.child_run / "state.json").write_text(json.dumps({"status": "RUNNING"}))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")

    def test_program_command_passes_profile_flags_without_authorizing_deployment(self):
        cmd = live_trial.program_command(self.project, profiles.resolve("fixture"), self.root / "program.json")
        self.assertEqual(["program", "run"], cmd[2:4])
        self.assertNotIn("--authorize-deployment", cmd)
        self.assertIn("--joint-planning", cmd)
        self.assertNotIn("--in-place", cmd)
        self.assertNotIn("--no-chat", cmd)

    def test_program_command_authorizes_deployment_only_when_explicit(self):
        cmd = live_trial.program_command(self.project, profiles.resolve("fixture"), self.root / "program.json",
                                         authorize_deployment=True)
        self.assertEqual(1, cmd.count("--authorize-deployment"))

    def test_program_deadline_is_shared_across_gate_steps(self):
        bundle = mock.Mock()
        self.program_status = ["WAITING", "COMPLETE"]
        with (mock.patch.object(live_trial.time, "monotonic", side_effect=[0, 2, 5, 8]),
              mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke) as invoke):
            live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                     {"version": 1}, 8, 20, bundle)
        self.assertEqual([18, 15, 12], [call.args[-1] for call in invoke.call_args_list])

    def test_cli_deployment_opt_in_is_independent_of_live_spend(self):
        spec = {"title": "Fixture", "program_manifest": {"version": 1, "name": "x"},
                "oracle_name": "T", "oracle": lambda project: scenarios.OracleResult(scenarios.PASS, "ok", [])}
        for profile in ("fixture", "glm53"):
            for authorize in (False, True):
                with self.subTest(profile=profile, authorize=authorize):
                    self.calls.clear()
                    self.program_status = ["WAITING", "COMPLETE"]
                    flags = ["--i-authorize-live-model-spend"] if profile != "fixture" else []
                    if authorize:
                        flags.append("--authorize-deployment")
                    with (mock.patch.object(live_trial, "make_workspace", return_value=self.project),
                          mock.patch.object(scenarios, "scenario", return_value=spec),
                          mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke)):
                        code = live_trial.main(["PROGRAM-01", "--mode", "program", "--profile", profile,
                                                "--workspace", str(self.root), *flags])
                    self.assertEqual(0, code)
                    self.assertEqual(3, len(self.calls))
                    for cmd in self.calls:
                        is_program = cmd[2:4] == ["program", "run"]
                        self.assertEqual(authorize and is_program, "--authorize-deployment" in cmd)
                        self.assertNotIn("--i-authorize-live-model-spend", cmd)

    def test_gates_are_served_per_child_and_the_product_is_the_integration_worktree(self):
        bundle = Bundle("PROGRAM-TEST")
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                           {"version": 1, "name": "x"}, 20, 60, bundle)
        self.assertEqual("COMPLETE", run["state"]["status"])
        self.assertEqual(self.integration, run["product"])
        kinds = [c[2:4] == ["program", "run"] for c in self.calls]
        self.assertEqual([True, False, True], kinds)
        self.assertTrue(all("--authorize-deployment" not in cmd for cmd in self.calls))
        approve = self.calls[1]
        self.assertIn("--approve-goal", approve)
        self.assertEqual(str(self.root / "wt"), approve[approve.index("--workspace") + 1])
        self.assertEqual(str(self.child_run), approve[approve.index("--run-dir") + 1])

    def test_a_program_stop_without_a_servable_gate_is_never_promoted(self):
        self.program_status = ["BLOCKED"]
        bundle = Bundle("PROGRAM-TEST")

        def blocked(cmd, env, cwd, timeout):
            self.calls.append(cmd)
            summary = {"status": "BLOCKED", "state_file": str(self.root / "s.json"),
                       "integration_workspace": str(self.integration),
                       "workstreams": [{"id": "contracts", "status": "FAILED"}]}
            return subprocess.CompletedProcess(cmd, 2, json.dumps(summary), "")

        with mock.patch.object(live_trial, "invoke", side_effect=blocked):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                           {"version": 1, "name": "x"}, 20, 60, bundle)
        self.assertEqual(1, len(self.calls))
        spec = {"oracle": lambda project: scenarios.OracleResult(scenarios.PASS, "ok", []), "oracle_name": "T"}
        verdict = live_trial.judge(run, spec, self.integration, bundle)
        self.assertEqual(scenarios.ERROR, verdict.status)
        self.assertEqual("paused", live_trial.classify_program_status("AUTHORIZATION_REQUIRED"))
        self.assertEqual("paused", live_trial.classify_program_status("PAUSED_MERGE_CONFLICT"))
        self.assertEqual("complete", live_trial.classify_program_status("COMPLETE"))

    def test_mode_program_needs_a_manifest_and_score_only_scores_without_driving(self):
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            self.assertEqual(2, live_trial.main(["LIVE-01", "--mode", "program", "--workspace", str(self.root)]))
        self.assertEqual([], self.calls)
        import scenario_references as references
        product = self.root / "delivered"
        references.write(references.BUGFIX_REFERENCE, product)
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            self.assertEqual(0, live_trial.main(["BUGFIX-01", "--score-only", str(product)]))
        self.assertEqual([], self.calls)
        report = json.loads(next((self.root / "artifacts").glob("BUGFIX-01/*/live-trial.json")).read_text())
        self.assertEqual(("PASS", "score-only", "bugfix"), (report["verdict"], report["mode"], report["task_type"]))


if __name__ == "__main__":
    unittest.main()
