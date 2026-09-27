"""Offline tests for the astra_diagnose live-validation trial driver.

These run for free, with no model calls, and prove the trial script's own
code: the seeded defect's regression proof, and the mechanical repeat-count
escalation. They do not exercise astra_diagnose itself -- that mechanic
(admission -> astra_diagnose -> a model's retry recommendation ->
re-dispatch) is already proven offline, through the real CLI, in
tools/test_resolver_runtime.py's OperationalDiagnosisTests. What is new here
is specific to live_diagnosis_trial.py: its fixture harness must not crash,
and it must not misattribute a non-target pause to astra_diagnose's trigger.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_diagnosis_trial as trial  # noqa: E402
from autopilot_testkit import Bundle  # noqa: E402


class SeedIntegrityTests(unittest.TestCase):
    """The plan requires a labeled, reproducible defect: seed fails, reference passes."""

    def test_prove_seed_and_reference_passes_on_the_real_fixture(self):
        bundle = Bundle("DIAGNOSIS-TRIAL-SELFTEST")
        trial.prove_seed_and_reference(bundle)  # raises TrialError on failure
        bundle.finish(trial.base.scenarios.PASS, "seed/reference regression proof holds")

    def test_seed_module_actually_fails_its_own_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "convert.py").write_text(trial.SEED_MODULE)
            (root / "test_convert.py").write_text(trial.SEED_TEST)
            proc = trial.subprocess.run([sys.executable, "test_convert.py"],
                                        capture_output=True, text=True, cwd=root)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("32", proc.stderr + proc.stdout)

    def test_reference_module_passes_the_seed_test_unmodified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "convert.py").write_text(trial.REFERENCE_MODULE)
            (root / "test_convert.py").write_text(trial.SEED_TEST)
            proc = trial.subprocess.run([sys.executable, "test_convert.py"],
                                        capture_output=True, text=True, cwd=root)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_ground_truth_names_the_actual_single_character_defect(self):
        # The comparison file this script writes is read by a human, so the
        # ground truth it states must actually match the seeded source.
        self.assertIn("+ 31", trial.SEED_MODULE)
        self.assertIn("31", trial.GROUND_TRUTH)
        self.assertIn("32", trial.GROUND_TRUTH)


class MechanicalEscalationTests(unittest.TestCase):
    """escalate_to_repeat_threshold: real evidence, zero model calls."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)
        self.bundle = Bundle("DIAGNOSIS-TRIAL-ESCALATION-SELFTEST")

    def write_state(self, state):
        (self.run_dir / "state.json").write_text(json.dumps(state))

    def test_requires_a_real_terra_report_repair_identity(self):
        self.write_state({"pending_report_repair": None, "stages": [], "failure_history": {}})
        with self.assertRaisesRegex(trial.TrialError, "no real rejected Builder \(terra\) report"):
            trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)

    def test_refuses_a_non_terra_identity(self):
        # A genuine rejection at a different stage must never be treated as
        # this trial's terra-scoped target, even if it has a failure_key.
        self.write_state({
            "pending_report_repair": {"original": {"stage": "requirements_gather", "failure_key": "k1"}},
            "stages": [], "failure_history": {}})
        with self.assertRaisesRegex(trial.TrialError, "no real rejected Builder \(terra\) report"):
            trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)

    def test_raises_a_real_single_occurrence_to_the_policy_threshold(self):
        self.write_state({
            "pending_report_repair": {"original": {"stage": "terra", "role": "terra", "failure_key": "k1"}},
            "stages": [{"stage": "terra", "failure_key": "k1"}],
            "failure_history": {"k1": {"count": 1, "last_error": "Missing summary field"}},
            "status": "RUNNING"})
        trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)
        saved = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(3, saved["failure_history"]["k1"]["count"])
        self.assertEqual("PAUSED_REPEATED_FAILURE", saved["status"])
        # The real error text is preserved; escalation only touches the count.
        self.assertEqual("Missing summary field", saved["failure_history"]["k1"]["last_error"])

    def test_falls_back_to_a_stages_row_when_report_repair_already_cleared(self):
        self.write_state({
            "pending_report_repair": None,
            "stages": [{"stage": "terra", "failure_key": "k2"}],
            "failure_history": {"k2": {"count": 1}}})
        trial.escalate_to_repeat_threshold(self.run_dir, self.bundle)
        saved = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(3, saved["failure_history"]["k2"]["count"])


class JudgeFinalVerdictTests(unittest.TestCase):
    """Negative controls for the false-PASS defect: the verdict must score
    against a frozen test file, never the delivered (editable) workspace copy."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE"}))
        self.frozen = root / "frozen_test_convert.py"
        self.frozen.write_text(trial.SEED_TEST)

    def test_a_neutered_delivered_test_does_not_mask_the_real_bug(self):
        (self.project / "convert.py").write_text(trial.SEED_MODULE)  # bug still present
        (self.project / "test_convert.py").write_text("pass\n")  # neutered by the candidate
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertNotEqual(0, verdict["independent_test_exit"])

    def test_a_genuine_fix_passes_the_frozen_test(self):
        (self.project / "convert.py").write_text(trial.REFERENCE_MODULE)
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertEqual(0, verdict["independent_test_exit"])

    def test_a_missing_convert_module_is_reported_not_silently_passed(self):
        (self.project / "test_convert.py").write_text(trial.SEED_TEST)
        verdict = trial.judge_final_verdict(self.project, self.run_dir, self.frozen)
        self.assertIsNone(verdict["independent_test_exit"])
        self.assertIn("missing", verdict["note"])


class SharedDeadlineTests(unittest.TestCase):
    """Negative control for the renewed-timeout defect: one deadline shared
    across every phase, not a fresh budget passed to each subprocess call."""

    def test_drive_step_passes_remaining_time_not_the_full_budget(self):
        recorded = {}

        def fake_invoke(cmd, env, cwd, timeout):
            recorded["timeout"] = timeout
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / ".autocode").mkdir()
            (project / ".autocode" / "runs").mkdir()
            run_dir = project / ".autocode" / "runs" / "fixture"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE"}))
            bundle = Bundle("DIAGNOSIS-TRIAL-DEADLINE-SELFTEST")
            profile = {"provider": "fixture"}
            # 1200s budget, but only ~1 second of it remains: the per-call
            # timeout passed to subprocess must reflect that, not renew to 1200.
            deadline = trial.time.monotonic() + 1.0
            with patch.object(trial.base, "invoke", fake_invoke), \
                 patch.object(trial.base, "_discover_run_dir", return_value=run_dir):
                trial.drive_to_first_verdict(project, root, profile, 40, deadline, bundle)
        self.assertLess(recorded["timeout"], 1.5)

    def test_step_refuses_to_launch_once_the_shared_deadline_has_passed(self):
        bundle = Bundle("DIAGNOSIS-TRIAL-DEADLINE-SELFTEST-2")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with self.assertRaisesRegex(trial.TrialError, "wall-clock budget exceeded"):
                trial.drive_to_first_verdict(project, project, {"provider": "fixture"}, 40,
                                             trial.time.monotonic() - 1.0, bundle)


class ParseArgsTests(unittest.TestCase):
    def test_default_profile_is_the_free_fixture(self):
        args = trial.parse_args([])
        self.assertEqual("fixture", args.profile)
        self.assertFalse(args.i_authorize_live_model_spend)

    def test_live_profile_without_authorization_is_refused_before_any_spend(self):
        with patch.object(sys, "stderr"):
            code = trial.main(["--profile", "glm53-mimo"])
        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
