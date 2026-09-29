"""Tests described in plain English, and the runner's link from each one to the test that proves it.

Two places write them. A bug fix's Investigator writes ``test_cases`` (id,
given, when, then) in its diagnosis (autocode_bug_job). A small feature's
Planner writes acceptance criteria as concrete examples and marks the ones a
test proves with a verification_method of ``test: test_<id>_...``
(``contract_cases``). Either way a person approves English, the Builder writes
one test per case named after its id, and the runner's proof
(autocode_regression) checks by name, with no model, that each case has a
test that passes with the change and did not before (``match_cases``). A bug's Investigator may mark a case ``guard``: a behavior that
already works on the unfixed code and must keep working (an edge that never broke). Its test
must pass with the change but cannot fail before it, so only the other cases need to.

In a plan with several milestones, a case is due once its milestone is: the
proof at a milestone checkpoint covers the current milestone (every member of a
parallel batch) and the milestones already accepted, never a later one
(``in_scope``). Criteria that belong to no milestone are due once every
milestone is, that is at final completion.

Pure functions over saved state. Imports nothing from the runner; milestone
progress is read from ``milestone_progress`` as autocode_milestones saves it.
"""
from __future__ import annotations

import re

MARK = "test:"


def contract_cases(state: dict) -> list[dict]:
    """The approved plan's criteria marked ``test:`` that are due now, as cases (id, text)."""
    body = (state.get("goal_contract") or {}).get("body") or {}
    due = in_scope(state)
    return [{"id": row["id"], "text": row.get("criterion", "")} for row in body.get("acceptance_criteria") or []
            if isinstance(row, dict) and row.get("id") and (due is None or row["id"] in due)
            and str(row.get("verification_method", "")).strip().lower().startswith(MARK)]


def in_scope(state: dict) -> set[str] | None:
    """Criterion ids due at this point of a multi-milestone plan, or None when all are due.

    Due: the criteria of the current task's milestone (or batch members) and of milestones
    already accepted under this contract. All are due in a one-milestone plan, when the
    current task names no milestone, and once every milestone is current or accepted.
    """
    contract = state.get("goal_contract") or {}
    milestones = (contract.get("body") or {}).get("milestones") or []
    task = state.get("current_task") or {}
    current = set(task.get("milestone_ids") or []) or ({task["milestone_id"]} if task.get("milestone_id") else set())
    if len(milestones) <= 1 or not current:
        return None
    accepted = {mid for row in (state.get("milestone_progress") or {}).values()
                if row.get("accepted") and row.get("contract_hash") == contract.get("hash")
                for mid in row.get("milestone_ids") or [row.get("id")]}
    reached = current | accepted
    if {milestone.get("id") for milestone in milestones} <= reached:
        return None
    return {criterion for milestone in milestones if milestone.get("id") in reached
            for criterion in milestone.get("acceptance_criteria") or []}


GUARD = "guard"


def is_guard(case: dict) -> bool:
    """A guard case is a behavior that already holds on the unfixed code and must keep holding. Every
    other case (no ``kind``, or ``regression``) must fail on the unfixed code and pass with the fix."""
    return case.get("kind") == GUARD


def case_text(case: dict) -> str:
    if "text" in case:
        return f"{case['id']}: {case['text']}"
    label = " (guard: already holds before the fix, must keep holding)" if is_guard(case) else ""
    return f"{case['id']}{label}: Given {case['given']}; when {case['when']}; then {case['then']}"


def case_test_name(case_id: str) -> str:
    return f"test_{case_id.lower()}_<what it checks>"


def _words(name: str) -> list[str]:
    """Lowercase words of an identifier: test_t1_x, TestT1X and test-t1-x all give test, t1, x."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [word for word in re.split(r"[^a-z0-9]+", name.lower()) if word]


def _test_function(test_id: str) -> str:
    """The test's own name inside a runner's id (module.Class.test_x, path::Class::test_x[param], ...)."""
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r"\[.*\]$", "", test_id))
    tests = [name for name in names if name.lower().startswith("test")]
    return tests[-1] if tests else (names[-1] if names else "")


def match_cases(cases: list[dict], test_ids: list[str]) -> dict[str, list[str]]:
    """For each case, the tests whose name carries its id as whole words (T1 -> test_t1_...)."""
    matched = {}
    for case in cases:
        want = _words(case["id"])
        matched[case["id"]] = [test for test in test_ids
                               if any(_words(_test_function(test))[i:i + len(want)] == want
                                      for i in range(len(_words(_test_function(test)))))]
    return matched


def run_probes(rows: list[dict], run_probe, *, what: str = "claim", key: str = "claim") -> list[dict]:
    """Run every row's ``probe`` (``run_probe(command)``, a scratch run); each must exit 0.

    A probed row needs its ``example`` in plain English. Returns the receipts of the rows shown;
    raises ValueError naming the rows whose probe did not exit 0. ``what`` names the rows in
    messages (claim, concern, conflict) and ``key`` is the field that identifies a row.
    """
    shown, failed = [], []
    for row in rows:
        probe = str(row.get("probe") or "").strip()
        if not probe:
            continue
        if not str(row.get("example") or "").strip():
            raise ValueError(f"A probed {what} needs its example in plain English: {row.get(key)!r}")
        run = run_probe(probe)
        receipt = {key: row.get(key), "probe": probe, "exit_code": run.get("exit_code"),
                   "tail": (run.get("tail") or run.get("error") or "")[-600:]}
        (shown if run.get("exit_code") == 0 and not run.get("error") else failed).append(receipt)
    if failed:
        raise ValueError(f"These {what}s' probes did not exit 0 on the code as it is, so they are not shown: "
                         + "; ".join(f"{row[key]!r} ({row['probe']}: exit {row['exit_code']}) {row['tail'][-200:]}"
                                     for row in failed))
    return shown


BUILDER_NOTE = """
TESTS NAMED IN THE PLAN: every acceptance criterion of your milestone whose verification_method starts with
"test:" is a concrete example you must write as its own test, named with that criterion's id (C2 ->
test_c2_<what it checks>) and asserting exactly the criterion's example. Before the Validator runs, the runner
runs these tests itself, with those of milestones already accepted: each must pass with the change and must
not have passed before the run began. Criteria without "test:" are checked by the Validator as usual.
"""


def builder_note(state: dict) -> str:
    return BUILDER_NOTE if contract_cases(state) else ""
