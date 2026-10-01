"""The real CLI's reserved-detail allowance recovery."""
from copy import deepcopy
import dataclasses
import json
from pathlib import Path
import tempfile
import unittest

import autocode_progressive_state as progressive
import autocode_resolver_human as human
from units import autoplanner as planning
from scenarios.harness import catalog
from scenarios.harness.driver import Driver, default_autocode, fake_setup
from scenarios.harness.project import materialize


class PublicLimitRecoveryTests(unittest.TestCase):
    def driver(self, directory):
        scenario = dataclasses.replace(catalog.load("progressive-learning-journey"),
                                       fake_fault="progressive_split_retry")
        root = Path(directory)
        project = materialize(scenario.seed, root / "project")
        flags, env = fake_setup(scenario, root, scenario.reference)
        return scenario, Driver(project, root, flags, env, autocode=default_autocode(),
                                max_steps=30, timeout_seconds=180)

    def exhausted(self, scenario, driver):
        driver.call("start", task=scenario.brief)
        driver.run_dir = next((driver.project / ".autocode" / "runs").iterdir())
        self.assertEqual(driver.view()["needs"]["kind"], "approve_plan")
        driver.serve(driver.view()["needs"])
        driver.call("resume")
        view = driver.view()
        self.assertEqual(view["status"], "PAUSED_PLANNING_BUDGET", view)
        self.assertEqual(view["next_stage"], "glm_revise")
        usage = view["progressive"]["allowance_usage"]
        self.assertEqual(usage["defaults"], {"reviews": 2, "local_seconds": 5400, "run_seconds": 43200})
        self.assertEqual(len(usage["pools"]), 2)
        self.assertTrue(all(pool["reviews_used"] == pool["review_limit"] == 2 for pool in usage["pools"].values()))
        self.assertEqual([row["slice_id"] for row in view["progressive"]["demonstrated_slices"]], ["S1", "S2a"])
        return view

    def test_cli_detail_pause_accepts_actual_operator_ceiling_without_reset_and_restarts(self):
        with tempfile.TemporaryDirectory(prefix="progressive-detail-limit-") as directory:
            scenario, driver = self.driver(directory)
            before = self.exhausted(scenario, driver)
            old = before["progressive"]["allowance_usage"]
            for limit in (4, 5, 4):
                driver.call("explicit-reserved-review-limit", "--planning-review-call-limit", str(limit), action=True)
                status = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
                self.assertEqual(status["settings"]["planning_review_call_limit"], limit)
            authorized = driver.view()
            usage = authorized["progressive"]["allowance_usage"]
            selected_pool = next(key for key, row in usage["pools"].items() if row["review_limit"] == 4)
            self.assertEqual(usage["pools"].keys(), old["pools"].keys())
            self.assertEqual(usage["run_seconds"], old["run_seconds"])
            self.assertEqual(usage["attempts"], old["attempts"])
            self.assertEqual(usage["time_receipts"], old["time_receipts"])
            for key in old["pools"]:
                for spent in ("reviews_used", "seconds_used"):
                    self.assertEqual(usage["pools"][key][spent], old["pools"][key][spent])
            self.assertEqual(authorized["progressive"]["demonstrated_slices"], before["progressive"]["demonstrated_slices"])
            self.assertFalse(authorized["done"])
            driver.call("repeat-same-review-action", "--planning-review-call-limit", "4", action=True)
            self.assertEqual(driver.view()["progressive"]["allowance_usage"], usage)
            restarted = Driver(driver.project, driver.root, driver.flags, driver.env,
                               autocode=default_autocode(), max_steps=30, timeout_seconds=180)
            restarted.run_dir = driver.run_dir
            self.assertEqual(restarted.view()["progressive"]["allowance_usage"], usage)
            restarted.call("resume-authorized-detail", "--resume-paused")
            final = restarted.view()
            current = final["progressive"]["allowance_usage"]
            self.assertTrue(final["done"], final)
            self.assertEqual(current["pools"].keys(), old["pools"].keys())
            self.assertEqual(current["pools"][selected_pool]["reviews_used"], 4)
            for identity, attempt in old["attempts"].items():
                self.assertEqual(current["attempts"][identity], attempt)
            self.assertGreaterEqual(current["run_seconds"], usage["run_seconds"])

    def test_detail_limit_rejects_unreconciled_or_unauthenticated_reservations(self):
        with tempfile.TemporaryDirectory(prefix="progressive-reservation-negative-") as directory:
            scenario, driver = self.driver(directory)
            self.exhausted(scenario, driver)
            # Read the genuine CLI checkpoint only as a policy input. Tampered
            # copies never replace the saved run or supply execution authority.
            state = driver.state()
            self.assertIsNotNone(progressive.authorized_detail_reservation(state))
            for flow, stage in (("v1", "glm_revise"), ("v2", "plan_revise")):
                candidate = deepcopy(state)
                candidate["settings"]["planning_flow"] = flow
                candidate["next_stage"] = stage
                original = deepcopy(candidate)
                reservation = progressive.authorized_detail_reservation(candidate)
                self.assertIsNotNone(reservation)
                planning.set_review_call_limit(candidate, 4)
                pool = reservation["pool_id"]
                self.assertEqual(candidate["progressive"]["budget"]["pools"][pool]["reviews_used"], 2)
                self.assertEqual(candidate["planning"]["astra_calls"], original["planning"]["astra_calls"])
                self.assertEqual(candidate["progressive"]["delegation"], original["progressive"]["delegation"])
                self.assertEqual(candidate["progressive"]["budget"]["allocations"], original["progressive"]["budget"]["allocations"])
                self.assertEqual(candidate["settings"]["planning_review_call_limit"], 4)
                self.assertEqual(candidate["user_events"][-1]["stage"], stage)
            mutations = {
                "active worker": lambda row: row.update(active_stage={"pid": 123}),
                "uncertain result": lambda row: row.update(uncertain_artifacts="unreconciled"),
                "report repair": lambda row: row.update(pending_report_repair={"attempts": 1}),
                "pending question": lambda row: row.update(pending_questions=[{"id": "Q1"}]),
                "unissued Resolver proposal": lambda row: row.update({human.PRIVATE: {"scope": "goal_change"}}),
                "unauthenticated Resolver publication": lambda row: row.update({human.PUBLIC: {"scope": "operational_exhaustion"}}),
                "unapproved contract": lambda row: row["goal_contract"].update(approval_status="draft"),
                "stale sealed body": lambda row: row["goal_contract"]["body"].update(intended_outcome="Different product"),
                "stale plan": lambda row: row["progressive"]["plan"].update(plan_hash="stale"),
                "mismatched plan content": lambda row: row["progressive"]["plan"]["proposal"]["slices"][0].update(intended_result="Stale plan content"),
                "stale transition source": lambda row: row["progressive"]["transition"]["source"].update(revision="stale"),
                "unknown pool": lambda row: row["progressive"]["pending_allowance"].update(pool_id="NEW-pool"),
                "renamed work": lambda row: row["progressive"]["pending_allowance"].update(work_id="NEW-work"),
                "stale reserved slice": lambda row: row["progressive"]["pending_allowance"].update(slice_id="Stale-slice"),
                "review, not detail": lambda row: row["progressive"]["transition"].update(phase="review"),
                "protected candidate scope": lambda row: row["progressive"]["transition"].update(scope_issue="This candidate exceeds approved permissions"),
                "unreviewed candidate": lambda row: row["progressive"]["transition"].update(candidate=row["progressive"]["active"]["artifact"]),
                "candidate-only scope": lambda row: row["progressive"].pop("delegation"),
                "ordinary root mode": lambda row: row["settings"].update(joint_planning=False),
                "wrong planner mode": lambda row: row.update(next_stage="plan_revise"),
                "pending intervention": lambda row: row.update(intervention_ack_pending=True),
                "abandoned, not budget pause": lambda row: row.update(status="PAUSED_STAGE_ABANDONED"),
            }
            for label, mutate in mutations.items():
                for limit in (4, 0):
                    candidate = deepcopy(state)
                    mutate(candidate)
                    original = deepcopy(candidate)
                    with self.subTest(label=label, limit=limit):
                        with self.assertRaises(ValueError):
                            planning.set_review_call_limit(candidate, limit)
                        self.assertEqual(candidate, original)
            source = driver.project / "lessons.py"
            source.write_text(source.read_text() + "\n# Changed after the saved detail boundary.\n")
            original = deepcopy(state)
            with self.assertRaises(ValueError):
                planning.set_review_call_limit(state, 4)
            self.assertEqual(state, original)

    def test_cli_time_ceilings_return_to_lower_value_after_restart(self):
        with tempfile.TemporaryDirectory(prefix="progressive-time-limit-") as directory:
            scenario, driver = self.driver(directory)
            before = self.exhausted(scenario, driver)
            old = before["progressive"]["allowance_usage"]
            run_dir = driver.run_dir
            # Keep the exhausted review bound explicitly pinned. A time-only
            # invocation must save its ceiling even though it cannot launch.
            driver.call("pin-exhausted-review-ceiling", "--planning-review-call-limit", "2", action=True)
            driver.call("time-ceiling-without-provider-launch", "--max-seconds", "50000")
            selected = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            self.assertEqual(selected["view"]["status"], "PAUSED_PLANNING_BUDGET")
            self.assertEqual(selected["settings"]["limits"]["max_seconds"], 50000)
            self.assertEqual(selected["view"]["progressive"]["allowance_usage"]["run_limit"], 50000)
            self.assertEqual(selected["view"]["progressive"]["allowance_usage"]["attempts"], old["attempts"])
            for flag, key, values in (("--max-milestone-seconds", "seconds_limit", (6000, 7000, 6000)),
                                      ("--max-seconds", "run_limit", (50000, 60000, 50000))):
                for limit in values:
                    driver.call("explicit-time-ceiling", flag, str(limit), "--show-goal", action=True)
                    status = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
                    usage = status["view"]["progressive"]["allowance_usage"]
                    if key == "run_limit":
                        self.assertEqual(usage[key], limit)
                        self.assertEqual(status["settings"]["limits"]["max_seconds"], limit)
                    else:
                        self.assertEqual(sorted(row[key] for row in usage["pools"].values()), [5400, limit])
                        self.assertEqual(status["settings"]["milestone_checkpoints"]["max_seconds"], limit)
                    self.assertEqual(usage["attempts"], old["attempts"])
                    self.assertEqual(usage["time_receipts"], old["time_receipts"])
                    self.assertEqual(usage["run_seconds"], old["run_seconds"])
                    driver = Driver(driver.project, driver.root, driver.flags, driver.env,
                                    autocode=default_autocode(), max_steps=30, timeout_seconds=180)
                    driver.run_dir = run_dir
                    self.assertEqual(driver.view()["progressive"]["allowance_usage"], usage)


if __name__ == "__main__":
    unittest.main()
