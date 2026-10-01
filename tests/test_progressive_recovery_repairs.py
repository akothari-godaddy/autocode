"""Real CLI recovery/accounting gates with offline providers and a fake clock.

Only provider waiting and storage can fail here. Planning, approval, run_role,
report validation, independent replay and restart reconciliation run unchanged.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import datetime as dt
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_progressive_artifacts as artifacts
import autocode_usage as usage
import autocode_util as util
from scenarios.harness import catalog
from scenarios.harness.driver import fake_setup
from scenarios.harness.oracle import run as command
from scenarios.harness.project import materialize


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PROVIDER = ROOT / "scenarios" / "catalog" / "progressive-learning-journey" / "recovery_provider.py"


class InterruptedCommit(BaseException):
    """Process death, deliberately outside the CLI's ordinary error handlers."""


class FakeClock:
    def __init__(self):
        self.seconds = 0

    def monotonic(self):
        return self.seconds

    def advance(self, seconds):
        self.seconds += seconds

    def now(self):
        return (dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=self.seconds)).isoformat()


class CLIJourney:
    """Fixture builder, not another unittest or a replacement controller."""

    def __init__(self, root, *, duration=None, fault="progressive_happy"):
        self.root = Path(root).resolve()
        self.scenario = dataclasses.replace(catalog.load("progressive-learning-journey"), fake_fault=fault)
        self.project = materialize(self.scenario.seed, self.root / "project")
        self.flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        self.env = {**env, "AUTOCODE_HOME": str(self.root / "registry"),
                    "CODEX_HOME": str(self.root / "codex-home"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.run_dir = None
        self.clock = FakeClock()
        self.duration = duration or (lambda record: 1000 if record["stage"] in ("terra", "sol", "astra_review") else 100)
        self.calls = []
        self.invocations = []

    def wait(self, child, timeout, checkpoint, **kwargs):
        # Execute the real provider binary and its commands. Only transport wait
        # and elapsed-time reporting are replaced; no reports or exits invented.
        output = Path(child.args[child.args.index("-o") + 1])
        state_path = output.parents[2] / "state.json"
        record = json.loads(state_path.read_text())["active_stage"]
        exit_code = child.wait(timeout=30)
        seconds = self.duration(record)
        self.clock.advance(seconds)
        ended = dt.datetime.fromisoformat(self.clock.now()).timestamp()
        os.utime(record["events"], (ended, ended))
        self.calls.append({"stage": record["stage"], "output": str(output),
                           "seconds": seconds, "exit_code": exit_code, "task_id": record.get("task_id")})
        return exit_code, False

    def call(self, *extra, task=None, action=False):
        argv = ["autocode", *([task] if task else []), "--workspace", str(self.project),
                *(["--run-dir", str(self.run_dir)] if self.run_dir else ["--in-place"]),
                *([] if action else ["--no-chat", *self.flags]), *extra]
        out, err = io.StringIO(), io.StringIO()
        try:
            with patch.dict(os.environ, self.env), patch.object(sys, "argv", argv), \
                    patch.object(runner, "time", types.SimpleNamespace(monotonic=self.clock.monotonic)), \
                    patch.object(runner, "now", side_effect=self.clock.now), \
                    patch.object(runner.processes, "wait_for_stage", side_effect=self.wait), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = runner.main()
        finally:
            self.invocations.append({"args": extra, "stdout": out.getvalue(), "stderr": err.getvalue()})
            if self.run_dir is None:
                paths = list((self.project / ".autocode" / "runs").glob("*/state.json"))
                if paths:
                    self.run_dir = paths[0].parent
        if code not in (0, 2):
            raise AssertionError(f"CLI returned {code}: {err.getvalue()}")
        return code, out.getvalue(), err.getvalue()

    def status(self):
        code, text, err = self.call("--status", action=True)
        if code:
            raise AssertionError(err or text)
        return json.loads(text)

    def state(self):
        # Read the owner's durable evidence, never synthesize or write state.
        return json.loads((self.run_dir / "state.json").read_text())

    def step(self):
        view = self.status()["view"]
        need = view["needs"]
        if need["kind"] == "approve_plan":
            self.call("--approve-goal", need["token"], action=True)
        elif need["kind"] == "resume" and view["status"] == "PAUSED_REQUESTED":
            self.call("--resume-paused", "--pause-after-stage")
        elif need["kind"] == "continue":
            self.call("--pause-after-stage")
        else:
            boundary = {key: view.get(key) for key in ("status", "needs", "next_stage", "stop_reason", "current_task")}
            raise AssertionError(f"Unexpected CLI boundary: {boundary}; last invocation: {self.invocations[-2]}")

    def until(self, predicate, *, steps=80):
        for _ in range(steps):
            status = self.status()
            if predicate(status):
                return status
            if status["view"]["done"]:
                raise AssertionError("CLI completed before the expected recovery boundary")
            self.step()
        raise AssertionError("CLI did not reach its bounded observation boundary")

    def start(self):
        self.call("--pause-after-stage", task=self.scenario.brief)

    def install_recovery_provider(self, fixture):
        spec = importlib.util.spec_from_file_location("recovery_fixture", FIXTURE_PROVIDER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        configuration = Path(self.env["SCENARIO_FAKE_CONFIG"])
        config = json.loads(configuration.read_text())
        config["recovery_fixture"] = fixture
        if fixture == "pools":
            reference = self.root / "reference"
            shutil.copytree(self.scenario.reference, reference)
            (reference / "practice.py").write_text(helper.practice_source(11))
            (self.project / "practice.py").write_text(helper.practice_source(1))
            (self.project / "test_pool_journey.py").write_text(helper.project_checks())
            config.update(reference=str(reference), paths=[*config["paths"], "practice.py"],
                          check="python3 -m unittest test_journey test_pool_journey")
            brief = self.scenario.brief + " Deliver ten distinct practice exercises numbered 2 through 11 with durable answers and next-exercise recommendations."
            config["brief"] = brief
            self.scenario = dataclasses.replace(self.scenario, brief=brief)
        configuration.write_text(json.dumps(config))
        shutil.copy2(FIXTURE_PROVIDER, self.root / "bin" / "codex")
        (self.root / "bin" / "codex").chmod(0o755)
        self.env["SCENARIO_BASE_FAKE_CODEX"] = str(ROOT / "scenarios" / "harness" / "fake_codex.py")


class RaiseOnce:
    def __init__(self, journey, boundary):
        self.journey, self.boundary = journey, boundary
        self.fired = False
        self.candidate = None
        self.previous = None
        self.identity = None
        self.bytes = None
        self.inode = None

    def selected(self, state):
        ledger = state.get("progressive") or {}
        if self.boundary == "checkpoint":
            return bool(ledger.get("history")) and ledger["history"][-1]["slice_id"] == "S1"
        return (ledger.get("active", {}).get("definition", {}).get("id") == "S2"
                and not ledger.get("transition"))

    def capture(self, identity, state=None):
        self.fired = True
        self.identity = copy.deepcopy(identity)
        self.previous = self.journey.state()
        self.candidate = copy.deepcopy(state)
        path = self.journey.run_dir / identity["path"]
        self.bytes, self.inode = path.read_bytes(), path.stat().st_ino
        raise InterruptedCommit(self.boundary)

    def state_write(self, real_write, path, state):
        if (not self.fired and Path(path).resolve() == (self.journey.run_dir / "state.json").resolve()
                and self.selected(state) and not self.selected(self.journey.state())):
            ledger = state["progressive"]
            identity = ledger["history"][-1]["artifact"] if self.boundary == "checkpoint" else ledger["active"]["review"]
            self.capture(identity, state)
        return real_write(path, state)

    def publish(self, real_link, source, destination, **kwargs):
        result = real_link(source, destination, **kwargs)
        kind = "checkpoint" if self.boundary == "checkpoint" else "review"
        if not self.fired and str(destination).startswith(kind + "-"):
            identity = {"version": 1, "path": "progressive/" + destination,
                        "sha256": destination.removeprefix(kind + "-").removesuffix(".json")}
            self.capture(identity)
        return result


class ProgressiveRecoveryRepairs(unittest.TestCase):
    def fixture(self, **kwargs):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="progressive-recovery-"))
        return CLIJourney(directory, **kwargs)

    def assert_usage(self, journey, state=None):
        state = state or journey.state()
        provider_stages = [row for row in state["stages"] if not row.get("runner_owned")]
        outputs = []
        for row in provider_stages:
            # Nonzero transports have no report to archive; their actual
            # launch identity is still pinned by the archived event-log map.
            events = next((original for original, archived in row.get("archived_paths", {}).items()
                           if archived == row["events"]), row["events"])
            outputs.append(str(Path(events).with_suffix(".json")))
        self.assertEqual(len(outputs), len(set(outputs)), "A completed provider was recorded twice")
        self.assertEqual(set(outputs), {call["output"] for call in journey.calls})
        totals = {key: 0 for key in usage.TOKEN_KEYS}
        for row in provider_stages:
            events = [json.loads(line) for line in Path(row["events"]).read_text().splitlines()]
            for event in events:
                if event["type"] in ("turn.completed", "turn.failed"):
                    for key in totals:
                        totals[key] += event.get("usage", {}).get(key, 0)
        self.assertGreater(totals["input_tokens"], 0)
        self.assertGreater(totals["output_tokens"], 0)
        self.assertEqual(usage.summary(state)["tokens"], totals)
        rows = [json.loads(line) for line in (journey.project / ".autocode" / usage.LEDGER).read_text().splitlines()]
        self.assertEqual(len(rows), 1, "Crash/resume must upsert, not duplicate the project's usage row")
        self.assertEqual(rows[0]["run"], journey.run_dir.name)
        for key, value in usage.summary(state).items():
            self.assertEqual(rows[0][key], value)
        return copy.deepcopy(rows[0])

    def assert_time(self, journey, state=None):
        state = state or journey.state()
        ledger = state["progressive"]["budget"]
        seconds = sum(call["seconds"] for call in journey.calls)
        self.assertEqual(state["active_seconds"], seconds)
        self.assertEqual(ledger["run_seconds"], seconds)
        self.assertEqual(sum(row["seconds"] for row in ledger["time_receipts"].values()), seconds)
        self.assertEqual(sum(pool["seconds_used"] for pool in ledger["pools"].values()), seconds)
        self.assertEqual({receipt["attempt"] for receipt in ledger["time_receipts"].values()},
                         {call["output"] for call in journey.calls})
        self.assertEqual(len(ledger["time_receipts"]), len(journey.calls))
        self.assertEqual(sum(row["seconds"] for row in state.get("milestone_progress", {}).values()),
                         sum(call["seconds"] for call in journey.calls if call["task_id"]),
                         "Whole-product stage history lost or duplicated a slice's elapsed time")
        return ledger

    def assert_unflagged_accounting(self, state, records):
        # Normalize copies of genuinely completed public-stage records, never
        # alter the persisted owner or manufacture accepted reports/receipts.
        probe = json.loads(json.dumps(state))
        before = copy.deepcopy(probe)
        for recorded in records:
            recovered = json.loads(json.dumps(recorded))
            recovered.pop("accounted", None)
            runner.account_stage(probe, recovered)
            self.assertTrue(recovered["accounted"])
        self.assertEqual(probe, before, "An already charged immutable attempt acquired a second time/review debit")

    def test_default_two_slices_restart_time_and_project_usage(self):
        journey = self.fixture()
        journey.start()
        first = journey.until(lambda status: len((status["view"].get("progressive") or {}).get("demonstrated_slices", [])) == 1)
        self.assertFalse(first["view"]["done"])
        self.assertFalse(first["view"]["progressive"]["current_whole_product_proof"]["verified"])
        self.assertIn("C1", [row["id"] for row in first["view"]["progressive"]["outstanding_product_criteria"]])
        state = journey.state()
        ledger = self.assert_time(journey, state)
        self.assertEqual(ledger["run_limit"], 43200)
        self.assertEqual(first["settings"]["budget_origins"]["max_seconds"], "runner_default")
        self.assertEqual(first["settings"]["budget_origins"]["milestone_max_seconds"], "runner_default")
        initial = next(iter(ledger["pools"].values()))
        self.assertEqual((initial["reviews_used"], initial["review_limit"], initial["seconds_limit"]), (2, 2, 5400))
        prior = copy.deepcopy(ledger)
        usage_before = self.assert_usage(journey, state)
        # Each actual main() invocation reloads the owner; no controller state
        # survives in this fixture, only clock/provider observations and inputs.
        final = journey.until(lambda status: status["view"]["done"])
        self.assertTrue(final["completion_current"])
        self.assertTrue(final["view"]["progressive"]["current_whole_product_proof"]["verified"])
        self.assertEqual([row["slice_id"] for row in final["view"]["progressive"]["demonstrated_slices"]], ["S1", "S2"])
        ledger = self.assert_time(journey)
        self.assertEqual(len(ledger["pools"]), 2)
        self.assertEqual(sorted(pool["reviews_used"] for pool in ledger["pools"].values()), [1, 2])
        self.assertGreater(ledger["run_seconds"], 5400)
        self.assertLess(ledger["run_seconds"], 43200)
        for pool in ledger["pools"].values():
            self.assertEqual((pool["review_limit"], pool["seconds_limit"]), (2, 5400))
            self.assertLessEqual(pool["seconds_used"], 5400)
        for identity, receipt in prior["time_receipts"].items():
            self.assertEqual(ledger["time_receipts"][identity], receipt)
        row = self.assert_usage(journey)
        self.assertGreater(row["tokens"]["input_tokens"], usage_before["tokens"]["input_tokens"])
        self.assertEqual([call["stage"] for call in journey.calls].count("terra"), 2)
        self.assert_unflagged_accounting(journey.state(), journey.state()["stages"])
        with patch.object(sys, "path", [str(ROOT / "scenarios"), *sys.path]):
            self.assertTrue(all(result.ok for result in journey.scenario.oracle()(journey.project, journey.scenario)))

    def test_artifact_to_owner_commit_interruption_reconciles_without_provider_replay(self):
        for boundary, publication in (("checkpoint", False), ("activation", False),
                                      ("checkpoint", True), ("activation", True)):
            with self.subTest(boundary=boundary, publication=publication):
                journey = self.fixture()
                journey.start()
                expected = "astra_review" if boundary == "checkpoint" else "astra_finalize"
                journey.until(lambda status: status["view"]["next_stage"] == expected
                              and bool((status["view"].get("progressive") or {}).get("delegation_approved")))
                crash = RaiseOnce(journey, boundary)
                atomic_json = util.atomic_json
                link = artifacts.os.link
                with contextlib.ExitStack() as faults:
                    if publication:
                        faults.enter_context(patch.object(artifacts.os, "link", side_effect=lambda src, dst, **kw: crash.publish(link, src, dst, **kw)))
                    else:
                        faults.enter_context(patch.object(util, "atomic_json", side_effect=lambda path, state: crash.state_write(atomic_json, path, state)))
                    try:
                        journey.step()
                    except InterruptedCommit:
                        pass
                    else:
                        self.fail(f"Storage boundary was not reached: {journey.invocations[-1]}")
                self.assertTrue(crash.fired, "The actual transition never reached its storage fault")
                saved = journey.state()
                self.assertEqual(saved, crash.previous)
                active = saved["active_stage"]
                self.assertEqual(active["stage"], expected)
                self.assertEqual(active["exit_code"], 0)
                self.assertTrue(active["accounted"])
                self.assertTrue(any(event.get("type") == "turn.completed" for event in
                                    (json.loads(line) for line in Path(active["events"]).read_text().splitlines())))
                calls = copy.deepcopy(journey.calls)
                prior_time = copy.deepcopy(saved["progressive"]["budget"])
                path = journey.run_dir / crash.identity["path"]
                self.assertEqual(artifacts.verify(journey.run_dir, crash.identity)["kind"],
                                 "checkpoint" if boundary == "checkpoint" else "review")
                # Reconciliation belongs to the interrupted unit. The ordinary
                # unit handoff stops before the next provider, without stubbing
                # admission or changing state to manufacture a pause.
                journey.call("--unit", "autoreview" if boundary == "checkpoint" else "autoplanner")
                recovered = journey.state()
                self.assertEqual(journey.calls, calls, "Restart launched a provider instead of reconciling its completed turn")
                self.assertNotIn("active_stage", recovered)
                self.assertEqual(path.read_bytes(), crash.bytes)
                self.assertEqual(path.stat().st_ino, crash.inode)
                ledger = self.assert_time(journey, recovered)
                self.assertEqual(ledger["run_seconds"], prior_time["run_seconds"])
                self.assertEqual(ledger["time_receipts"], prior_time["time_receipts"])
                self.assertEqual(ledger["attempts"], prior_time["attempts"])
                self.assertEqual(len(recovered["stages"]), len(saved["stages"]) + 1)
                progressive = recovered["progressive"]
                if boundary == "checkpoint":
                    self.assertEqual([row["slice_id"] for row in progressive["history"]], ["S1"])
                    self.assertEqual(progressive["history"][-1]["artifact"], crash.identity)
                    self.assertEqual(len(ledger["pools"]), 2)
                    self.assertEqual(len(ledger["allocations"]), 2)
                    self.assertEqual(sorted(pool["reviews_used"] for pool in ledger["pools"].values()), [0, 2])
                    self.assertEqual(progressive["transition"]["phase"], "detail")
                else:
                    self.assertEqual(progressive["active"]["definition"]["id"], "S2")
                    self.assertEqual(progressive["active"]["review"], crash.identity)
                    self.assertNotIn("transition", progressive)
                    self.assertEqual(len(ledger["pools"]), len(prior_time["pools"]))
                    self.assertEqual(ledger["pools"], prior_time["pools"])
                    self.assertEqual(ledger["allocations"], prior_time["allocations"])
                self.assert_usage(journey, recovered)
                self.assert_unflagged_accounting(recovered, [row for row in recovered["stages"] if row["output"] == active["output"]])
                final = journey.until(lambda status: status["view"]["done"])
                self.assertTrue(final["completion_current"])
                self.assertEqual([call["stage"] for call in journey.calls].count("terra"), 2)
                self.assertEqual(sum(call["output"] == active["output"] for call in journey.calls), 1)
                self.assert_time(journey)
                row = self.assert_usage(journey)
                journey.call("--status", action=True)
                self.assertEqual(self.assert_usage(journey), row)

    def test_default_local_exhaustion_does_not_reset_usage_when_ceiling_increases(self):
        journey = self.fixture(duration=lambda record: 4800 if record["stage"] == "terra" else 100 if
                               record["stage"] in ("recognize_workflow", "requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize") else 0)
        journey.start()
        journey.until(lambda status: status["view"]["next_stage"] == "sol")
        prior = journey.state()
        self.assertEqual(prior["active_seconds"], 5400)
        self.assertEqual(len(journey.calls), 7)
        admitted = copy.deepcopy(prior["progressive"]["budget"])
        journey.call("--resume-paused", "--pause-after-stage")
        stopped = journey.status()
        self.assertFalse(stopped["view"]["done"])
        self.assertEqual(len(journey.calls), 7, "An exhausted local pool admitted another provider")
        self.assertEqual(journey.state()["progressive"]["budget"], admitted)
        self.assertEqual(admitted["run_limit"], 43200)
        self.assertEqual(next(iter(admitted["pools"].values()))["seconds_limit"], 5400)
        milestones = copy.deepcopy(journey.state()["milestone_progress"])
        code, _, _ = journey.call("--max-milestone-seconds", "6000", "--show-goal", action=True)
        self.assertEqual(code, 0)
        authorized = journey.state()
        self.assertEqual(authorized["active_seconds"], 5400)
        self.assertEqual(authorized["milestone_progress"], milestones)
        ledger = authorized["progressive"]["budget"]
        self.assertEqual(ledger["run_seconds"], admitted["run_seconds"])
        self.assertEqual(ledger["time_receipts"], admitted["time_receipts"])
        self.assertEqual(ledger["attempts"], admitted["attempts"])
        self.assertEqual(next(iter(ledger["pools"].values()))["seconds_limit"], 6000)
        self.assertEqual(len(journey.calls), 7)
        journey.call("--resume-paused", "--max-milestone-seconds", "6000", "--unit", "autocode")
        resumed = journey.state()
        self.assertEqual(len(journey.calls), 7)
        self.assertEqual(resumed["active_seconds"], 5400)
        self.assertEqual(resumed["milestone_progress"], milestones,
                         "Forwarded resume options cleared the progressive milestone's spent history")
        self.assertEqual(resumed["progressive"]["budget"], ledger)
        final = journey.until(lambda status: status["view"]["done"])
        self.assertTrue(final["completion_current"])
        completed = self.assert_time(journey)
        self.assertEqual(completed["run_seconds"], 10400)
        self.assertEqual(sorted(pool["seconds_limit"] for pool in completed["pools"].values()), [5400, 6000])
        self.assert_usage(journey)

    def test_default_43200_frontier_requires_explicit_increase_before_admission(self):
        def duration(record):
            if record["stage"] == "sol" and record.get("task_id"):
                task = journey.state()["current_task"]
                if task.get("slice_id") == "S10":
                    return 1600
            return 1400 if record["stage"] in ("terra", "sol", "astra_review") else 100

        journey = self.fixture(duration=duration)
        journey.install_recovery_provider("pools")
        journey.start()
        boundary = journey.until(lambda status: status["view"].get("progressive", {}).get("allowance_usage", {}).get("run_seconds") == 43200)
        self.assertFalse(boundary["view"]["done"])
        self.assertEqual(boundary["view"]["next_stage"], "astra_review")
        self.assertEqual([row["slice_id"] for row in boundary["view"]["progressive"]["demonstrated_slices"]],
                         [f"S{number}" for number in range(1, 10)])
        prior = journey.state()
        ledger = self.assert_time(journey, prior)
        self.assertEqual((ledger["run_limit"], ledger["defaults"]["run_seconds"]), (43200, 43200))
        self.assertEqual(len(ledger["pools"]), 10)
        self.assertTrue(all(pool["seconds_limit"] == 5400 and pool["seconds_used"] <= 5400 and
                            pool["review_limit"] == 2 for pool in ledger["pools"].values()))
        self.assertEqual(ledger["limit_changes"], {})
        self.assertEqual(len(ledger["allocations"]), 10)
        self.assertEqual(boundary["settings"]["budget_origins"]["max_seconds"], "runner_default")
        self.assertEqual(command([sys.executable, "-m", "unittest", "test_pool_journey.Exercise11"], journey.project).returncode, 1,
                         "The genuinely unallocated final work must still be unbuilt at the frontier")
        calls = copy.deepcopy(journey.calls)
        journey.call("--resume-paused", "--pause-after-stage")
        paused = journey.status()
        self.assertFalse(paused["view"]["done"])
        self.assertEqual(journey.calls, calls, "Default aggregate exhaustion started a provider")
        saved = journey.state()
        self.assertEqual(saved["progressive"]["budget"], ledger, "Default aggregate recovery minted capacity or erased usage")
        self.assertEqual(saved["active_seconds"], 43200)
        self.assertFalse(any(row.get("kind") == "max_seconds" for row in saved.get("resolver", {}).get("budget_extensions", [])))
        self.assertNotIn("active_stage", saved)
        usage_before = self.assert_usage(journey, saved)
        milestones = copy.deepcopy(saved["milestone_progress"])
        # This is the first increase: an actual user action after the actual
        # default frontier, never configuration that hides the default gate.
        code, _, _ = journey.call("--max-seconds", "50000", "--show-goal", action=True)
        self.assertEqual(code, 0)
        authorized = journey.state()
        self.assertEqual(journey.calls, calls)
        self.assertEqual(authorized["active_seconds"], 43200)
        self.assertEqual(authorized["milestone_progress"], milestones)
        updated = authorized["progressive"]["budget"]
        self.assertEqual(updated["run_limit"], 50000)
        for key in ("attempts", "time_receipts", "pools", "allocations", "run_seconds", "defaults"):
            self.assertEqual(updated[key], ledger[key])
        self.assertEqual(self.assert_usage(journey)["tokens"], usage_before["tokens"])
        self.assertEqual(updated["run_limit_provenance"], "user_cli_explicit")
        journey.call("--resume-paused", "--pause-after-stage")
        final = journey.until(lambda status: status["view"]["done"])
        self.assertTrue(final["completion_current"])
        self.assertEqual(final["contract_token"], boundary["contract_token"])
        self.assertEqual(len(final["view"]["progressive"]["demonstrated_slices"]), 11)
        completed = self.assert_time(journey)
        self.assertEqual(completed["run_seconds"], 49000)
        self.assertEqual(completed["run_limit"], 50000)
        self.assertEqual(completed["defaults"]["run_seconds"], 43200)
        self.assertEqual(journey.state()["progressive"]["delegation"]["limits"]["run_max_seconds"], 43200)
        self.assertEqual(final["settings"]["budget_origins"]["max_seconds"], "user_explicit")
        self.assertTrue(all(pool["seconds_limit"] == 5400 and pool["seconds_used"] <= 5400 for pool in completed["pools"].values()))
        self.assert_usage(journey)

    def test_terminal_accounting_commit_crash_recovers_elapsed_once_from_real_events(self):
        journey = self.fixture()
        journey.start()
        journey.until(lambda status: status["view"]["next_stage"] == "sol")
        prior = journey.state()
        fired, terminal = False, None
        write = util.atomic_json

        def interrupt(path, state):
            nonlocal fired, terminal
            active = state.get("active_stage") or {}
            if (not fired and Path(path) == journey.run_dir / "state.json" and active.get("stage") == "sol"
                    and active.get("accounted") and active.get("exit_code") == 0):
                fired, terminal = True, copy.deepcopy(active)
                raise InterruptedCommit("terminal accounting")
            return write(path, state)

        with patch.object(util, "atomic_json", side_effect=interrupt):
            with self.assertRaises(InterruptedCommit):
                journey.step()
        self.assertTrue(fired)
        saved = journey.state()
        self.assertNotIn("accounted", saved["active_stage"])
        self.assertEqual(saved["progressive"]["budget"]["time_receipts"], prior["progressive"]["budget"]["time_receipts"])
        self.assertEqual(terminal["duration_seconds"], 1000)
        calls = copy.deepcopy(journey.calls)
        journey.call("--unit", "autocode")
        recovered = journey.state()
        self.assertEqual(journey.calls, calls)
        self.assertNotIn("active_stage", recovered)
        self.assertEqual(recovered["next_stage"], "astra_review")
        applied = next(row for row in recovered["stages"] if row["output"] == terminal["output"])
        self.assertEqual(applied["duration_seconds"], 1000)
        self.assertTrue(applied["accounted"])
        self.assert_time(journey, recovered)
        self.assert_usage(journey, recovered)
        self.assert_unflagged_accounting(recovered, [applied])
        final = journey.until(lambda status: status["view"]["done"])
        self.assertTrue(final["completion_current"])
        self.assert_time(journey)
        self.assert_usage(journey)

    def test_nonzero_review_refunds_once_without_refunding_time_across_restart(self):
        journey = self.fixture()
        journey.install_recovery_provider("review_nonzero")
        journey.start()
        journey.until(lambda status: status["view"]["next_stage"] == "astra_finalize" and
                      bool(status["view"].get("progressive", {}).get("delegation_approved")))
        prior = journey.state()
        pool_id = prior["progressive"]["pending_allowance"]["pool_id"]
        journey.step()
        failed = journey.state()
        active = copy.deepcopy(failed["active_stage"])
        self.assertEqual((active["stage"], active["exit_code"], active["duration_seconds"]), ("astra_finalize", 1, 100))
        ledger = self.assert_time(journey, failed)
        self.assertTrue(ledger["attempts"][active["output"]]["refunded"])
        self.assertEqual(ledger["pools"][pool_id]["reviews_used"], 0)
        self.assertEqual(ledger["pools"][pool_id]["seconds_used"], 200)
        calls = copy.deepcopy(journey.calls)
        journey.call("--pause-after-stage")
        restored = journey.state()
        self.assertEqual(journey.calls, calls)
        self.assertEqual(restored["progressive"]["budget"], ledger)
        self.assert_unflagged_accounting(restored, [active, active])
        selected = runner.attempt_id(active)
        code, _, err = journey.call("--abandon-stage", selected, action=True)
        self.assertEqual(code, 0, err)
        archived = journey.state()
        self.assertEqual(archived["progressive"]["budget"], ledger)
        self.assert_usage(journey, archived)
        journey.call("--resume-paused", "--pause-after-stage")
        final = journey.until(lambda status: status["view"]["done"])
        self.assertTrue(final["completion_current"])
        completed = self.assert_time(journey)
        self.assertEqual(completed["pools"][pool_id]["reviews_used"], 1)
        self.assertEqual(completed["attempts"][active["output"]], ledger["attempts"][active["output"]])
        self.assertEqual(completed["time_receipts"]["time:" + active["output"]]["seconds"], 100)
        self.assertEqual(len(completed["pools"]), 2)
        self.assert_usage(journey)

    def test_exit_zero_report_repair_consumes_time_not_another_review_after_commit_crash(self):
        journey = self.fixture()
        journey.install_recovery_provider("review_repair")
        journey.start()
        journey.until(lambda status: status["view"]["next_stage"] == "astra_finalize" and
                      bool(status["view"].get("progressive", {}).get("delegation_approved")))
        before = journey.state()
        pool_id = before["progressive"]["pending_allowance"]["pool_id"]
        crash = RaiseOnce(journey, "activation")
        write = util.atomic_json
        with patch.object(util, "atomic_json", side_effect=lambda path, state: crash.state_write(write, path, state)):
            with self.assertRaises(InterruptedCommit):
                journey.step()
        self.assertTrue(crash.fired)
        saved = journey.state()
        active = saved["active_stage"]
        self.assertTrue(active["report_only"])
        self.assertEqual(active["stage"], "astra_finalize_report_repair")
        original = saved["pending_report_repair"]["original"]
        self.assertEqual(original["exit_code"], 0)
        ledger = self.assert_time(journey, saved)
        self.assertEqual(ledger["pools"][pool_id]["reviews_used"], 1)
        original_identity = next(key for key, row in original["archived_paths"].items() if row == original["output"])
        self.assertFalse(ledger["attempts"][original_identity]["refunded"])
        self.assertFalse(ledger["attempts"][active["output"]]["review"])
        self.assertEqual(ledger["time_receipts"]["time:" + original_identity]["seconds"], 100)
        self.assertEqual(ledger["time_receipts"]["time:" + active["output"]]["seconds"], 100)
        calls = copy.deepcopy(journey.calls)
        journey.call("--unit", "autoplanner")
        recovered = journey.state()
        self.assertEqual(journey.calls, calls, "A completed repair/provider was replayed during reconciliation")
        self.assertNotIn("pending_report_repair", recovered)
        self.assertNotIn("active_stage", recovered)
        self.assertEqual(recovered["progressive"]["active"]["definition"]["id"], "S2")
        self.assertEqual(recovered["progressive"]["active"]["review"], crash.identity)
        self.assertEqual(recovered["progressive"]["budget"], ledger)
        self.assert_usage(journey, recovered)
        final = journey.until(lambda status: status["view"]["done"])
        self.assertTrue(final["completion_current"])
        completed = self.assert_time(journey)
        self.assertEqual(completed["pools"][pool_id]["reviews_used"], 1)
        self.assertEqual(sum(call["stage"] == "astra_finalize_report_repair" for call in journey.calls), 1)
        self.assertEqual(sum(call["stage"] == "terra" for call in journey.calls), 2)
        self.assert_usage(journey)


if __name__ == "__main__":
    unittest.main()
