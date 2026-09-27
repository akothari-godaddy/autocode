#!/usr/bin/env python3
"""Live-model validation trial for the astra_diagnose operational-recovery route.

This is the deferred "live validation" step from issue #61's AutoResolver
recovery plan: at least one real model diagnosis, on a genuine failure, with
an independently verified outcome. It requires real provider credentials
this session did not have, so it is written to be run later -- on a machine
with ``opencode`` installed and authenticated -- rather than run here.

What it proves, and what it does not
-------------------------------------
The trial seeds one small, clearly labeled defective candidate (a real bug,
with a regression test proving the seed fails and the reference fix passes),
drives it through the real ``autocode`` CLI with a real model profile, and:

1. Lets the Builder make a genuine first attempt. If that attempt's report is
   accepted, astra_diagnose is never reached -- this is recorded as
   ``NOT_EXERCISED``, per the plan's own allowance ("if it never reaches
   AutoResolver, record NOT_EXERCISED... rather than counting it as resolver
   success or forcing a model to fail"). The trial does not manufacture a
   rejection to force the route; a well-formed first attempt is a valid,
   if unexercising, outcome.
2. If the first attempt's report is genuinely rejected (real evidence, a
   real defect in the report itself -- e.g. a bad evidence citation -- not
   fabricated), the trial captures that real record, then MECHANICALLY
   raises its repeat count to the policy's 3-occurrence threshold using that
   same real evidence. This step makes no model call and is logged plainly
   as bookkeeping, not as three organic live failures: forcing a real,
   well-behaved model to fail identically three times in a row is neither
   reliable nor useful (a first-attempt success or a differently-worded
   second failure would prove nothing about the diagnosis itself). What the
   trial actually puts under live test is the diagnosis call and the
   verified retry, both genuine.
3. Runs ``--resume-paused --diagnose-failed-stage`` for real: astra_diagnose
   receives the real rejected report and error, and a real model returns a
   diagnosis and a bounded retry-or-escalate recommendation.
4. On "retry": resumes normally, so the real Builder gets a real second
   attempt carrying the model's guidance, and the real Reviewer/Validator
   independently check it. The trial records the actually-observed verdict;
   it does not accept the runner's own completion claim (see ``judge``).
   On "escalate": records that outcome. A correct escalate on a genuinely
   ambiguous defect is a valid, scoreable result, not a failure of the trial.

Scoring is deliberately not automatic. The evidence bundle places the
seeded defect's author-labeled ground truth beside the model's diagnosis
text for a human to compare, per the plan ("keyword presence alone is not
diagnosis correctness"). The trial checks only what is mechanically
checkable: that the recommendation obeys its schema and boundaries, that a
retry actually re-dispatched the original stage, and that the final verdict
came from independent review, not from the runner's own claim.

Usage
-----
    # Offline, free: proves the harness itself, and the negative controls
    # (see test_live_diagnosis_trial.py for the same controls as unit tests).
    python3 tools/live_diagnosis_trial.py --profile fixture

    # On a machine with opencode installed and `opencode auth login` done,
    # using the profile the issue's plan names:
    python3 tools/live_diagnosis_trial.py --profile glm53-mimo \\
        --i-authorize-live-model-spend

Read the printed evidence directory afterward; ``diagnosis-comparison.json``
is the file to read side by side with the seeded defect's own description.

Note on ``--profile fixture``: the scripted fixture provider is written for
the LIVE-01..LIVE-05 task-type scenarios, not this trial's celsius task, and
its scripted planning responses do not reproduce a genuine terra (Builder)
rejection for this task. Running it here is expected to print
``astra_diagnose: NOT_EXERCISED`` -- that proves the driving, escalation and
reporting code runs cleanly end to end and correctly declines to
misattribute a non-target pause, not that the fixture organically reaches
astra_diagnose. The admission -> astra_diagnose -> retry mechanic itself is
already proven offline, through the real CLI, by
tools/test_resolver_runtime.py's OperationalDiagnosisTests. Only a live
profile puts a real model in front of this trial's actual seeded defect.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_profiles as profiles  # noqa: E402
import live_trial as base  # noqa: E402
from autopilot_testkit import Bundle, source_revision  # noqa: E402

TrialError = base.TrialError

# --- the seeded defect ----------------------------------------------------
# A real, small, single-cause bug: the additive constant is 31, not 32. The
# test asserts both the freezing and boiling points, so a partial/incorrect
# fix (e.g. patching only one call site) still fails independent validation.
SEED_MODULE = '''"""Temperature conversion. Seeded defect: see convert() below."""


def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 31  # BUG: should be + 32
'''

SEED_TEST = '''"""Regression tests: stdlib only, run as `python3 test_convert.py`."""
import unittest
from convert import celsius_to_fahrenheit


class ConversionTests(unittest.TestCase):
    def test_freezing_point(self):
        self.assertEqual(32, celsius_to_fahrenheit(0))

    def test_boiling_point(self):
        self.assertEqual(212, celsius_to_fahrenheit(100))

    def test_body_temperature(self):
        self.assertAlmostEqual(98.6, celsius_to_fahrenheit(37), places=2)


if __name__ == "__main__":
    unittest.main()
'''

REFERENCE_MODULE = '''"""Temperature conversion."""


def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 32
'''

GROUND_TRUTH = (
    "The additive constant in celsius_to_fahrenheit is 31 instead of 32 "
    "(a single-character typo: '+ 31' should read '+ 32'). All three "
    "conversions are off by exactly one degree Fahrenheit as a result."
)

TASK = (
    "Fix the bug in celsius_to_fahrenheit inside convert.py so that "
    "celsius_to_fahrenheit(0) == 32, celsius_to_fahrenheit(100) == 212, and "
    "celsius_to_fahrenheit(37) is within 0.01 of 98.6. Do not change "
    "test_convert.py or the function's name or signature. Python standard "
    "library only."
)


def prove_seed_and_reference(bundle: Bundle) -> None:
    """Regression proof, run before any model is involved: the seed fails
    its own test and the reference fix passes it -- exactly the labeled,
    reproducible defect the plan requires, not a claim taken on faith."""
    with tempfile.TemporaryDirectory(prefix="diagnosis-trial-proof-") as tmp:
        root = Path(tmp)
        (root / "convert.py").write_text(SEED_MODULE)
        (root / "test_convert.py").write_text(SEED_TEST)
        seed_result = subprocess.run([sys.executable, "test_convert.py"],
                                     capture_output=True, text=True, cwd=root)
        (root / "convert.py").write_text(REFERENCE_MODULE)
        reference_result = subprocess.run([sys.executable, "test_convert.py"],
                                          capture_output=True, text=True, cwd=root)
    bundle.log("regression_proof", seed_exit=seed_result.returncode, seed_tail=seed_result.stdout[-400:],
               reference_exit=reference_result.returncode, reference_tail=reference_result.stdout[-400:])
    if seed_result.returncode == 0:
        raise TrialError("seeded defect does not actually fail its own test; fix the fixture")
    if reference_result.returncode != 0:
        raise TrialError("reference fix does not pass the seed's own test; fix the fixture")


# --- driving the real CLI to a genuine first Builder verdict --------------

def drive_to_first_verdict(project: Path, root: Path, profile: dict,
                           budget_stages: int, timeout: int, bundle: Bundle) -> dict:
    """Serve ordinary gates for real until the run reaches a terminal state,
    a genuine PAUSED_REPEATED_FAILURE, or a genuine rejected Builder report
    becomes visible. Unlike live_trial.drive, this stops at the first sign
    of the condition this trial needs, rather than trying to finish the run.
    """
    env = dict(os.environ, AUTOCODE_HOME=str(root / "registry"),
               PYTHONDONTWRITEBYTECODE="1")
    if profile["provider"] == "fixture":
        env.update(base.install_fixture_provider(root))
        env.pop("AUTOCODE_PROVIDER", None)

    steps: list[dict] = []
    started = time.monotonic()

    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        if time.monotonic() - started > timeout:
            raise TrialError(f"wall-clock budget exceeded after {len(steps)} CLI steps")
        if len(steps) >= budget_stages:
            raise TrialError(f"stage budget exceeded after {len(steps)} CLI steps")
        bundle.log("cli_step", kind=kind, cmd=cmd)
        proc = base.invoke(cmd, env, root, timeout)
        record = {"kind": kind, "cmd": cmd, "returncode": proc.returncode,
                  "stdout_tail": proc.stdout[-800:], "stderr_tail": proc.stderr[-800:]}
        steps.append(record)
        bundle.log("cli_result", kind=kind, returncode=proc.returncode,
                   stdout_tail=record["stdout_tail"], stderr_tail=record["stderr_tail"])
        if proc.returncode not in allow_codes:
            raise TrialError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
        return proc

    step("start", base.autocode_command(project, profile, TASK, None, []))
    run_dir = base._discover_run_dir(project)
    if run_dir is None:
        raise TrialError("first invocation did not create a run directory")
    bundle.log("run_dir", path=str(run_dir))

    def terra_report_repair_identity(state: dict) -> dict | None:
        """The one condition this trial's astra_diagnose route accepts: a
        report-repair-eligible identity whose original stage is terra. A
        PAUSED_REPEATED_FAILURE or rejected row from any other stage (e.g. a
        planning-stage rejection) is real, but is not this trial's target and
        must not be misreported as astra_diagnose's trigger.
        """
        pending = state.get("pending_report_repair") or {}
        original = pending.get("original")
        if original and original.get("stage") == "terra" and original.get("failure_key"):
            return original
        for row in reversed(state.get("stages", [])):
            if row.get("stage") == "terra" and row.get("failure_key"):
                return row
        return None

    for _ in range(budget_stages):
        state = base.load_state(run_dir)
        status = state.get("status", "")
        bundle.state(f"gate.{len(steps)}", {k: state.get(k) for k in (
            "status", "phase", "next_stage", "displayed_goal", "pending_questions")})

        if terra_report_repair_identity(state):
            return {"state": state, "steps": steps, "run_dir": run_dir, "outcome": "rejected"}
        if status in ("TASK_COMPLETE", "COMPLETE"):
            return {"state": state, "steps": steps, "run_dir": run_dir, "outcome": "complete"}
        if status.startswith("PAUSED_") and not (state.get("pending_questions") or state.get("user_request")):
            # A genuine pause this trial does not know how to serve further
            # (e.g. a non-terra repeated failure, or an unrelated blocker) and
            # that is not this trial's target condition. Stop cleanly rather
            # than attempt an unproductive resume.
            return {"state": state, "steps": steps, "run_dir": run_dir, "outcome": "blocked"}

        if base._serve_gate(state, run_dir, project, profile, step):
            continue

        before_state = state
        step("resume", base.autocode_command(project, profile, None, run_dir, []))
        after = base.load_state(run_dir)
        if (after.get("status", "") == before_state.get("status", "")
                and not base._progressed(before_state, after)):
            raise TrialError(f"run is stuck at {after.get('status')!r} with no gate and no progress")

    final = base.load_state(run_dir)
    return {"state": final, "steps": steps, "run_dir": run_dir, "outcome": "budget_exhausted"}


def escalate_to_repeat_threshold(run_dir: Path, bundle: Bundle) -> None:
    """Mechanically raise a real rejected report's repeat count to the
    policy's 3-occurrence threshold, from the one real occurrence already
    on disk. No model call happens here; this is bookkeeping so the live
    model call under test (the diagnosis itself) can proceed on real
    evidence without gambling on three organic identical failures.
    """
    state = base.load_state(run_dir)
    pending = state.get("pending_report_repair") or {}
    original = pending.get("original")
    # Scoped to terra exactly like admit_operational_diagnosis itself: a real
    # rejection at a different stage exists, but is not this trial's target
    # and must never be mistaken for it.
    failure_key = original.get("failure_key") if original and original.get("stage") == "terra" else None
    if not failure_key:
        original = next((row for row in reversed(state.get("stages", []))
                          if row.get("stage") == "terra" and row.get("failure_key")), None)
        failure_key = original.get("failure_key") if original else None
    if not failure_key or failure_key not in state.get("failure_history", {}):
        raise TrialError("no real rejected Builder (terra) report with a failure_key was found to escalate")
    entry = state["failure_history"][failure_key]
    bundle.log("mechanical_escalation", failure_key=failure_key, real_count_before=entry.get("count"),
               real_last_error=entry.get("last_error"), note="bookkeeping only; no model call")
    entry["count"] = 3
    state["status"] = "PAUSED_REPEATED_FAILURE"
    (run_dir / "state.json").write_text(json.dumps(state, indent=2, default=str))


def diagnose_and_retry(project: Path, root: Path, profile: dict, run_dir: Path,
                       budget_stages: int, timeout: int, bundle: Bundle) -> dict:
    """The two genuine live-model steps: admit + run astra_diagnose, then,
    on an accepted retry, let the real Builder and Reviewer finish for real.
    """
    env = dict(os.environ, AUTOCODE_HOME=str(root / "registry"),
               PYTHONDONTWRITEBYTECODE="1")
    if profile["provider"] == "fixture":
        env.update(base.install_fixture_provider(root))
        env.pop("AUTOCODE_PROVIDER", None)

    def invoke_cli(extra: list[str], allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        cmd = base.autocode_command(project, profile, None, run_dir, extra)
        bundle.log("cli_step", kind="diagnose", cmd=cmd)
        proc = base.invoke(cmd, env, root, timeout)
        bundle.log("cli_result", kind="diagnose", returncode=proc.returncode,
                   stdout_tail=proc.stdout[-800:], stderr_tail=proc.stderr[-800:])
        if proc.returncode not in allow_codes:
            raise TrialError(f"diagnose step exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
        return proc

    # Admit: real dispatch path (autocode.main --resume-paused --diagnose-failed-stage).
    invoke_cli(["--resume-paused", "--diagnose-failed-stage"])
    state = base.load_state(run_dir)
    if state.get("status") not in ("RUNNING",) or state.get("next_stage") != "astra_diagnose":
        return {"admitted": False, "state": state}

    # The real model call: run the admitted astra_diagnose stage.
    invoke_cli([])
    state = base.load_state(run_dir)
    diagnosis_record = next((row for row in reversed(state.get("stages", []))
                             if row.get("stage") == "astra_diagnose"), None)
    if diagnosis_record is None:
        raise TrialError("astra_diagnose did not produce a recorded stage after the model call")
    diagnosis_value = json.loads(Path(diagnosis_record["output"]).read_text())
    recommendation = diagnosis_value.get("recommendation", {})
    bundle.log("diagnosis_received", diagnosis=diagnosis_value.get("diagnosis"),
               recommendation=recommendation)

    result = {"admitted": True, "diagnosis": diagnosis_value.get("diagnosis"),
              "recommendation": recommendation, "state": state}
    if recommendation.get("action") != "retry":
        return result

    # Accepted retry: the real Builder gets a genuine second attempt, then
    # real independent review. Drive normally (ordinary gates only) to a
    # terminal or blocked state; do not trust the runner's own completion
    # claim (see judge_final_verdict). _serve_gate expects a step(kind, cmd,
    # allow_codes=...) callback, so wrap invoke_cli's extra-args interface.
    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        bundle.log("cli_step", kind=kind, cmd=cmd)
        proc = base.invoke(cmd, env, root, timeout)
        bundle.log("cli_result", kind=kind, returncode=proc.returncode,
                   stdout_tail=proc.stdout[-800:], stderr_tail=proc.stderr[-800:])
        if proc.returncode not in allow_codes:
            raise TrialError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
        return proc

    for _ in range(budget_stages):
        state = base.load_state(run_dir)
        status = state.get("status", "")
        if status in ("TASK_COMPLETE", "COMPLETE") or (status.startswith("PAUSED_") and not base._resumable(state)):
            break
        if base._serve_gate(state, run_dir, project, profile, step):
            continue
        before = state
        step("resume", base.autocode_command(project, profile, None, run_dir, []))
        after = base.load_state(run_dir)
        if after.get("status") == before.get("status") and not base._progressed(before, after):
            break
    result["state"] = base.load_state(run_dir)
    return result


def judge_final_verdict(project: Path, run_dir: Path) -> dict:
    """Independent scoring: re-run the seed's own test against the delivered
    workspace. The runner's own status is recorded for comparison, but the
    verdict here never comes from it.
    """
    proc = subprocess.run([sys.executable, "test_convert.py"],
                          capture_output=True, text=True, cwd=project)
    state = base.load_state(run_dir)
    return {"independent_test_exit": proc.returncode, "independent_test_tail": proc.stdout[-800:],
            "runner_status": state.get("status")}


# --- entry point -----------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="fixture",
                        help="model profile from live_profiles (default: fixture)")
    parser.add_argument("--workspace", type=Path,
                        help="parent directory for the disposable project (default: temp)")
    parser.add_argument("--budget-stages", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--i-authorize-live-model-spend", action="store_true",
                        help="required for any non-fixture profile")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profile = profiles.resolve(args.profile)
    if profile["provider"] != "fixture" and not args.i_authorize_live_model_spend:
        print(f"refusing live spend: rerun with --i-authorize-live-model-spend "
              f"(profile={args.profile}, model={profiles.describe(profile)})", file=sys.stderr)
        return 2

    bundle = Bundle("DIAGNOSIS-TRIAL")
    bundle.log("trial_started", profile=args.profile, model=profiles.describe(profile),
               authorized=args.i_authorize_live_model_spend, ground_truth=GROUND_TRUTH)

    prove_seed_and_reference(bundle)

    temp = None
    if args.workspace:
        root = Path(args.workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temp = tempfile.TemporaryDirectory(prefix="autopilot-diagnosis-trial-")
        root = Path(temp.name).resolve()

    try:
        project = base.make_workspace(root)
        (project / "convert.py").write_text(SEED_MODULE)
        (project / "test_convert.py").write_text(SEED_TEST)
        subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(project), "-c", "user.name=LiveTrial",
                        "-c", "user.email=live@example.test", "commit", "-qm", "seed defective candidate"],
                       check=True)
        bundle.log("seeded", files=["convert.py", "test_convert.py"])

        first = drive_to_first_verdict(project, root, profile, args.budget_stages, args.timeout, bundle)
        bundle.state("first_verdict", {k: first["state"].get(k) for k in ("status", "phase", "next_stage")})

        payload = {"profile": args.profile, "profile_detail": profile, "ground_truth": GROUND_TRUTH,
                  "source": source_revision(), "outcome": first["outcome"]}

        if first["outcome"] == "complete":
            verdict = judge_final_verdict(project, first["run_dir"])
            payload.update(astra_diagnose="NOT_EXERCISED", reason="first attempt already valid",
                           final_verdict=verdict)
        elif first["outcome"] not in ("rejected",):
            payload.update(astra_diagnose="NOT_EXERCISED",
                           reason=f"drive stopped at {first['outcome']!r} before any Builder rejection")
        else:
            if first["state"].get("status") != "PAUSED_REPEATED_FAILURE":
                escalate_to_repeat_threshold(first["run_dir"], bundle)
                payload["repeat_count_mechanically_escalated"] = True
            else:
                payload["repeat_count_mechanically_escalated"] = False
            diagnosis = diagnose_and_retry(project, root, profile, first["run_dir"],
                                          args.budget_stages, args.timeout, bundle)
            payload["astra_diagnose"] = "EXERCISED" if diagnosis["admitted"] else "ADMISSION_REFUSED"
            payload["diagnosis"] = diagnosis.get("diagnosis")
            payload["recommendation"] = diagnosis.get("recommendation")
            if diagnosis.get("recommendation", {}).get("action") == "retry":
                payload["final_verdict"] = judge_final_verdict(project, first["run_dir"])

        report_path = bundle.dir / "diagnosis-comparison.json"
        report_path.write_text(json.dumps(payload, indent=2, default=str))
        bundle.finish(base.scenarios.PASS,
                      f"astra_diagnose={payload.get('astra_diagnose', 'NOT_EXERCISED')}")
        print(f"astra_diagnose: {payload.get('astra_diagnose', 'NOT_EXERCISED')}")
        print(f"evidence: {bundle.dir}")
        print(f"read {report_path} next to the ground truth above for human scoring")
        return 0
    except TrialError as error:
        bundle.finish(base.scenarios.ERROR, str(error))
        print(f"ERROR: {error}", file=sys.stderr)
        print(f"evidence: {bundle.dir}", file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
