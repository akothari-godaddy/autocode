"""Read-only proof policy over real temporary artifacts and normalized replay receipts.

These are completion-reader tests, not CLI/provider provenance coverage. The
controller's end-to-end tests independently exercise publication and requests.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import autocode_completion as completion
import autocode_contract_identity as contracts
import autocode_goals as goals
import autocode_human_review_policy as human_review
import autocode_progressive_artifacts as artifacts
import autocode_progressive_completion as reader
import autocode_progressive_plan as plan
import autocode_progressive_state as ledger
import autocode_util as util


class ProgressiveProofRepairs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.source = self.root / "product.py"
        self.source.write_text("VALUE = 7\n", encoding="ascii")
        (self.root / "check_product.py").write_text(
            "import unittest\nfrom product import VALUE\n"
            "class Checks(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertGreater(VALUE, 0)\n"
            "    def test_b(self):\n        self.assertEqual(VALUE, 7)\n", encoding="ascii")
        self.check_a = {"id": "A", "relation": "contributes_to", "criterion_ids": ["C1"],
                        "method": "python -m unittest check_product.Checks.test_a"}
        self.check_b = {"id": "B", "relation": "fully_verify", "criterion_ids": ["C1", "C2"],
                        "method": "python -m unittest check_product.Checks.test_b"}
        first = {"id": "S1", "intended_result": "Persist a useful product result", "criterion_ids": ["C1"],
                 "paths": ["product.py"], "depends_on": [], "tentative": False, "checks": [self.check_a]}
        second = {**deepcopy(first), "id": "S2", "intended_result": "Verify the complete product",
                  "criterion_ids": ["C1", "C2"], "depends_on": ["S1"], "tentative": True,
                  "checks": [self.check_b]}
        self.initial = {"version": 1, "needed_because": "Two independently useful deliveries",
                        "shared_decisions": [], "outstanding_criteria": [], "done_slices": [],
                        "slices": [first, second]}
        self.proposal = {**deepcopy(self.initial), "done_slices": ["S1"],
                         "slices": [{**deepcopy(second), "tentative": False}]}
        self.sequence = 0
        self.build()

    def build(self, humans=()):
        criteria = [{"id": cid, "criterion": "Accept " + cid, "human_review": cid in humans,
                     "verification_method": "Review the delivered interaction" if cid in humans else self.check_b["method"]}
                    for cid in ("C1", "C2")]
        body = {"acceptance_criteria": criteria, "open_blocking_questions": [], "end_to_end_flow": "Use the product",
                **plan.disclosure(self.initial, ["C1", "C2"])}
        contract = {"task_id": "goal-T", "revision": 1, "body": body, "approval_status": "approved"}
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": self.token}
        contract["approval_event"] = event
        self.record = {"version": 1, "delegation": plan.seal_delegation(self.initial, self.token),
                       "initial_plan": {"proposal": deepcopy(self.initial), "plan_hash": plan.plan_identity(self.initial)},
                       "future": [], "outstanding_criteria": list(humans),
                       "required_checks": [deepcopy(self.check_a), deepcopy(self.check_b)]}
        self.state = {"version": 3, "run_dir": str(self.run_dir), "goal_contract": contract,
                      "user_events": [event], "criteria_revision": 1, "progressive": self.record,
                      "current_task": {"id": "task-T", "slice_id": "S2"}, "stages": [],
                      "settings": {"milestone_checkpoints": {"enabled": True}},
                      "acceptance_criteria": [{"id": row["id"], "criterion": row["criterion"],
                                               "status": "unverified", "evidence": ""} for row in criteria]}
        ph = plan.plan_identity(self.proposal)
        bindings = {"contract_token": self.token, "predecessor_identity": self.record["initial_plan"]["plan_hash"],
                    "candidate_identity": ph, "plan_identity": ph, "source_snapshot_identity": "reviewed-source"}
        envelope, _ = artifacts.prepare("revision", report={"proposal": self.proposal}, **bindings)
        candidate = artifacts.persist(self.run_dir, envelope)
        envelope, _ = artifacts.prepare("review", report={"accepted": True, "candidate_sha256": candidate["sha256"]}, **bindings)
        self.record["active"] = {"definition": deepcopy(self.proposal["slices"][0]), "plan_hash": ph,
                                 "artifact": candidate, "review": artifacts.persist(self.run_dir, envelope)}
        self.new_validation(humans)
        past = self.payload(humans=())
        past.update(slice_id="S1", plan_hash="earlier-plan", source_revision="earlier-source",
                    required_checks=[deepcopy(self.check_a)], verified_slices=["S1"], criterion_ids=[])
        past["results"] = {"A": deepcopy(past["results"]["A"])}
        past["results"]["A"].update(source_revision="earlier-source")
        old_artifact = self.checkpoint(past, "earlier-active-artifact")
        self.record["history"] = [{"slice_id": "S1", "plan_hash": "earlier-plan", "artifact": old_artifact,
                                   "required_checks": [deepcopy(self.check_a)]}]
        self.publish(self.payload(humans), formal=True)

    def admit_record(self, stage, role):
        self.sequence += 1
        output = self.run_dir / f"{stage}-{self.sequence}.json"
        output.write_text(json.dumps({"stage": stage, "sequence": self.sequence}), encoding="ascii")
        record = {"stage": stage, "role": role, "output": str(output), "exit_code": 0,
                  "source_revision": self.current["revision"], "changed_files": []}
        binding = {"contract_token": self.token, "plan_hash": self.record["active"]["plan_hash"],
                   "slice_id": "S2", "task_id": "task-T", "assignment_source": "assigned-source",
                   "attempt": self.sequence, "stage": stage, "role": role}
        self.record.setdefault("attempts", {})[str(output)] = binding
        self.state["stages"].append(record)
        return record

    def new_validation(self, humans=()):
        self.current = {"revision": util.digest({"product.py": util.file_hash(self.source)})}
        record = self.admit_record("sol", "sol")
        rows = []
        for index, check in enumerate(self.record["required_checks"]):
            command = plan.check_commands(check)[0]
            result = subprocess.run([sys.executable, "-m", "unittest", command.split()[-1]],
                                    cwd=self.root, capture_output=True, text=True, check=True)
            evidence = self.run_dir / f"replay-{self.sequence}-{index}.txt"
            evidence.write_text(result.stdout + result.stderr, encoding="ascii")
            rows.append({"command": command, "exit_code": result.returncode, "output": str(evidence),
                         "output_sha256": util.file_hash(evidence), "timed_out": False})
        contract = self.state["goal_contract"]
        pins = {row["output"]: row["output_sha256"] for row in rows}
        self.state["validation"] = {"output": record["output"], "reviewer_role": "sol",
            "verdict": "BLOCKED" if humans else "PASS", "source_revision": self.current["revision"],
            "contract_revision": contract["revision"], "contract_hash": contract["hash"], "criteria_revision": 1,
            "task_id": "task-T", "findings": [], "checks": [{"command": row["command"], "exit_code": 0} for row in rows],
            "evidence_hashes": pins, "unverified_criteria": list(humans),
            "criterion_results": [{"id": cid, "status": "NOT_VERIFIED" if cid in humans else "PASS",
                                   "evidence_refs": [next(iter(pins))]} for cid in ("C1", "C2")],
            "end_to_end_result": {"status": "PASS", "summary": "Used the complete product", "evidence_refs": list(pins)},
            "check_replay": {"verdict": "PASS", "source_revision": self.current["revision"], "checks": rows}}
        self.owner = self.admit_record("astra_review", "astra")

    def payload(self, humans=()):
        _, results = ledger.validation_receipts(self.state, self.current, allow_human_pending=True)
        payload = {"version": 1, "contract_token": self.token, "plan_hash": self.record["active"]["plan_hash"],
                   "source_revision": self.current["revision"], "slice_id": "S2",
                   "required_checks": deepcopy(self.record["required_checks"]), "results": results,
                   "criterion_ids": [cid for cid in ("C1", "C2") if cid not in humans], "verified_slices": ["S1", "S2"]}
        if humans:
            payload["pending_human_criteria"] = list(humans)
        return payload

    def checkpoint(self, payload, predecessor):
        envelope, _ = artifacts.prepare("checkpoint", contract_token=payload["contract_token"],
            predecessor_identity=predecessor, candidate_identity=payload["plan_hash"], plan_identity=payload["plan_hash"],
            source_snapshot_identity=payload["source_revision"], report=payload)
        return artifacts.persist(self.run_dir, envelope)

    def publish(self, payload, *, formal=False, predecessor=None):
        if predecessor is None:
            predecessor = (payload["refresh_of"]["sha256"] if "refresh_of" in payload
                           else self.record["active"]["artifact"]["sha256"])
        artifact = self.checkpoint(payload, predecessor)
        self.record["completion_proof"] = {**deepcopy(payload), "artifact": artifact}
        if formal:
            self.record["history"] = self.record["history"][:1] + [{"slice_id": "S2", "plan_hash": payload["plan_hash"],
                "artifact": artifact, "required_checks": deepcopy(payload["required_checks"])}]

    def refresh(self, *, source="VALUE = 7\n# Fresh implementation\n", humans=()):
        previous = deepcopy(self.record["completion_proof"])
        self.source.write_text(source, encoding="ascii")
        self.new_validation(humans)
        payload = self.payload(humans)
        payload["refresh_of"] = deepcopy(previous["artifact"])
        payload["reviewer_receipt"] = {"output": self.owner["output"], "output_sha256": util.file_hash(self.owner["output"]),
            "binding": ledger.check_result_binding(self.state, self.owner, self.current), "stage": "astra_review", "role": "astra"}
        self.record.setdefault("completion_proof_history", []).append(previous)
        self.publish(payload)
        return previous

    def proof_payload(self):
        return {key: deepcopy(value) for key, value in self.record["completion_proof"].items() if key != "artifact"}

    def approve(self, criterion):
        selected = human_review.review_token(self.state)
        self.state["displayed_review"] = selected
        goals.approve_review(self.state, criterion, selected, self.current)

    def decision(self, *, pending=()):
        contract = self.state["goal_contract"]
        return {"status": "COMPLETE", "contract_revision": contract["revision"], "contract_hash": contract["hash"],
                "task_id": "task-T", "findings": [], "acceptance_criteria": [
                    {**row, "status": "unverified" if row["id"] in pending else "verified",
                     "evidence": "" if row["id"] in pending else "Current independent evidence"}
                    for row in deepcopy(self.state["acceptance_criteria"])]}

    def test_human_pending_is_technical_readiness_not_machine_or_product_acceptance(self):
        self.build(["C2"])
        before = deepcopy(self.state)
        self.assertTrue(reader.ready(self.state, self.current, require_human_reviews=False))
        self.assertFalse(reader.ready(self.state, self.current))
        self.assertEqual(before, self.state)
        self.assertEqual(["C1"], self.record["completion_proof"]["criterion_ids"])
        self.assertEqual("BLOCKED", self.state["validation"]["verdict"])
        self.assertEqual("NOT_VERIFIED", self.state["validation"]["criterion_results"][1]["status"])
        self.approve("C2")
        before = deepcopy(self.state)
        self.assertTrue(reader.ready(self.state, self.current))
        self.assertEqual(before, self.state)
        self.assertEqual(self.token, contracts.token(self.state["goal_contract"]))
        self.assertEqual(["C2"], self.record["outstanding_criteria"])
        self.assertEqual("NOT_VERIFIED", self.state["validation"]["criterion_results"][1]["status"])

    def test_all_human_criteria_still_require_nonempty_successful_machine_proof(self):
        self.build(["C1", "C2"])
        self.assertEqual([], self.record["completion_proof"]["criterion_ids"])
        self.assertTrue(reader.ready(self.state, self.current, False))
        self.approve("C1")
        self.assertFalse(reader.ready(self.state, self.current))
        self.approve("C2")
        self.assertTrue(reader.ready(self.state, self.current))
        self.record["completion_proof"]["results"] = {}
        self.assertFalse(reader.ready(self.state, self.current, False))

    def test_declared_unmapped_human_criterion_needs_human_acceptance_not_a_fake_full_check(self):
        self.check_b["criterion_ids"] = ["C1"]
        for proposal in (self.initial, self.proposal):
            proposal["outstanding_criteria"] = ["C2"]
            for row in proposal["slices"]:
                row["criterion_ids"] = ["C1"]
                for check in row["checks"]:
                    check["criterion_ids"] = ["C1"]
        self.build(["C2"])
        self.assertTrue(reader.ready(self.state, self.current, False))
        self.assertFalse(reader.ready(self.state, self.current))
        self.approve("C2")
        self.assertTrue(reader.ready(self.state, self.current))

    def test_shared_gate_forwards_prereview_flag_without_relaxing_final_criteria(self):
        self.build(["C2"])
        decision = self.decision(pending=["C2"])
        self.assertTrue(completion.completion_ready(self.state, decision, self.current, require_human_reviews=False))
        self.assertFalse(completion.completion_ready(self.state, decision, self.current))
        self.approve("C2")
        self.assertFalse(completion.completion_ready(self.state, decision, self.current))
        self.assertTrue(completion.completion_ready(self.state, self.decision(), self.current))
        for decision in (self.decision(pending=["C1", "C2"]), {**self.decision(), "acceptance_criteria": []}):
            self.assertFalse(completion.completion_ready(self.state, decision, self.current, require_human_reviews=False))

    def test_ordinary_human_gate_does_not_gain_progressive_prereview_exception(self):
        self.build(["C2"])
        ordinary = deepcopy(self.state)
        ordinary.pop("progressive")
        body = ordinary["goal_contract"]["body"]
        body.update(constraints=[], technical_approach=[])
        contract = ordinary["goal_contract"]
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        event = {"actor": "user_cli", "token": contracts.token(contract)}
        contract["approval_event"] = event
        ordinary["user_events"] = [event]
        ordinary["validation"]["contract_hash"] = contract["hash"]
        decision = self.decision(pending=["C2"])
        decision["contract_hash"] = contract["hash"]
        self.assertTrue(reader.ready(ordinary, {}, False))
        self.assertFalse(completion.completion_ready(ordinary, decision, self.current, require_human_reviews=False))

    def test_denial_unknown_criterion_wrong_actor_or_missing_event_cannot_accept_human_review(self):
        self.build(["C2"])
        token = human_review.review_token(self.state)
        valid = {"kind": "human_review", "actor": "user_cli", "criterion": "C2", "token": token}
        for changes in ({"kind": "permission_answer", "text": "Reject C2."}, {"criterion": "unknown"},
                        {"actor": "runner"}, {"token": self.token}, {"token": token + "-stale"}):
            with self.subTest(changes=changes):
                event = {**valid, **changes}
                self.state["human_reviews"] = {"C2": event}
                self.state["user_events"] = [self.state["goal_contract"]["approval_event"], event]
                self.assertFalse(reader.ready(self.state, self.current))
                self.assertTrue(reader.ready(self.state, self.current, False))
        self.state["human_reviews"] = {"C2": valid}
        self.state["user_events"] = [self.state["goal_contract"]["approval_event"]]
        self.assertFalse(reader.ready(self.state, self.current))

    def test_current_artifact_change_invalidates_existing_human_token_without_goal_rewrite(self):
        self.build(["C2"])
        self.approve("C2")
        accepted = deepcopy(self.state["human_reviews"])
        self.refresh(humans=["C2"])
        self.assertEqual(accepted, self.state["human_reviews"])
        self.assertTrue(reader.ready(self.state, self.current, False))
        self.assertFalse(reader.ready(self.state, self.current))
        self.approve("C2")
        self.assertTrue(reader.ready(self.state, self.current))
        self.assertEqual(self.token, contracts.token(self.state["goal_contract"]))

    def test_pending_payload_and_outstanding_must_name_only_exact_declared_human_gaps(self):
        self.build(["C2"])
        original = self.proof_payload()
        for pending in ([], ["C1"], ["unknown"], ["C2", "C2"], ["C1", "C2"], None, True):
            with self.subTest(pending=pending):
                payload = {**deepcopy(original), "pending_human_criteria": pending}
                self.publish(payload, formal=True)
                self.assertFalse(reader.ready(self.state, self.current, False))
        self.publish(original, formal=True)
        for outstanding in (["C1"], ["unknown"], ["C2", "C2"], None, True):
            self.record["outstanding_criteria"] = outstanding
            self.assertFalse(reader.ready(self.state, self.current, False))
        self.record["outstanding_criteria"] = ["C2"]
        for claimed in (["C1", "C2"], [], ["C1", "unknown"], ["C1", "C1"]):
            self.publish({**deepcopy(original), "criterion_ids": claimed}, formal=True)
            self.assertFalse(reader.ready(self.state, self.current, False))

    def test_human_only_normalized_validation_matrix_refuses_technical_failure_or_unknown_gap(self):
        self.build(["C2"])
        original = deepcopy(self.state["validation"])
        variants = [{"verdict": "PASS"}, {"verdict": "FAIL"}, {"unverified_criteria": ["C1", "C2"]},
                    {"unverified_criteria": ["unknown"]}, {"findings": [{"blocking": True}]},
                    {"end_to_end_result": {"status": "NOT_VERIFIED"}}, {"criterion_results": []}]
        for cid, status in (("C1", "FAIL"), ("C1", "NOT_VERIFIED"), ("C2", "PASS")):
            rows = deepcopy(original["criterion_results"])
            next(row for row in rows if row["id"] == cid)["status"] = status
            variants.append({"criterion_results": rows})
        for changes in variants:
            with self.subTest(changes=changes):
                self.state["validation"] = {**deepcopy(original), **changes}
                self.assertFalse(reader.ready(self.state, self.current, False))

    def test_pending_human_review_cannot_bypass_stale_validation_or_latest_replay(self):
        self.build(["C2"])
        original = deepcopy(self.state["validation"])
        for field, value in (("source_revision", "stale"), ("contract_hash", "stale"), ("contract_revision", 2),
                             ("task_id", "different-task"), ("criteria_revision", 2), ("evidence_hashes", {})):
            self.state["validation"] = {**deepcopy(original), field: value}
            self.assertFalse(reader.ready(self.state, self.current, False))
        self.state["validation"] = deepcopy(original)
        self.state["validation"]["check_replay"]["checks"].pop(0)
        self.assertFalse(reader.ready(self.state, self.current, False))
        self.state["validation"] = original
        path = Path(next(iter(original["evidence_hashes"])))
        path.write_text("Changed replay output\n", encoding="ascii")
        self.assertFalse(reader.ready(self.state, self.current, False))

    def test_refresh_accepts_new_source_and_retains_formal_history_read_only(self):
        history = deepcopy(self.record["history"])
        previous = self.refresh()
        before = deepcopy(self.state)
        self.assertNotEqual(previous["source_revision"], self.current["revision"])
        self.assertNotEqual(history[-1]["artifact"], self.record["completion_proof"]["artifact"])
        self.assertTrue(reader.ready(self.state, self.current))
        self.assertTrue(completion.completion_ready(self.state, self.decision(), self.current))
        self.assertEqual(before, self.state)
        self.assertEqual(history, self.record["history"])
        self.assertEqual([previous], self.record["completion_proof_history"])

    def test_multiple_refreshes_are_bounded_by_saved_exact_artifact_chain(self):
        history = deepcopy(self.record["history"])
        first = self.refresh(source="VALUE = 7\n# Revision one\n")
        second = self.refresh(source="VALUE = 7\n# Revision two\n")
        self.assertTrue(reader.ready(self.state, self.current))
        self.assertEqual([first, second], self.record["completion_proof_history"])
        self.assertEqual(history, self.record["history"])
        for saved in self.record["completion_proof_history"]:
            self.assertEqual({key: value for key, value in saved.items() if key != "artifact"},
                             artifacts.verify(self.run_dir, saved["artifact"])["report"])

    def test_same_source_refresh_requires_new_current_replay_not_previous_pass(self):
        previous = deepcopy(self.record["completion_proof"])
        old_evidence = Path(next(iter(previous["results"]["A"]["evidence_hashes"])))
        old_evidence.write_text("Old evidence changed\n", encoding="ascii")
        self.refresh(source=self.source.read_text(encoding="ascii"))
        self.assertEqual(previous["source_revision"], self.current["revision"])
        self.assertTrue(reader.ready(self.state, self.current))
        payload = self.proof_payload()
        payload["results"]["A"] = previous["results"]["A"]
        self.publish(payload)
        self.assertFalse(reader.ready(self.state, self.current))

    def test_historical_pins_are_historical_but_latest_retained_checks_all_need_fresh_pass(self):
        previous = self.refresh()
        for receipt in previous["results"].values():
            for path in receipt["evidence_hashes"]:
                Path(path).unlink(missing_ok=True)
        self.assertTrue(reader.ready(self.state, self.current))
        original = self.proof_payload()
        for changes in ({"status": "FAIL"}, {"status": "SKIPPED"}, {"exit_code": False}, {"exit_code": 1},
                        {"replayed": False}, {"source_revision": previous["source_revision"]},
                        {"contract_token": "old-token"}, {"check_hash": "0" * 64}, {"executions": []},
                        {"evidence_hashes": {}}):
            with self.subTest(changes=changes):
                payload = deepcopy(original)
                payload["results"]["A"].update(changes)
                self.publish(payload)
                self.assertFalse(reader.ready(self.state, self.current))
        payload = deepcopy(original)
        payload["results"].pop("A")
        self.publish(payload)
        self.assertFalse(reader.ready(self.state, self.current))

    def test_changed_source_or_evidence_without_fresh_refresh_is_stale(self):
        self.refresh()
        self.assertFalse(reader.ready(self.state, {"revision": "unvalidated-source"}))
        self.state["validation"]["check_replay"]["source_revision"] = "old-replay"
        self.assertFalse(reader.ready(self.state, self.current))
        self.state["validation"]["check_replay"]["source_revision"] = self.current["revision"]
        path = Path(next(iter(self.record["completion_proof"]["results"]["A"]["evidence_hashes"])))
        path.write_text("Changed current evidence\n", encoding="ascii")
        self.assertFalse(reader.ready(self.state, self.current))

    def test_refresh_payload_cannot_change_plan_slice_check_definitions_or_verified_history(self):
        self.refresh()
        original = self.proof_payload()
        for changes in ({"plan_hash": "different-plan"}, {"slice_id": "different-slice"},
                        {"verified_slices": ["S2"]}, {"contract_token": "old-token"},
                        {"required_checks": [deepcopy(self.check_b)]}):
            self.publish({**deepcopy(original), **changes})
            self.assertFalse(reader.ready(self.state, self.current))
        previous = self.record["completion_proof_history"][0]
        changed = deepcopy(previous)
        changed["required_checks"][0]["method"] = self.check_b["method"]
        self.record["completion_proof_history"] = [changed]
        self.publish(original)
        self.assertFalse(reader.ready(self.state, self.current))

    def test_refresh_owner_requires_current_record_independence_output_pin_and_immutable_binding(self):
        self.refresh()
        original = self.proof_payload()
        for changes in ({"stage": "sol"}, {"role": "sol"}, {"output_sha256": "0" * 64},
                        {"binding": {}}, {"binding": {**original["reviewer_receipt"]["binding"], "validated_source": "old"}},
                        {"authenticated": True}):
            with self.subTest(changes=changes):
                payload = deepcopy(original)
                payload["reviewer_receipt"].update(changes)
                self.publish(payload)
                self.assertFalse(reader.ready(self.state, self.current))
        self.publish(original)
        saved = deepcopy(self.owner)
        for changes in ({"exit_code": 1}, {"exit_code": False}, {"changed_files": ["product.py"]},
                        {"rejected": True}, {"source_revision": "old"}):
            self.owner.update(changes)
            self.assertFalse(reader.ready(self.state, self.current))
            self.owner.clear()
            self.owner.update(saved)
        self.state["stages"].remove(self.owner)
        self.assertFalse(reader.ready(self.state, self.current))
        self.state["active_stage"] = saved
        self.assertTrue(reader.ready(self.state, self.current))

    def test_refresh_owner_report_repair_normalizes_original_stage_without_rebinding(self):
        self.refresh()
        self.owner.update(stage="astra_review_report_repair", original_stage="astra_review")
        self.assertTrue(reader.ready(self.state, self.current))
        self.owner["original_stage"] = "astra_checkpoint"
        self.assertFalse(reader.ready(self.state, self.current))

    def test_refresh_refuses_empty_malformed_duplicate_or_self_referential_history(self):
        previous = self.refresh()
        current = deepcopy(self.record["completion_proof"])
        for saved in ([], None, True, [True], [{}], [previous, previous], [previous, current]):
            with self.subTest(saved=saved):
                self.record["completion_proof_history"] = saved
                self.assertFalse(reader.ready(self.state, self.current))
        self.record["completion_proof_history"] = [previous]
        self.record["completion_proof"]["refresh_of"] = deepcopy(current["artifact"])
        self.assertFalse(reader.ready(self.state, self.current))

    def test_refresh_cannot_link_to_a_nonancestor_or_skip_latest_saved_predecessor(self):
        first = self.refresh(source="VALUE = 7\n# One\n")
        self.refresh(source="VALUE = 7\n# Two\n")
        original = self.proof_payload()
        for identity in (self.record["history"][0]["artifact"], first["artifact"], self.record["active"]["artifact"]):
            self.publish({**deepcopy(original), "refresh_of": deepcopy(identity)})
            self.assertFalse(reader.ready(self.state, self.current))
        self.publish(original, predecessor=self.record["active"]["artifact"]["sha256"])
        self.assertFalse(reader.ready(self.state, self.current))

    def test_refresh_chain_requires_full_saved_payload_equality_and_untampered_artifacts(self):
        self.refresh()
        previous = self.record["completion_proof_history"][0]
        previous["criterion_ids"] = []
        self.assertFalse(reader.ready(self.state, self.current))
        previous["criterion_ids"] = ["C1", "C2"]
        path = self.run_dir / previous["artifact"]["path"]
        contents = path.read_bytes()
        path.write_bytes(contents + b" ")
        self.assertFalse(reader.ready(self.state, self.current))
        path.unlink()
        self.assertFalse(reader.ready(self.state, self.current))

    def test_final_formal_checkpoint_must_bind_active_artifact_even_under_valid_refresh(self):
        payload = self.proof_payload()
        self.publish(payload, formal=True, predecessor="unrelated-reviewed-plan")
        self.refresh()
        self.assertFalse(reader.ready(self.state, self.current))

    def test_current_refresh_must_use_latest_normalized_validation_not_same_source_older_pass(self):
        self.refresh()
        self.assertTrue(reader.ready(self.state, self.current))
        self.new_validation()
        self.assertFalse(reader.ready(self.state, self.current))
        self.refresh(source=self.source.read_text(encoding="ascii"))
        self.assertTrue(reader.ready(self.state, self.current))

    def test_empty_retained_checks_or_forged_retirement_never_prove_technical_product(self):
        self.refresh()
        original = self.proof_payload()
        for changes in ({"required_checks": [], "results": {}}, {"criterion_ids": []}):
            self.publish({**deepcopy(original), **changes})
            self.assertFalse(reader.ready(self.state, self.current))
        self.record["retirements"] = [{"kind": "product_change", "check_id": "A", "authenticated": True}]
        self.publish(original)
        self.assertFalse(reader.ready(self.state, self.current))

    def test_human_pending_cannot_promote_contribution_only_technical_product_proof(self):
        self.check_b["relation"] = "contributes_to"
        self.initial["slices"][1]["checks"][0]["relation"] = "contributes_to"
        self.proposal["slices"][0]["checks"][0]["relation"] = "contributes_to"
        self.build(["C2"])
        self.assertTrue(all(row["status"] == "PASS" for row in self.record["completion_proof"]["results"].values()))
        self.assertFalse(reader.ready(self.state, self.current, False))


if __name__ == "__main__":
    unittest.main()
