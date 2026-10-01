"""Genuine v2 public-stage recovery with actual CLI ceiling actions.

Planning reports, provider command events and replay receipts use real files.
The lifecycle is driven in-process, not through a complete fake-provider CLI run.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning_artifacts as planning_artifacts
import autocode_progressive_artifacts as artifacts
import autocode_progressive_state as progressive
import autocode_repair_provenance as repair_provenance
import autocode_resolver_human as human
import autocode_resolver_runtime as resolver
import autocode_util as util
import goal_fixtures
from units import autoplanner as planning


COMMAND = "python3 -m unittest test_greeting.py"


class V2ReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="progressive-v2-review-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"],
                       check=True)
        goal_fixtures.seed_greeting_workspace(self.workspace)
        self.run_dir = self.workspace / ".autocode" / "runs" / "v2-review-recovery"
        self.run_dir.mkdir(parents=True)
        self.path = self.run_dir / "state.json"
        self.body = goal_fixtures.body()
        self.body["required_behaviors"] = ["Print the greeting", "Reject invalid input"]
        self.body["milestones"][0]["affected_paths"] = ["greet.py", "test_greeting.py"]
        self.first = {"objective": "Deliver greeting", "affected_paths": ["greet.py", "test_greeting.py"],
            "kind": "validate", "milestone_id": "M1", "requirements": ["Print the greeting"],
            "acceptance_criteria": ["C1"], "validation_plan": [COMMAND]}
        self.proposal = {"version": 1, "needed_because": "Two independently useful CLI capabilities",
            "shared_decisions": ["Local CLI"], "outstanding_criteria": [], "done_slices": [],
            "slices": [self.slice("S1", "A", False), self.slice("S2", "B", True)]}
        self.state = {"version": 3, "task_id": "v2-review-recovery", "task": "Build greeting and rejection",
            "workspace": str(self.workspace), "run_dir": str(self.run_dir), "iteration": 0,
            "answers": {}, "user_events": [], "acceptance_criteria": [], "sessions": {},
            "status": "RUNNING", "phase": "PLANNING", "next_stage": "requirements",
            "settings": {"joint_planning": True, "planning_flow": "v2", "engine": "opencode",
                "roles": {role: {"engine": "opencode", "model": "fixture/" + role}
                          for role in ("requirements", "glm", "plan_reviewer", "sol", "astra")},
                "milestone_checkpoints": {"enabled": True, "max_seconds": 5400},
                "report_repair": {"max_attempts": 2},
                "limits": {"max_seconds": 43200}}}

    def slice(self, sid, check_id, tentative):
        return {"id": sid, "intended_result": "Deliver " + sid, "criterion_ids": ["C1"],
            "paths": ["greet.py", "test_greeting.py"], "depends_on": [], "tentative": tentative,
            "checks": [{"id": check_id, "method": COMMAND, "relation": "contributes_to",
                        "criterion_ids": ["C1"]}]}

    def stage(self, stage, report, *, repair=False, session=None, diagnostic=False, supports_sessions=True):
        self.assertEqual(stage, self.state["next_stage"])
        if stage in planning.V2_STAGES:
            request = planning.prepare(self.state, stage, self.path, self.run_dir)
            self.assertEqual(planning.V2_STAGE_ROLES[stage], request.role)
        provider_stage = stage + "_report_repair" if repair else stage
        output = self.run_dir / (provider_stage + "-" + str(len(self.state.get("stages", []))) + ".json")
        snapshot = util.snapshot(self.workspace)
        record = {"stage": provider_stage, "role": planning.role_for(self.state, stage), "output": str(output),
            "iteration": self.state["iteration"], "source_revision": snapshot["revision"],
            "thread_id": (session or "fixture-" + output.stem) if supports_sessions else None,
            "supports_sessions": supports_sessions, "changed_files": []}
        if repair:
            record.update(report_only=True, original_stage=stage)
        try:
            if not repair:
                planning.charge(self.state, stage, record=record, workspace=self.workspace)
            progressive.admit_attempt(self.state, record, snapshot)
        except util.Paused as error:
            self.state.update(status=error.status, phase="PAUSED_OR_BLOCKED", stop_reason=str(error))
            runner.write_json(self.path, self.state)
            raise
        if self.state.get("current_task"):
            record["task_id"] = self.state["current_task"]["id"]
        self.state["active_stage"] = record
        output.write_text(json.dumps(report))
        events = [{"type": "thread.started", "thread_id": record["thread_id"]}] if supports_sessions else []
        if stage == "sol":
            for check in report["checks"]:
                result = subprocess.run(check["command"], shell=True, cwd=self.workspace,
                                        capture_output=True, text=True, check=True)
                events.append({"type": "item.completed", "item": {
                    "id": check["evidence_ref"].removeprefix("event:"), "type": "command_execution",
                    "command": check["command"], "exit_code": result.returncode,
                    "aggregated_output": result.stdout + result.stderr}})
        events.append({"type": "turn.completed"})
        event_path = output.with_suffix(".jsonl")
        event_path.write_text(("Provider diagnostic before JSON events\n" if diagnostic else "")
            + "\n".join(json.dumps(row) for row in events) + "\n")
        record.update(events=str(event_path), exit_code=0, duration_seconds=10)
        runner.account_stage(self.state, record)
        if repair:
            runner.accept_repaired_report(self.state, self.run_dir, self.workspace, copy.deepcopy(report), record)
        else:
            runner.commit_stage_result(self.state, stage, copy.deepcopy(report), record, self.workspace, self.run_dir)
        return record

    def repaired_stage(self, stage, report, invalid, *, session=None, supports_sessions=True):
        with self.assertRaises(ValueError) as raised:
            self.stage(stage, invalid, session=session, diagnostic=True, supports_sessions=supports_sessions)
        original = self.state["active_stage"]
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run_dir, original, raised.exception)
        return self.stage(stage, report, repair=True)

    def plan_report(self, **extra):
        return {"summary": "Reviewed progressive product", "contract": {
            **copy.deepcopy(self.body), "initial_task": copy.deepcopy(self.first)},
            "progressive_proposal": copy.deepcopy(self.proposal), **extra}

    def approve(self):
        human.evaluate(self.state)
        lifecycle.present(self.state)
        public = human.current(self.state)
        self.assertEqual("goal_approval", public["scope"])
        human.require_response(self.state, public["request_id"], public["request_token"])
        runner.write_json(self.path, self.state)
        self.cli("--approve-goal", goals.token(self.state["goal_contract"]))

    def initial(self, *, repaired_final=False):
        requirements = copy.deepcopy(self.body)
        for field in goals.BRIEF_FIELDS:
            requirements.pop(field)
        self.stage("requirements", {"requirements": requirements, "summary": "Whole product requirements"})
        self.stage("plan", self.plan_report())
        self.stage("plan_review", {"summary": "No concerns", "concerns": []})
        self.stage("plan_revise", self.plan_report(responses=[]))
        if repaired_final:
            self.repaired_stage("plan_finalize", self.plan_report(decisions=[]), self.plan_report())
        else:
            self.stage("plan_finalize", self.plan_report(decisions=[]))
        self.approve()
        self.assertEqual([row.get("original_stage") or row["stage"] for row in self.state["stages"]
            if not row.get("rejected")], list(planning.V2_STAGES))
        self.assertEqual(self.state["planning"]["astra_calls"], 2)
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S1")
        for stage in ("plan", "plan_review", "plan_revise", "plan_finalize"):
            self.assertIsNotNone(planning_artifacts.verify_predecessor(self.state, stage, self.run_dir))

    def checkpoint_s1(self):
        self.stage("sol", {**goal_fixtures.envelope(self.state), "verdict": "PASS", "findings": [],
            "checks_run": [COMMAND], "unverified_criteria": ["C1"],
            "checks": [{"command": COMMAND, "exit_code": 0, "evidence_ref": "event:check"}],
            "end_to_end_result": {"status": "NOT_VERIFIED", "summary": "Only S1 demonstrated", "evidence_refs": []},
            "criterion_results": [{"id": "C1", "status": "NOT_VERIFIED", "evidence_refs": []}],
            "finding_dispositions": []})
        _, receipts = progressive.validation_receipts(self.state, util.snapshot(self.workspace))
        self.assertEqual(receipts["A"]["status"], "PASS")
        self.assertTrue(receipts["A"]["replayed"])
        self.stage("astra_review", {**goal_fixtures.envelope(self.state), "status": "CONTINUE",
            "progressive_checkpoint": True, "acceptance_criteria": copy.deepcopy(self.state["acceptance_criteria"]),
            "next_objective": "Detail S2", "next_task": {"kind": "none", "milestone_id": "",
                "requirements": [], "acceptance_criteria": [], "validation_plan": [], "findings": []},
            "findings": [], "finding_dispositions": [], "agreed_limitations": [], "evidence": [],
            "blocker": "", "plan": ["Continue approved slices"], "affected_paths": []})
        self.assertEqual("plan_revise", self.state["next_stage"])

    def detail_s2(self, accepted=True, *, repaired_review=False, supports_sessions=True):
        proposal = copy.deepcopy(self.proposal)
        proposal.update(done_slices=["S1"], slices=[copy.deepcopy(proposal["slices"][1])])
        proposal["slices"][0]["tentative"] = False
        self.stage("plan_revise", {"summary": "Detail S2", "progressive_proposal": proposal,
            "initial_task": {**copy.deepcopy(self.first), "objective": "Deliver S2"}})
        review = {"summary": "Independent technical review", "accepted": accepted,
            "product_changes": False, "permission_changes": False, "unresolved_product_decisions": False}
        if repaired_review:
            self.repaired_stage("plan_finalize", review, {key: value for key, value in review.items() if key != "accepted"},
                supports_sessions=supports_sessions)
        else:
            self.stage("plan_finalize", review)

    def cli(self, *arguments, expected=0):
        result = subprocess.run([sys.executable, runner.__file__, "--workspace", str(self.workspace),
            "--run-dir", str(self.run_dir), *arguments], capture_output=True, text=True, timeout=30,
            env={**os.environ, "AUTOCODE_HOME": str(self.workspace / ".autocode" / "registry")})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.state = util.read(self.path)
        return result.stdout

    def renew(self):
        self.body["intended_outcome"] = "NewCycle: greeting and rejection with a revised product goal"
        draft = self.run_dir / "edited-goal.json"
        draft.write_text(json.dumps(self.body))
        self.cli("--edit-goal", str(draft))
        self.assertEqual("plan_review", self.state["next_stage"])
        self.assertFalse(goals.approved(self.state))
        self.proposal["slices"] = [self.slice("S3", "N", False), self.slice("S4", "F", True)]
        self.first["objective"] = "Deliver S3"

    def paused(self, stage, *, repaired_predecessor=False, supports_sessions=True):
        self.initial()
        self.checkpoint_s1()
        if stage == "plan_review":
            self.detail_s2(accepted=False)
        self.detail_s2()
        self.assertEqual("S2", progressive.require_active(self.state)["definition"]["id"])
        self.renew()
        if stage == "plan_finalize":
            self.stage("plan_review", {"summary": "Review revised user goal", "concerns": []})
            if repaired_predecessor:
                self.repaired_stage("plan_revise", self.plan_report(responses=[]), self.plan_report(), supports_sessions=supports_sessions)
            else:
                self.stage("plan_revise", self.plan_report(responses=[]))
        report = ({"summary": "Review revised user goal", "concerns": []} if stage == "plan_review"
                  else self.plan_report(decisions=[]))
        with self.assertRaises(util.Paused) as raised:
            self.stage(stage, report)
        self.assertEqual(raised.exception.status, "PAUSED_PLANNING_BUDGET")
        self.assertEqual(self.state["next_stage"], stage)
        self.assertIsNone(progressive.authorized_detail_reservation(self.state))
        return copy.deepcopy(self.state)

    def test_actual_operator_ceiling_recovers_normal_v2_goal_review_without_reset(self):
        before = self.paused("plan_finalize")
        pool_id = before["progressive"]["active_allowance"]["pool_id"]
        old = before["progressive"]["budget"]
        self.assertEqual(old["pools"][pool_id]["reviews_used"], 2)
        self.assertEqual(before["planning"]["astra_calls"], 1)
        self.cli("--planning-review-call-limit", "4")
        status = json.loads(self.cli("--status"))
        self.assertEqual(status["settings"]["planning_flow"], "v2")
        self.assertEqual(status["settings"]["planning_review_call_limit"], 4)
        usage = status["view"]["progressive"]["allowance_usage"]
        self.assertEqual(usage["pools"][pool_id]["review_limit"], 4)
        self.assertEqual(usage["pools"][pool_id]["reviews_used"], 2)
        for key in ("allocations", "work_pools", "attempts", "time_receipts", "run_seconds", "defaults"):
            self.assertEqual(usage[key], old[key])
        self.assertEqual(self.state["planning"]["astra_calls"], before["planning"]["astra_calls"])
        for key in ("history", "delegation", "plan", "initial_plan", "tasks", "attempts", "required_checks"):
            self.assertEqual(self.state["progressive"][key], before["progressive"][key])
        authorized = copy.deepcopy(self.state)
        self.cli("--planning-review-call-limit", "4")
        self.assertEqual(self.state, authorized)
        self.state = util.read(self.path)
        self.state.update(status="RUNNING", phase="PLANNING")
        self.stage("plan_finalize", self.plan_report(decisions=[]))
        old_token = before["progressive"]["delegation"]["contract_token"]
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, old_token)
        self.approve()
        self.assertNotEqual(goals.token(self.state["goal_contract"]), old_token)
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S3")
        current = self.state["progressive"]
        self.assertEqual(current["budget"]["pools"].keys(), old["pools"].keys())
        self.assertEqual(current["budget"]["pools"][pool_id]["reviews_used"], 3)
        self.assertEqual(current["history"], before["progressive"]["history"])
        self.assertEqual({row["id"] for row in current["required_checks"]}, {"A", "B", "N"})
        self.assertEqual(current["budget"]["allocations"], old["allocations"])
        for identity, attempt in old["attempts"].items():
            self.assertEqual(current["budget"]["attempts"][identity], attempt)
        for row in current["history"]:
            self.assertEqual(artifacts.verify(self.run_dir, row["artifact"])["kind"], "checkpoint")

    def test_normal_v2_first_review_pause_uses_saved_mode_and_current_user_plan_artifact(self):
        before = self.paused("plan_review")
        mismatched = copy.deepcopy(before)
        mismatched["planning_artifacts"]["plan"]["delta"] = copy.deepcopy(before["planning_artifacts"]["plan_review"]["delta"])
        unchanged = copy.deepcopy(mismatched)
        with self.assertRaises(ValueError):
            planning.set_review_call_limit(mismatched, 4)
        self.assertEqual(mismatched, unchanged)
        planning.set_review_call_limit(self.state, 4)
        pool = before["progressive"]["active_allowance"]["pool_id"]
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 2)
        self.assertEqual(self.state["planning"]["astra_calls"], 0)
        self.state.update(status="RUNNING", phase="PLANNING")
        self.stage("plan_review", {"summary": "Review the current edited plan", "concerns": []})
        self.stage("plan_revise", self.plan_report(responses=[]))
        self.stage("plan_finalize", self.plan_report(decisions=[]))
        self.approve()
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 4)
        self.assertEqual(self.state["progressive"]["budget"]["pools"].keys(), before["progressive"]["budget"]["pools"].keys())

    def test_normal_v2_review_rejects_wrong_modes_and_unreconciled_controls_without_mutation(self):
        original = self.paused("plan_finalize")
        changes = {
            "no saved v2 mode": lambda row: row["settings"].pop("planning_flow"),
            "legacy saved mode": lambda row: row["settings"].update(planning_flow="v1"),
            "unknown saved mode": lambda row: row["settings"].update(planning_flow="unknown"),
            "ordinary root mode": lambda row: row["settings"].update(joint_planning=False),
            "v2 stage cannot imply legacy mode": lambda row: row.update(next_stage="astra_finalize"),
            "live worker": lambda row: row.update(active_stage={"pid": 123}),
            "report repair": lambda row: row.update(pending_report_repair={"attempts": 1}),
            "uncertain artifacts": lambda row: row.update(uncertain_artifacts="unreconciled"),
            "pending question": lambda row: row.update(pending_questions=[{"id": "Q1"}]),
            "pending user request": lambda row: row.update(user_request={"kind": "goal_change"}),
            "pending intervention": lambda row: row.update(intervention_ack_pending=True),
            "unissued Resolver proposal": lambda row: row.update({human.PRIVATE: {"scope": "goal_change"}}),
            "forged operational publication": lambda row: row.update({human.PUBLIC: {"scope": "operational_exhaustion"}}),
            "not a budget pause": lambda row: row.update(status="RUNNING"),
            "abandonment is not a v2 budget frontier": lambda row: row.update(status="PAUSED_STAGE_ABANDONED"),
            "stale draft body": lambda row: row["goal_contract"]["body"].update(intended_outcome="Unreviewed"),
        }
        for label, change in changes.items():
            for limit in (4, 0):
                candidate = copy.deepcopy(original)
                change(candidate)
                before = copy.deepcopy(candidate)
                with self.subTest(label=label, limit=limit), self.assertRaises(ValueError):
                    planning.set_review_call_limit(candidate, limit)
                self.assertEqual(candidate, before)
        for limit in (None, True, -1, 1, 2.5, "4"):
            candidate = copy.deepcopy(original)
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                planning.set_review_call_limit(candidate, limit)
            self.assertEqual(candidate, original)

    def test_normal_v2_review_requires_current_accepted_predecessor_and_real_source(self):
        original = self.paused("plan_finalize")
        entry = original["planning_artifacts"]["plan_revise"]
        changes = {
            "missing predecessor": lambda row: row["planning_artifacts"].pop("plan_revise"),
            "unrecorded predecessor": lambda row: row["planning_artifacts"]["plan_revise"]["artifact"].update(path="planning/unrecorded.json"),
            "stale predecessor hash": lambda row: row["planning_artifacts"]["plan_revise"]["artifact"].update(sha256="stale"),
            "stale delta hash": lambda row: row["planning_artifacts"]["plan_revise"]["delta"].update(sha256="stale"),
            "another accepted stage's delta": lambda row: row["planning_artifacts"]["plan_revise"].update(
                delta=copy.deepcopy(row["planning_artifacts"]["plan_review"]["delta"])),
            "accepted Planner delta differs": lambda row: row["planning"]["reports"]["plan_revise"]["artifact"].update(
                delta=copy.deepcopy(row["planning_artifacts"]["plan_review"]["delta"])),
            "missing Planner report": lambda row: row["planning"]["reports"].pop("plan_revise"),
            "mismatched accepted report": lambda row: row["planning"]["reports"]["plan_revise"]["report"].update(summary="Not the accepted report"),
            "missing Planner witness": lambda row: row.update(stages=[stage for stage in row["stages"]
                if stage["output"] != row["planning"]["reports"]["plan_revise"]["output"]]),
            "rejected Planner witness": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == row["planning"]["reports"]["plan_revise"]["output"]).update(rejected=True),
            "stale Planner source": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == row["planning"]["reports"]["plan_revise"]["output"]).update(source_revision="stale"),
        }
        for label, change in changes.items():
            candidate = copy.deepcopy(original)
            change(candidate)
            before = copy.deepcopy(candidate)
            with self.subTest(label=label), self.assertRaises(ValueError):
                planning.set_review_call_limit(candidate, 4)
            self.assertEqual(candidate, before)
        for kind in ("artifact", "delta"):
            path = self.run_dir / entry[kind]["path"]
            content = path.read_bytes()
            try:
                path.write_text("{}\n")
                candidate = copy.deepcopy(original)
                with self.subTest(tampered_file=kind), self.assertRaises(ValueError):
                    planning.set_review_call_limit(candidate, 4)
                self.assertEqual(candidate, original)
            finally:
                path.write_bytes(content)
        goal_fixtures.write_greeting_source(self.workspace, revision="changed after normal v2 revision")
        candidate = copy.deepcopy(original)
        with self.assertRaises(ValueError):
            planning.set_review_call_limit(candidate, 4)
        self.assertEqual(candidate, original)

    def test_cli_rejects_cross_linked_predecessor_delta_without_granting_capacity(self):
        before = self.paused("plan_finalize")
        self.state["planning_artifacts"]["plan_revise"]["delta"] = copy.deepcopy(
            self.state["planning_artifacts"]["plan_review"]["delta"])
        util.atomic_json(self.path, self.state)
        self.cli("--planning-review-call-limit", "4", expected=2)
        self.assertEqual(self.state["progressive"]["budget"], before["progressive"]["budget"])
        self.assertEqual(self.state["user_events"], before["user_events"])

    def test_cli_recovers_accepted_repaired_planner_predecessor_without_reset(self):
        self.paused("plan_finalize", repaired_predecessor=True)
        before = util.read(self.path)
        repair = before["stages"][-1]
        self.assertEqual((repair["stage"], repair["original_stage"]), ("plan_revise_report_repair", "plan_revise"))
        self.assertEqual(before["report_repair_history"][-1]["result"], "accepted")
        self.assertNotIn("pending_report_repair", before)
        self.assertIsNotNone(planning_artifacts.verify_predecessor(before, "plan_finalize", self.run_dir))
        changes = {
            "missing accepted repair": lambda row: row.pop("report_repair_history"),
            "wrong original stage": lambda row: row["stages"][-1].update(original_stage="plan"),
            "changed original events": lambda row: row["stages"][-1].update(applied_original_events=row["stages"][-1]["events"]),
            "stale original source": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == repair["applied_original_events"]).update(source_revision="stale"),
            "fabricated original session": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == repair["applied_original_events"]).update(thread_id="not-in-original-events"),
            "missing original session": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == repair["applied_original_events"]).pop("thread_id"),
            "mismatched expected session": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == repair["applied_original_events"]).update(expected_session="another-session"),
        }
        for label, change in changes.items():
            candidate = copy.deepcopy(before)
            change(candidate)
            unchanged = copy.deepcopy(candidate)
            with self.subTest(label=label), self.assertRaises(ValueError):
                planning.set_review_call_limit(candidate, 4)
            self.assertEqual(candidate, unchanged)
        for metadata in ({"thread_id": "not-in-original-events"}, {"thread_id": None}, {"expected_session": "another-session"}):
            self.state = copy.deepcopy(before)
            next(stage for stage in self.state["stages"]
                if stage.get("events") == repair["applied_original_events"]).update(metadata)
            util.atomic_json(self.path, self.state)
            with self.subTest(metadata=metadata):
                self.cli("--planning-review-call-limit", "4", expected=2)
            self.assertEqual(self.state["progressive"]["budget"], before["progressive"]["budget"])
            self.assertFalse(goals.approved(self.state))
        self.state = copy.deepcopy(before)
        util.atomic_json(self.path, self.state)
        self.cli("--planning-review-call-limit", "4")
        pool = before["progressive"]["active_allowance"]["pool_id"]
        ledger = self.state["progressive"]["budget"]
        self.assertEqual((ledger["pools"][pool]["review_limit"], ledger["pools"][pool]["reviews_used"]), (4, 2))
        for key in ("attempts", "time_receipts", "allocations", "work_pools", "run_seconds"):
            self.assertEqual(ledger[key], before["progressive"]["budget"][key])
        self.assertEqual(self.state["stages"], before["stages"])
        self.assertEqual(self.state["planning"]["astra_calls"], before["planning"]["astra_calls"])
        self.assertFalse(goals.approved(self.state))
        self.state.update(status="RUNNING", phase="PLANNING")
        self.stage("plan_finalize", self.plan_report(decisions=[]))
        self.assertEqual(self.state["planning"]["final_token"], goals.token(self.state["goal_contract"]))
        self.assertFalse(goals.approved(self.state))
        self.assertEqual(self.state["progressive"]["budget"]["pools"].keys(), ledger["pools"].keys())
        final = util.read(self.path)
        original = next(row for row in final["stages"] if row.get("events") == repair["applied_original_events"])
        for label, change in {
            "missing accepted repair": lambda row: row.pop("report_repair_history"),
            "wrong original stage": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == repair["output"]).update(original_stage="plan"),
            "stale original source": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(source_revision="stale"),
            "changed original session": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(thread_id="unverified-session"),
        }.items():
            candidate = copy.deepcopy(final)
            change(candidate)
            unchanged = copy.deepcopy(candidate)
            with self.subTest(label=label), self.assertRaises(ValueError):
                lifecycle.approve(candidate, goals.token(candidate["goal_contract"]))
            self.assertEqual(candidate, unchanged)
        approval_before = copy.deepcopy(self.state)
        self.approve()
        self.assertTrue(goals.approved(self.state))
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S3")
        self.assertEqual(self.state["progressive"]["budget"], approval_before["progressive"]["budget"])
        self.assertEqual(self.state["progressive"]["history"], before["progressive"]["history"])

    def test_cli_initial_approval_accepts_repaired_final_reviewer_without_extra_review_call(self):
        self.initial(repaired_final=True)
        repair = self.state["stages"][-1]
        self.assertEqual(repair["original_stage"], "plan_finalize")
        self.assertEqual(self.state["report_repair_history"][-1]["result"], "accepted")
        pool = self.state["progressive"]["active_allowance"]["pool_id"]
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 2)
        self.assertTrue(goals.approved(self.state))
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S1")

    def test_cli_recovers_and_approves_explicitly_sessionless_repaired_planner(self):
        self.paused("plan_finalize", repaired_predecessor=True, supports_sessions=False)
        before = util.read(self.path)
        repair = before["stages"][-1]
        original = next(row for row in before["stages"] if row.get("events") == repair["applied_original_events"])
        self.assertIs(original["supports_sessions"], False)
        self.assertIsNone(original["thread_id"])
        self.assertEqual(repair_provenance.session(before, repair, "plan_revise"), repair["output"])
        self.cli("--planning-review-call-limit", "4")
        pool = before["progressive"]["active_allowance"]["pool_id"]
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 2)
        self.assertEqual(self.state["progressive"]["budget"]["attempts"], before["progressive"]["budget"]["attempts"])
        self.state.update(status="RUNNING", phase="PLANNING")
        self.stage("plan_finalize", self.plan_report(decisions=[]))
        reviewed_budget = copy.deepcopy(self.state["progressive"]["budget"])
        self.approve()
        self.assertTrue(goals.approved(self.state))
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S3")
        self.assertEqual(self.state["progressive"]["budget"], reviewed_budget)

    def test_cli_recovers_explicitly_sessionless_repaired_rejected_reviewer(self):
        self.initial()
        self.checkpoint_s1()
        self.detail_s2(accepted=False)
        self.detail_s2(accepted=False, repaired_review=True, supports_sessions=False)
        with self.assertRaises(util.Paused):
            self.stage("plan_revise", {"summary": "Retry detail"})
        before = util.read(self.path)
        repair = before["stages"][-1]
        original = next(row for row in before["stages"] if row.get("events") == repair["applied_original_events"])
        self.assertIs(original["supports_sessions"], False)
        self.assertIsNone(original["thread_id"])
        self.assertEqual(repair_provenance.session(before, repair, "plan_finalize"), repair["output"])
        reviewed = artifacts.verify(self.run_dir, before["progressive"]["transition"]["last_review"])["report"]
        self.assertEqual(reviewed["session"], repair["output"])
        self.cli("--planning-review-call-limit", "4")
        pool = before["progressive"]["pending_allowance"]["pool_id"]
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 2)
        self.assertEqual(self.state["progressive"]["budget"]["attempts"], before["progressive"]["budget"]["attempts"])
        self.state.update(status="RUNNING", phase="PLANNING")
        self.detail_s2()
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S2")
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 3)

    def test_cli_renewed_approval_authenticates_repaired_final_reviewer_provenance(self):
        self.paused("plan_finalize")
        self.cli("--planning-review-call-limit", "4")
        self.state.update(status="RUNNING", phase="PLANNING")
        self.repaired_stage("plan_finalize", self.plan_report(decisions=[]), self.plan_report())
        before = util.read(self.path)
        repair = before["stages"][-1]
        original = next(row for row in before["stages"] if row.get("events") == repair["applied_original_events"])
        changes = {
            "missing accepted repair": lambda row: row.pop("report_repair_history"),
            "wrong original stage": lambda row: row["stages"][-1].update(original_stage="plan_review"),
            "changed original events": lambda row: row["stages"][-1].update(applied_original_events=row["stages"][-1]["events"]),
            "changed original role": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(role="not-the-reviewer"),
            "stale original source": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(source_revision="stale"),
            "changed original session": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(thread_id="unverified-session"),
            "missing original session": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).pop("thread_id"),
            "mismatched expected session": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(expected_session="another-session"),
            "sessionful evidence relabeled sessionless": lambda row: next(stage for stage in row["stages"]
                if stage["output"] == original["output"]).update(thread_id=None, supports_sessions=False),
        }
        for label, change in changes.items():
            candidate = copy.deepcopy(before)
            change(candidate)
            unchanged = copy.deepcopy(candidate)
            with self.subTest(label=label), self.assertRaises(ValueError):
                lifecycle.approve(candidate, goals.token(candidate["goal_contract"]))
            self.assertEqual(candidate, unchanged)
        self.state = copy.deepcopy(before)
        next(stage for stage in self.state["stages"] if stage["output"] == original["output"]).update(thread_id="unverified-session")
        util.atomic_json(self.path, self.state)
        self.cli("--approve-goal", goals.token(self.state["goal_contract"]), expected=2)
        self.assertFalse(goals.approved(self.state))
        self.assertEqual(self.state["progressive"]["budget"], before["progressive"]["budget"])
        self.state = copy.deepcopy(before)
        util.atomic_json(self.path, self.state)
        self.approve()
        self.assertTrue(goals.approved(self.state))
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S3")
        self.assertEqual(self.state["progressive"]["budget"], before["progressive"]["budget"])

    def test_repaired_final_review_cannot_reuse_the_planner_session_for_approval(self):
        self.paused("plan_finalize")
        self.cli("--planning-review-call-limit", "4")
        self.state.update(status="RUNNING", phase="PLANNING")
        planner = next(row for row in self.state["stages"]
            if row["output"] == self.state["planning"]["reports"]["plan_revise"]["output"])
        self.repaired_stage("plan_finalize", self.plan_report(decisions=[]), self.plan_report(), session=planner["thread_id"])
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(self.state, before)

    def test_cli_preserves_genuine_issued_operational_exhaustion_recovery(self):
        self.state["settings"]["report_repair"]["max_attempts"] = 0
        requirements = copy.deepcopy(self.body)
        for field in goals.BRIEF_FIELDS:
            requirements.pop(field)
        self.stage("requirements", {"requirements": requirements, "summary": "Ordinary requirements"})
        report = self.plan_report()
        report.pop("progressive_proposal")
        self.stage("plan", report)
        with self.assertRaises(ValueError) as invalid:
            self.stage("plan_review", {"summary": "Malformed first review"})
        with self.assertRaises(util.Paused):
            runner.reject_completed_stage(self.state, self.run_dir, self.state["active_stage"], invalid.exception)
        self.state.update(status="RUNNING", phase="PLANNING")
        self.stage("plan_review", {"summary": "Accepted retry", "concerns": []})
        self.stage("plan_revise", {**report, "responses": []})
        with self.assertRaises(util.Paused):
            self.stage("plan_finalize", {**report, "decisions": []})
        self.cli("--show-goal")
        self.assertTrue(resolver.record_operational_exhaustion(runner, self.state, self.run_dir,
            util.Paused("PAUSED_PLANNING_BUDGET", "Two ordinary reviews used")))
        runner.write_json(self.path, self.state)
        issued = human.current(self.state)
        self.assertEqual(issued["scope"], "operational_exhaustion")
        before = copy.deepcopy(self.state)
        self.cli("--planning-review-call-limit", "4")
        self.assertEqual(self.state["planning"]["astra_calls"], 2)
        self.assertEqual(self.state["planning"]["reports"], before["planning"]["reports"])
        self.assertEqual(self.state["resolver"]["human_escalations"][issued["request_id"]]["status"], "superseded")
        self.assertFalse(self.state.get("pending_questions"))
        self.assertNotIn(human.PUBLIC, self.state)
        self.assertFalse(goals.approved(self.state))

    def test_genuine_v2_rejected_review_detail_recovers_only_authenticated_saved_reservation(self):
        self.initial()
        self.checkpoint_s1()
        self.detail_s2(accepted=False)
        self.detail_s2(accepted=False)
        with self.assertRaises(util.Paused) as raised:
            self.stage("plan_revise", {"summary": "Retry detail"})
        self.assertEqual(raised.exception.status, "PAUSED_PLANNING_BUDGET")
        before = copy.deepcopy(self.state)
        reservation = progressive.authorized_detail_reservation(self.state)
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation["reviews_used"], 2)
        changes = {
            "wrong saved mode": lambda row: row["settings"].update(planning_flow="v1"),
            "wrong stage alias": lambda row: row.update(next_stage="glm_revise"),
            "review, not detail": lambda row: row["progressive"]["transition"].update(phase="review"),
            "material scope issue": lambda row: row["progressive"]["transition"].update(scope_issue="New permission required"),
            "unreviewed candidate": lambda row: row["progressive"]["transition"].pop("last_review"),
            "changed review feedback": lambda row: row["progressive"]["transition"]["feedback"].update(summary="Forged feedback"),
            "stale candidate identity": lambda row: row["progressive"]["transition"]["candidate"].update(sha256="stale"),
            "stale detail source": lambda row: row["progressive"]["transition"]["source"].update(revision="stale"),
            "stale reserved slice": lambda row: row["progressive"]["pending_allowance"].update(slice_id="NEW"),
            "new pool label": lambda row: row["progressive"]["pending_allowance"].update(pool_id="NEW"),
            "pending decision": lambda row: row.update(pending_questions=[{"id": "Q1"}]),
            "unissued Resolver proposal": lambda row: row.update({human.PRIVATE: {"scope": "goal_change"}}),
            "forged operational publication": lambda row: row.update({human.PUBLIC: {"scope": "operational_exhaustion"}}),
        }
        for label, change in changes.items():
            for limit in (4, 0):
                candidate = copy.deepcopy(before)
                change(candidate)
                original = copy.deepcopy(candidate)
                with self.subTest(label=label, limit=limit), self.assertRaises(ValueError):
                    planning.set_review_call_limit(candidate, limit)
                self.assertEqual(candidate, original)
        self.state[human.PUBLIC] = {"scope": "operational_exhaustion"}
        util.atomic_json(self.path, self.state)
        self.cli("--planning-review-call-limit", "4", expected=2)
        self.assertEqual(self.state["progressive"]["budget"], before["progressive"]["budget"])
        self.state = copy.deepcopy(before)
        util.atomic_json(self.path, self.state)
        self.cli("--planning-review-call-limit", "4")
        status = json.loads(self.cli("--status"))
        self.assertEqual(status["settings"]["planning_flow"], "v2")
        self.assertEqual(status["settings"]["planning_review_call_limit"], 4)
        pool = reservation["pool_id"]
        usage = status["view"]["progressive"]["allowance_usage"]
        self.assertEqual((usage["pools"][pool]["reviews_used"], usage["pools"][pool]["review_limit"]), (2, 4))
        self.assertEqual(usage["attempts"], before["progressive"]["budget"]["attempts"])
        self.assertEqual(usage["time_receipts"], before["progressive"]["budget"]["time_receipts"])
        self.state.update(status="RUNNING", phase="PLANNING")
        self.detail_s2()
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S2")
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 3)
        self.assertEqual(self.state["progressive"]["budget"]["allocations"], before["progressive"]["budget"]["allocations"])

    def test_cli_recovers_repaired_rejected_detail_with_authenticated_original_session(self):
        self.initial()
        self.checkpoint_s1()
        self.detail_s2(accepted=False)
        self.detail_s2(accepted=False, repaired_review=True)
        with self.assertRaises(util.Paused):
            self.stage("plan_revise", {"summary": "Retry detail"})
        before = util.read(self.path)
        repair = before["stages"][-1]
        original = next(row for row in before["stages"] if row.get("events") == repair["applied_original_events"])
        reviewed = artifacts.verify(self.run_dir, before["progressive"]["transition"]["last_review"])["report"]
        self.assertEqual(reviewed["session"], original["thread_id"])
        self.assertNotEqual(reviewed["session"], repair["thread_id"])
        changes = {
            "wrong original stage": lambda row: row["stages"][-1].update(original_stage="plan_review"),
            "missing accepted repair": lambda row: row.pop("report_repair_history"),
            "changed repair provenance": lambda row: row["stages"][-1].update(applied_original_events=row["stages"][-1]["events"]),
            "changed original session": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == original["events"]).update(thread_id="not-the-reviewed-session"),
            "stale original source": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == original["events"]).update(source_revision="stale"),
            "mismatched expected session": lambda row: next(stage for stage in row["stages"]
                if stage.get("events") == original["events"]).update(expected_session="another-session"),
        }
        for label, change in changes.items():
            for limit in (4, 0):
                candidate = copy.deepcopy(before)
                change(candidate)
                unchanged = copy.deepcopy(candidate)
                with self.subTest(label=label, limit=limit), self.assertRaises(ValueError):
                    planning.set_review_call_limit(candidate, limit)
                self.assertEqual(candidate, unchanged)
        original_events = Path(original["events"])
        saved_events = original_events.read_bytes()
        try:
            for event in ({"type": "thread.started", "thread_id": "wrong-original-session"},
                          {"type": "step_start", "sessionID": "wrong-opencode-session"}):
                original_events.write_text(json.dumps(event) + "\n")
                candidate = copy.deepcopy(before)
                with self.subTest(event=event), self.assertRaises(ValueError):
                    planning.set_review_call_limit(candidate, 4)
                self.assertEqual(candidate, before)
        finally:
            original_events.write_bytes(saved_events)
        self.state = copy.deepcopy(before)
        next(stage for stage in self.state["stages"] if stage.get("events") == original["events"]).update(expected_session="another-session")
        util.atomic_json(self.path, self.state)
        self.cli("--planning-review-call-limit", "4", expected=2)
        self.assertEqual(self.state["progressive"]["budget"], before["progressive"]["budget"])
        self.state = copy.deepcopy(before)
        util.atomic_json(self.path, self.state)
        self.cli("--planning-review-call-limit", "4")
        pool = before["progressive"]["pending_allowance"]["pool_id"]
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 2)
        for key in ("attempts", "time_receipts", "allocations", "work_pools", "run_seconds"):
            self.assertEqual(self.state["progressive"]["budget"][key], before["progressive"]["budget"][key])
        self.assertEqual(self.state["progressive"]["transition"], before["progressive"]["transition"])
        self.state.update(status="RUNNING", phase="PLANNING")
        self.detail_s2()
        self.assertEqual(progressive.require_active(self.state)["definition"]["id"], "S2")
        self.assertEqual(self.state["progressive"]["budget"]["pools"][pool]["reviews_used"], 3)


if __name__ == "__main__":
    unittest.main()
