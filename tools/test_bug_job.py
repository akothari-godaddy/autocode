"""The bug-fix workflow's Investigator: diagnose before fixing, and end the run when
the report does not reproduce."""
import json
import tempfile
import unittest
from pathlib import Path

from . import autocode_bug_job as bug_job
from . import autocode_jobs as jobs
from . import autocode_run_view as run_view
from . import autocode_workflows as workflows
from . import autopilot
from .units import autoresolver


def state_for(workspace="/nowhere", task="Occasionally we renew the same domain twice after a timeout. Fix it."):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "workflow": {"kind": "bugfix", "then": "requirements_gather"},
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p"},
                "astra": {"model": "a", "engine": "codex"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def diagnosis(outcome="reproduced", **overrides):
    value = {"outcome": outcome, "note_path": "docs/bugs/duplicate-renew.json",
             "observed": "Renewed twice after a timeout", "reproduction": "fail_next('timeout-after'); renew() -> 2 mutations",
             "root_cause": "retries after an uncertain timeout with a fresh cl_trid",
             "affected_paths": ["epp/client.py"], "invariant": "one logical renew, at most one mutation",
             "conclusion": "Reconcile before resending.", "fix_size": "small",
             "fix_plan": ["keep one cl_trid", "poll before resending"], "questions": [], "tests_run": ["python3 -m unittest"]}
    if outcome == "not_reproduced":
        value.update(root_cause="", affected_paths=[], invariant="", fix_size="none", fix_plan=[],
                     note_path="docs/bugs/none-cells.json", conclusion="export() already writes None as empty.",
                     questions=["Which version is the reporter running?"])
    value.update(overrides)
    return value


class RoutingTests(unittest.TestCase):
    def test_a_recognized_bug_goes_to_the_investigator_and_remembers_the_build_entry(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "bugfix", "reason": "", "signals": []}, {})
        self.assertEqual(bug_job.STAGE, state["next_stage"])
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        self.assertEqual("autoresolver", autopilot.unit_for(bug_job.STAGE))
        self.assertIn(bug_job.STAGE, jobs.STAGES)


class PrepareTests(unittest.TestCase):
    def test_investigator_gets_its_own_route_and_a_scratch_copy_but_no_plan(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            request = autoresolver.prepare(state, bug_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "investigator", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual(state["settings"]["roles"]["astra"], state["settings"]["roles"]["investigator"])
        self.assertEqual(bug_job.SCHEMA, request.schema)
        self.assertIn("renew the same domain twice", request.prompt)
        self.assertEqual("INVESTIGATING", state["phase"])


class ApplyTests(unittest.TestCase):
    def apply(self, value, changed=()):
        workspace = tempfile.mkdtemp()
        state = state_for(workspace)
        bug_job.apply(state, value, {"changed_files": list(changed), "output": "/run/investigate_bug-01.json"}, workspace)
        return state, Path(workspace)

    def test_a_reproduced_bug_writes_the_diagnosis_and_hands_over_to_the_build_pipeline(self):
        state, workspace = self.apply(diagnosis())
        note = json.loads((workspace / "docs/bugs/duplicate-renew.json").read_text())
        self.assertEqual((True, []), (note["reproduced"], note["changed"]))
        for field in ("observed", "reproduction", "root_cause", "affected_paths", "invariant"):
            self.assertTrue(note[field], field)
        self.assertEqual(("RUNNING", "requirements_gather"), (state["status"], state["next_stage"]))
        self.assertIsNone(jobs.ended_in(state))

    def test_a_report_that_does_not_reproduce_ends_the_run_with_questions(self):
        state, workspace = self.apply(diagnosis("not_reproduced"))
        note = json.loads((workspace / "docs/bugs/none-cells.json").read_text())
        self.assertEqual((False, []), (note["reproduced"], note["changed"]))
        self.assertEqual(["Which version is the reporter running?"], note["questions"])
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertEqual("bugfix", view["workflow"])
        self.assertIs(bug_job, jobs.ended_in(state))
        rendered = jobs.render(state, lambda _: "build completion")
        self.assertIn("NOT REPRODUCED", rendered)
        self.assertIn("Question for the reporter: Which version", rendered)

    def test_an_investigation_that_changed_the_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not change the repository.*epp/client.py"):
            self.apply(diagnosis(), changed=["epp/client.py", "docs/bugs/scratch.json"])

    def test_the_note_must_live_under_docs_bugs(self):
        for path in ("notes/x.json", "docs/bugs/x.md", "docs/bugs/../../etc.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "note_path"):
                self.apply(diagnosis(note_path=path))

    def test_a_reproduced_bug_needs_a_cause_paths_and_an_invariant(self):
        with self.assertRaisesRegex(ValueError, "root cause"):
            self.apply(diagnosis(invariant=""))
        with self.assertRaisesRegex(ValueError, "root cause"):
            self.apply(diagnosis(fix_size="none"))

    def test_a_report_that_did_not_reproduce_may_not_propose_a_fix(self):
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            self.apply(diagnosis("not_reproduced", fix_plan=["strip the text None defensively"]))

    def test_an_investigation_must_say_what_it_tried(self):
        with self.assertRaisesRegex(ValueError, "what it ran"):
            self.apply(diagnosis(reproduction="  "))


if __name__ == "__main__":
    unittest.main()
