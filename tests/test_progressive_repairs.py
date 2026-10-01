"""Offline regressions at actual planning, admission, review and completion boundaries."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_progressive_artifacts as artifacts
import autocode_progressive_completion as completion
import autocode_progressive_state as progressive
import autocode_resolver_human as human
import autocode_util as util
import goal_fixtures


LOCAL = "python3 -m unittest test_greeting.GreetingTests.test_greets_a_valid_name"
PRODUCT = "python3 -m unittest test_greeting.py"


class ProgressiveRepairs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        goal_fixtures.seed_greeting_workspace(self.workspace)
        self.run_dir = self.workspace / ".autocode" / "runs" / "repairs"
        self.run_dir.mkdir(parents=True)
        self.attempt = 0
        self.state = {"version": 3, "task_id": "progressive-repairs", "task": "Greeting and rejection",
            "workspace": str(self.workspace), "run_dir": str(self.run_dir), "iteration": 0,
            "answers": {}, "user_events": [], "acceptance_criteria": [], "status": "RUNNING",
            "next_stage": "astra_discovery", "settings": {"joint_planning": True,
                "roles": {"glm": {"model": "fake-planner"}, "astra": {"model": "fake-reviewer"}},
                "milestone_checkpoints": {"enabled": True, "max_seconds": 5400},
                "limits": {"max_seconds": 43200}}}
        self.body = goal_fixtures.body()
        self.body["milestones"][0]["affected_paths"] = ["greet.py", "test_greeting.py"]
        self.first = {"objective": "Deliver greeting", "affected_paths": ["greet.py", "test_greeting.py"],
            "kind": "implement", "milestone_id": "M1", "requirements": ["Print greeting"],
            "acceptance_criteria": ["C1"], "validation_plan": [LOCAL]}
        self.proposal = {"version": 1, "needed_because": "Greeting and rejection are separately useful",
            "shared_decisions": ["Local CLI"], "outstanding_criteria": [], "done_slices": [],
            "slices": [{"id": "S1", "intended_result": "Print greeting", "criterion_ids": ["C1"],
                "paths": ["greet.py", "test_greeting.py"], "depends_on": [], "tentative": False,
                "checks": [{"id": "A", "method": LOCAL, "relation": "contributes_to", "criterion_ids": ["C1"]}]},
                {"id": "S2", "intended_result": "Reject invalid input too", "criterion_ids": ["C1"],
                 "paths": ["greet.py", "test_greeting.py"], "depends_on": ["S1"], "tentative": True,
                 "checks": [{"id": "B", "method": PRODUCT, "relation": "fully_verify", "criterion_ids": ["C1"]}]}]}

    def stage(self, stage, report):
        self.state["next_stage"] = stage
        self.attempt += 1
        output = self.run_dir / f"{stage}-{self.attempt}.json"
        output.write_text(json.dumps(report))
        current = util.snapshot(self.workspace)
        record = {"stage": stage, "role": "glm" if stage in ("astra_discovery", "glm_revise") else "astra",
            "output": str(output), "iteration": self.state["iteration"], "exit_code": 0,
            "duration_seconds": 1, "source_revision": current["revision"], "changed_files": []}
        if stage == "sol":
            record["role"] = "sol"
            events = []
            for check in report["checks"]:
                result = subprocess.run(check["command"], shell=True, cwd=self.workspace,
                                        capture_output=True, text=True)
                events.append({"type": "item.completed", "item": {
                    "id": check["evidence_ref"].removeprefix("event:"), "type": "command_execution",
                    "command": check["command"], "exit_code": result.returncode,
                    "aggregated_output": result.stdout + result.stderr}})
            event_path = output.with_suffix(".jsonl")
            event_path.write_text("\n".join(json.dumps(row) for row in events) + "\n")
            record["events"] = str(event_path)
        if stage in ("astra_challenge", "astra_finalize"):
            runner.planning.charge(self.state, stage, record=record)
        if progressive.view(self.state).get("budget"):
            record["task_id"] = (self.state.get("current_task") or {}).get("id", "")
            progressive.admit_attempt(self.state, record, current)
            self.state["active_stage"] = record
        runner.account_stage(self.state, record)
        runner.apply_result(self.state, stage, copy.deepcopy(report), record, self.workspace, self.run_dir)
        return record

    def plan(self):
        common = {"summary": "Useful slices", "contract_changes": [], "conflict_resolutions": [],
                  "requirement_trace": [], "progressive_proposal": copy.deepcopy(self.proposal)}
        self.stage("astra_discovery", {**common, "contract": copy.deepcopy(self.body),
            "code_refs": ["greet.py"], "alternatives": [], "uncertainties": []})
        self.stage("astra_challenge", {"summary": "No concerns", "concerns": []})
        self.stage("glm_revise", {**common, "contract": copy.deepcopy(self.body),
                                  "code_refs": ["greet.py"], "responses": []})
        self.stage("astra_finalize", {**common, "contract": {**copy.deepcopy(self.body),
                     "initial_task": copy.deepcopy(self.first)}, "decisions": []})
        human.evaluate(self.state)
        lifecycle.present(self.state)

    def approve(self):
        self.plan()
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))

    def validation(self, *, full=False, human_only=False):
        commands = [LOCAL, PRODUCT] if full else [LOCAL]
        statuses = {row["id"]: "PASS" if full else "NOT_VERIFIED" for row in self.body["acceptance_criteria"]}
        if human_only:
            statuses.update({row["id"]: "NOT_VERIFIED" for row in self.body["acceptance_criteria"] if row["human_review"]})
        return {**goal_fixtures.envelope(self.state), "verdict": "BLOCKED" if human_only else "PASS",
            "findings": [], "checks_run": commands, "unverified_criteria": [cid for cid, status in statuses.items() if status != "PASS"],
            "checks": [{"command": command, "exit_code": 0, "evidence_ref": f"event:check-{index}"}
                       for index, command in enumerate(commands)],
            "end_to_end_result": {"status": "PASS" if full else "NOT_VERIFIED", "summary": "Executed CLI",
                                  "evidence_refs": ["event:check-0"] if full else []},
            "criterion_results": [{"id": cid, "status": status,
                                   "evidence_refs": ["event:check-0"] if full else []} for cid, status in statuses.items()],
            "finding_dispositions": []}

    def decision(self, *, complete=False):
        criteria = copy.deepcopy(self.state["acceptance_criteria"])
        if complete:
            for criterion in criteria:
                criterion.update(status="verified", evidence="Current technical evidence and declared acceptance gate")
        return {**goal_fixtures.envelope(self.state), "status": "COMPLETE" if complete else "CONTINUE",
            "progressive_checkpoint": not complete, "acceptance_criteria": criteria,
            "next_objective": "Evaluate original product proof", "next_task": {"kind": "none", "milestone_id": "",
                "requirements": [], "acceptance_criteria": [], "validation_plan": [], "findings": []},
            "findings": [], "finding_dispositions": [], "agreed_limitations": [], "evidence": [], "blocker": "",
            "plan": ["Continue reviewed slices"], "affected_paths": []}

    def next_slice(self):
        self.stage("sol", self.validation())
        self.stage("astra_review", self.decision())
        proposal = copy.deepcopy(self.proposal)
        proposal.update(done_slices=["S1"], slices=[proposal["slices"][1]])
        proposal["slices"][0]["tentative"] = False
        self.stage("glm_revise", {"summary": "Detail S2", "progressive_proposal": proposal,
                                  "initial_task": {**copy.deepcopy(self.first), "validation_plan": [PRODUCT]}})
        self.stage("astra_finalize", {"summary": "Exact independent review", "accepted": True,
            "product_changes": False, "permission_changes": False, "unresolved_product_decisions": False})

    def complete(self):
        self.approve()
        self.next_slice()
        self.stage("sol", self.validation(full=True))
        self.stage("astra_review", self.decision(complete=True))
        self.assertEqual("TASK_COMPLETE", self.state["status"])

    def add_human_review(self):
        self.body["acceptance_criteria"].append({"id": "H1", "criterion": "Human accepts the interaction",
            "verification_method": "Review the local CLI interaction", "human_review": True})
        self.body["milestones"][0]["acceptance_criteria"].append("H1")
        self.proposal["slices"][1]["criterion_ids"].append("H1")
        self.proposal["slices"][1]["checks"][0]["criterion_ids"].append("H1")

    def test_later_continue_cannot_target_future_objective_or_method_with_identical_paths_and_c1(self):
        self.approve()
        original = copy.deepcopy(self.state["current_task"])
        source = util.snapshot(self.workspace)
        future = self.proposal["slices"][1]
        for objective, method in ((future["intended_result"], LOCAL), ("Another task", PRODUCT),
                                  ("Add invalid-input rejection", "python3 -m unittest -v test_greeting.py"),
                                  ("Repair invalid input", "python -m unittest --quiet test_greeting"),
                                  ("A plausible current task", "python3 -c 'print(1)'")):
            with self.subTest(objective=objective, method=method):
                decision = {**self.decision(), "progressive_checkpoint": False,
                    "next_objective": objective, "affected_paths": copy.deepcopy(self.first["affected_paths"]),
                    "next_task": {key: copy.deepcopy(value) for key, value in self.first.items()
                                  if key not in ("objective", "affected_paths")}}
                decision["next_task"]["validation_plan"] = [method]
                with self.assertRaisesRegex(ValueError, "tentative future|not authorized by the active reviewed slice"):
                    lifecycle.assign_task(self.state, decision, source)
                self.assertEqual(original, self.state["current_task"])
                self.assertEqual(source, util.snapshot(self.workspace))
        self.next_slice()
        self.assertEqual("S2", self.state["current_task"]["slice_id"])
        progressive.guard_dispatch(self.state, "terra")

    def test_saved_later_task_cannot_bypass_future_check_admission(self):
        self.approve()
        self.state["current_task"]["validation_plan"] = [PRODUCT]
        with self.assertRaisesRegex(util.Paused, "not authorized by the active reviewed slice"):
            progressive.guard_dispatch(self.state, "terra")

    def test_initial_approval_accepts_current_target_presentation_alias(self):
        self.first["validation_plan"] = [LOCAL.replace("unittest ", "unittest -v ")]
        self.approve()
        self.assertTrue(goals.approved(self.state))
        progressive.guard_dispatch(self.state, "terra")

    def test_initial_approval_accepts_narrow_current_selection_with_interpreter_alias(self):
        self.proposal["slices"][0]["checks"][0]["method"] = "python3 -m unittest test_greeting.GreetingTests"
        self.first["validation_plan"] = [LOCAL.replace("python3", "python").replace("unittest ", "unittest --quiet ")]
        self.approve()
        self.assertTrue(goals.approved(self.state))
        progressive.guard_dispatch(self.state, "terra")

    def test_initial_approval_rejects_broader_future_target_alias(self):
        self.first["validation_plan"] = ["python -m unittest -v test_greeting.py"]
        self.plan()
        events = copy.deepcopy(self.state["user_events"])
        source = util.snapshot(self.workspace)
        with self.assertRaisesRegex(ValueError, "not authorized by the active reviewed slice"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertFalse(goals.approved(self.state))
        self.assertEqual(events, self.state["user_events"])
        self.assertEqual(source, util.snapshot(self.workspace))

    def test_legitimate_current_task_can_share_a_future_criterion_path_and_check(self):
        self.proposal["slices"][1]["checks"][0]["method"] = LOCAL
        self.approve()
        decision = {**self.decision(), "progressive_checkpoint": False, "next_objective": "Finish the current greeting",
            "affected_paths": copy.deepcopy(self.first["affected_paths"]),
            "next_task": {key: copy.deepcopy(value) for key, value in self.first.items()
                          if key not in ("objective", "affected_paths")}}
        lifecycle.assign_task(self.state, decision, util.snapshot(self.workspace))
        self.assertEqual("S1", self.state["current_task"]["slice_id"])
        progressive.guard_dispatch(self.state, "terra")

    def test_ordinary_answer_does_not_archive_or_reconstruct_an_approval(self):
        self.proposal = {"version": 0, "needed_because": "", "shared_decisions": [],
                         "outstanding_criteria": [], "done_slices": [], "slices": []}
        self.approve()
        old_history = copy.deepcopy(self.state["contract_history"])
        lifecycle.wait_for_user(self.state, {"kind": "goal_change", "discovered": "A product choice remains",
            "decision_needed": "Revise the outcome?", "impact": "The outcome changes", "options": [], "proposed_delta": "Revised outcome"})
        human.evaluate(self.state)
        question = human.current(self.state)["questions"][0]
        goals.answer(self.state, question["id"], "Use the revised outcome")
        self.assertEqual(old_history, self.state["contract_history"])
        self.assertFalse(goals.approved(self.state))
        self.assertNotIn("progressive", self.state)

    def test_progressive_product_answer_cannot_restore_an_approval_already_invalidated(self):
        self.approve()
        self.state["goal_contract"].update(approval_status="draft", approval_event=None)
        old_history = copy.deepcopy(self.state["contract_history"])
        lifecycle.wait_for_user(self.state, {"kind": "goal_change", "discovered": "An unapproved choice remains",
            "decision_needed": "Revise the outcome?", "impact": "The outcome changes", "options": [], "proposed_delta": "Revised outcome"})
        human.evaluate(self.state)
        question = human.current(self.state)["questions"][0]
        goals.answer(self.state, question["id"], "Use the revised outcome")
        self.assertEqual(old_history, self.state["contract_history"])
        self.assertFalse(goals.approved(self.state))

    def test_product_answer_preserves_real_old_approval_before_invalidation_and_renewal(self):
        self.approve()
        self.stage("sol", self.validation())
        self.stage("astra_review", self.decision())
        old_contract = copy.deepcopy(self.state["goal_contract"])
        history = copy.deepcopy(self.state["progressive"]["history"])
        request = {"kind": "goal_change", "discovered": "A product decision remains",
            "decision_needed": "Use the revised product outcome?", "impact": "The product outcome changes",
            "options": ["Keep original", "Revise outcome"], "proposed_delta": "Revised greeting product"}
        lifecycle.wait_for_user(self.state, request, origin={"stage": "astra_finalize"})
        human.evaluate(self.state)
        question = human.current(self.state)["questions"][0]
        goals.answer(self.state, question["id"], "Revise the greeting product outcome")
        preserved = [row for row in self.state.get("contract_history", []) if goals.token(row) == goals.token(old_contract)
                     and goals.approved({"goal_contract": row, "user_events": self.state["user_events"]})]
        self.assertEqual([old_contract], preserved)
        self.assertFalse(goals.approved(self.state))
        self.body["intended_outcome"] = "Provide the revised greeting product"
        self.proposal["slices"] = [copy.deepcopy(self.proposal["slices"][1]), copy.deepcopy(self.proposal["slices"][1])]
        self.proposal["slices"][0].update(tentative=False, depends_on=[])
        self.proposal["slices"][1].update(id="S3", intended_result="Demonstrate final product", depends_on=["S2"])
        self.proposal["slices"][1]["checks"][0]["id"] = "C"
        self.first["validation_plan"] = [PRODUCT]
        self.plan()
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertNotEqual(goals.token(old_contract), goals.token(self.state["goal_contract"]))
        self.assertEqual(history, self.state["progressive"]["history"])
        self.assertEqual("S2", progressive.require_active(self.state)["definition"]["id"])

    def adaptive_initial_plan(self):
        self.state["settings"]["adaptive_planning"] = True
        self.body["initial_task"] = copy.deepcopy(self.first)
        common = {"summary": "Useful slices", "contract_changes": [], "conflict_resolutions": [],
                  "requirement_trace": [], "progressive_proposal": copy.deepcopy(self.proposal)}
        self.stage("astra_discovery", {**common, "contract": copy.deepcopy(self.body),
            "code_refs": ["greet.py"], "alternatives": [], "uncertainties": []})
        self.stage("astra_challenge", {"summary": "Early accepted draft", "concerns": []})
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertIsNone(human.current(self.state))
        self.stage("glm_revise", {**common, "contract": copy.deepcopy(self.body), "code_refs": ["greet.py"], "responses": []})
        self.assertEqual("astra_finalize", self.state["next_stage"])
        self.stage("astra_finalize", {**common, "contract": copy.deepcopy(self.body), "decisions": []})
        human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))

    def test_adaptive_progressive_plan_finishes_independent_final_review_before_approval(self):
        self.adaptive_initial_plan()
        self.assertEqual("S1", progressive.require_active(self.state)["definition"]["id"])
        self.assertEqual(2, self.state["planning"]["astra_calls"])

    def test_adaptive_large_scope_uses_bounded_initial_challenge_and_final_review(self):
        self.body["milestones"][0]["affected_paths"] += [f"future{index}.py" for index in range(8)]
        self.adaptive_initial_plan()
        self.assertEqual(2, self.state["planning"]["astra_calls"])
        pool = self.state["progressive"]["budget"]["pools"][self.state["progressive"]["active_allowance"]["pool_id"]]
        self.assertEqual((2, 2), (pool["reviews_used"], pool["review_limit"]))

    def test_human_only_blocked_validation_reaches_existing_gate_without_validator_pass(self):
        self.add_human_review()
        self.approve()
        self.next_slice()
        self.stage("sol", self.validation(full=True, human_only=True))
        self.assertTrue(goals.human_only_pending_validation(self.state, self.state["validation"], "H1"))
        decision = self.decision(complete=True)
        decision["acceptance_criteria"][1].update(status="unverified", evidence="Pending actual human acceptance")
        self.stage("astra_review", decision)
        human.evaluate(self.state)
        lifecycle.present(self.state)
        self.assertEqual("human_review", human.current(self.state)["scope"])
        self.assertEqual("BLOCKED", self.state["validation"]["verdict"])
        self.assertEqual("NOT_VERIFIED", self.state["validation"]["criterion_results"][1]["status"])
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))
        selected = goals.review_token(self.state)
        goals.approve_review(self.state, "H1", selected, util.snapshot(self.workspace))
        self.stage("astra_review", self.decision(complete=True))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assertEqual("BLOCKED", self.state["validation"]["verdict"])
        self.assertEqual("NOT_VERIFIED", self.state["validation"]["criterion_results"][1]["status"])

    def test_human_only_checkpoint_routes_review_without_claiming_human_product_pass(self):
        self.add_human_review()
        self.approve()
        self.next_slice()
        self.stage("sol", self.validation(full=True, human_only=True))
        decision = self.decision()
        decision["acceptance_criteria"][0].update(status="verified", evidence="Current technical checks passed")
        self.stage("astra_review", decision)
        human.evaluate(self.state)
        lifecycle.present(self.state)
        self.assertEqual("human_review", human.current(self.state)["scope"])
        self.assertEqual("unverified", self.state["acceptance_criteria"][1]["status"])
        self.assertEqual(["C1"], self.state["progressive"]["completion_proof"]["criterion_ids"])
        goals.approve_review(self.state, "H1", goals.review_token(self.state), util.snapshot(self.workspace))
        self.stage("astra_review", self.decision(complete=True))
        self.assertEqual("TASK_COMPLETE", self.state["status"])

    def test_human_only_claim_with_missing_cumulative_replay_cannot_request_acceptance(self):
        self.add_human_review()
        self.approve()
        self.next_slice()
        report = self.validation(full=True, human_only=True)
        report["checks"] = report["checks"][1:]
        with self.assertRaisesRegex(ValueError, "omitted cumulative"):
            self.stage("sol", report)
        self.assertIsNone(human.current(self.state))
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))

    def test_entirely_human_acceptance_still_requires_and_receives_real_machine_replay(self):
        self.body["acceptance_criteria"][0]["human_review"] = True
        self.approve()
        self.next_slice()
        self.stage("sol", self.validation(full=True, human_only=True))
        replay = self.state["validation"]["check_replay"]
        self.assertEqual("PASS", replay["verdict"])
        self.assertEqual({LOCAL, PRODUCT}, {row["command"] for row in replay["checks"]})
        self.stage("astra_review", self.decision())
        human.evaluate(self.state)
        lifecycle.present(self.state)
        self.assertEqual("human_review", human.current(self.state)["scope"])
        goals.approve_review(self.state, "C1", goals.review_token(self.state), util.snapshot(self.workspace))
        self.stage("astra_review", self.decision(complete=True))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assertEqual("BLOCKED", self.state["validation"]["verdict"])

    def test_completed_changed_source_refreshes_current_proof_without_new_slice_or_quota(self):
        self.complete()
        history = copy.deepcopy(self.state["progressive"]["history"])
        proof = copy.deepcopy(self.state["progressive"]["completion_proof"])
        allocations = copy.deepcopy(self.state["progressive"]["budget"]["allocations"])
        goal_fixtures.write_greeting_source(self.workspace, revision="after completion")
        runner.recheck_completion(self.state, self.workspace)
        self.assertEqual("PAUSED_STALE_VALIDATION", self.state["status"])
        self.state.update(status="RUNNING", phase="READY_TO_EXECUTE")
        self.stage("sol", self.validation(full=True))
        self.stage("astra_review", self.decision(complete=True))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        ledger = self.state["progressive"]
        self.assertEqual(history, ledger["history"])
        self.assertEqual(allocations, ledger["budget"]["allocations"])
        self.assertIn(proof, ledger["completion_proof_history"])
        refreshed = ledger["completion_proof"]
        self.assertEqual(proof["artifact"], refreshed["refresh_of"])
        self.assertNotEqual(proof["source_revision"], refreshed["source_revision"])
        envelope = artifacts.verify(self.run_dir, refreshed["artifact"])
        self.assertEqual(proof["artifact"]["sha256"], envelope["predecessor_identity"])
        self.assertTrue(all(row["source_revision"] == refreshed["source_revision"] for row in refreshed["results"].values()))
        self.assertTrue(completion.ready(self.state, util.snapshot(self.workspace)))

    def test_multiple_current_proof_refreshes_preserve_the_same_formal_history(self):
        self.complete()
        history = copy.deepcopy(self.state["progressive"]["history"])
        proofs = []
        for revision in ("refresh-one", "refresh-two"):
            proofs.append(copy.deepcopy(self.state["progressive"]["completion_proof"]))
            goal_fixtures.write_greeting_source(self.workspace, revision=revision)
            runner.recheck_completion(self.state, self.workspace)
            self.state.update(status="RUNNING", phase="READY_TO_EXECUTE")
            self.stage("sol", self.validation(full=True))
            self.stage("astra_review", self.decision(complete=True))
            self.assertEqual("TASK_COMPLETE", self.state["status"])
            self.assertEqual(history, self.state["progressive"]["history"])
        self.assertEqual(proofs, self.state["progressive"]["completion_proof_history"])
        self.assertTrue(completion.ready(self.state, util.snapshot(self.workspace)))

    def test_changed_evidence_can_refresh_after_real_revalidation_even_with_same_source(self):
        self.complete()
        before = copy.deepcopy(self.state["progressive"]["completion_proof"])
        evidence = next(iter(before["results"]["A"]["evidence_hashes"]))
        Path(evidence).write_text("Changed old runner evidence\n")
        runner.recheck_completion(self.state, self.workspace)
        self.assertEqual("PAUSED_STALE_VALIDATION", self.state["status"])
        self.state.update(status="RUNNING", phase="READY_TO_EXECUTE")
        self.stage("sol", self.validation(full=True))
        self.stage("astra_review", self.decision(complete=True))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        proof = self.state["progressive"]["completion_proof"]
        self.assertEqual(before["source_revision"], proof["source_revision"])
        self.assertNotEqual(before["results"], proof["results"])
        self.assertEqual(before["artifact"], proof["refresh_of"])
