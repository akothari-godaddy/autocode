"""The bug-fix workflow's first stage: investigate a reported misbehavior before anyone changes code.

A run recognized as ``bugfix`` (autocode_workflows) starts with ``STAGE``: the
Investigator reproduces the report in a scratch copy of its own, finds the root
cause, and returns a diagnosis. The runner then:

- rejects the report if the stage changed anything in the workspace outside
  ``docs/bugs/`` (investigating is read-only; the before/after snapshot is the evidence),
- writes the diagnosis note (``note_path``, under ``docs/bugs/``) from the
  validated report, before any fix exists,
- ends the run when the report did not reproduce (the note says so and lists
  what the reporter must supply), or hands a reproduced bug to the build
  pipeline, which starts at the stage saved when recognition began.

Pure module: prompt, schema, transition, rendering. Imports nothing from the
runner. State key written: ``investigation`` (the report, its note path and output).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

try:
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_workflows as workflows

STAGE = workflows.INVESTIGATE_STAGE
NOTES_PREFIX = "docs/bugs/"
OUTCOMES = ("reproduced", "not_reproduced")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["outcome", "note_path", "observed", "reproduction", "root_cause", "affected_paths",
                 "test_paths", "invariant", "conclusion", "fix_size", "fix_plan", "questions", "tests_run"],
    "properties": {
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "note_path": TEXT,
        "observed": TEXT,
        "reproduction": TEXT,
        "root_cause": TEXT,
        "affected_paths": TEXTS,
        "test_paths": TEXTS,
        "invariant": TEXT,
        "conclusion": TEXT,
        "fix_size": {"type": "string", "enum": ["small", "large", "none"]},
        "fix_plan": TEXTS,
        "questions": TEXTS,
        "tests_run": TEXTS,
    },
}

PROMPT = """You are the Investigator: an engineer handed a bug report. Before anyone changes code, you find out
what is actually happening. You do not fix anything and you do not edit the repository.

What to do:
1. Restate what the reporter observed (observed).
2. Try to reproduce it. Make your own scratch copy OUTSIDE the workspace (for example under a temporary
   directory) and run the code there: the command or scenario from the report, the existing tests, a small
   script or test of your own. Never write into the workspace itself; the runner compares it before and
   after and rejects an investigation that changed anything outside docs/bugs/.
   Record exactly what you ran and what happened (reproduction, tests_run).
3. If it reproduces (outcome reproduced): find the ROOT cause, not the place the symptom shows up.
   - root_cause: why it happens, in terms of the code's logic.
   - affected_paths: the source files that must change (not tests).
   - test_paths: where the regression test belongs: an existing test file, or the test directory.
   - invariant: the rule a correct fix must uphold (for example "one logical renew produces at most one
     mutation"), stated so a test can check it.
   - fix_size: small when the cause is obvious and the fix is one bounded change in one or two files;
     large otherwise. A small fix goes straight to a Builder and an independent Validator without a
     planning round, so say large whenever the fix needs design choices or touches several modules.
   - fix_plan: the steps of the fix, and the regression test that fails before it and passes after it.
4. If it does NOT reproduce (outcome not_reproduced): say so plainly. Do not invent a cause and do not
   propose a "defensive" change to code that works. reproduction says what you tried; conclusion says
   what the code actually does and why the report may differ (old version, different input, upstream data);
   questions lists what you need from the reporter. fix_size is none; fix_plan, affected_paths and
   test_paths are empty.
5. conclusion: two or three sentences a person can act on.
6. note_path: where the runner saves your diagnosis. Use the path the request names if it names one under
   docs/bugs/, otherwise docs/bugs/<short-kebab-name>.json.

Return JSON only, matching the schema the runner gives you. The runner writes the note; you do not.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "execution_engine": engine, "notes_directory": NOTES_PREFIX,
            "workspace_inventory": inventory or {},
            # Present because every provider reads them; nothing is planned yet.
            "goal_contract": None, "current_task": None, "saved_answers": {}}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    """Reject an investigation that wrote into the repository or does not say what it found."""
    stray = sorted(path for path in (changed_files or []) if not str(path).startswith(NOTES_PREFIX))
    if stray:
        raise ValueError("An investigation must not change the repository; this attempt changed: " + ", ".join(stray))
    note = value["note_path"]
    if not note.startswith(NOTES_PREFIX) or not note.endswith(".json") or ".." in Path(note).parts:
        raise ValueError(f"note_path must be a .json file under {NOTES_PREFIX}: {note!r}")
    if not value["reproduction"].strip():
        raise ValueError("An investigation must say what it ran to reproduce the report")
    if value["outcome"] == "reproduced":
        missing = [field for field in ("root_cause", "affected_paths", "test_paths", "invariant") if not value[field]]
        if missing or value["fix_size"] == "none":
            raise ValueError("A reproduced bug needs a root cause, affected and test paths, an invariant "
                             f"and a fix size: {missing}")
        unsafe = [path for path in value["affected_paths"] + value["test_paths"] if not safe_path(path)]
        if unsafe:
            raise ValueError(f"Paths must be relative paths inside the repository: {unsafe}")
    elif value["affected_paths"] or value["test_paths"] or value["fix_plan"] or value["fix_size"] != "none":
        raise ValueError("A report that did not reproduce must not propose a fix")


def safe_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(path.strip()) and not Path(path).is_absolute() and ".." not in parts and ".git" not in parts


def note(value: dict) -> dict:
    """The diagnosis as saved in the repository. ``changed`` is always empty: nothing is fixed yet."""
    return {"reproduced": value["outcome"] == "reproduced", "observed": value["observed"],
            "reproduction": value["reproduction"], "root_cause": value["root_cause"],
            "affected_paths": value["affected_paths"], "test_paths": value["test_paths"], "invariant": value["invariant"],
            "conclusion": value["conclusion"], "fix_size": value["fix_size"], "fix_plan": value["fix_plan"],
            "questions": value["questions"], "tests_run": value["tests_run"], "changed": []}


def apply(state: dict, value: dict, record: dict, workspace) -> None:
    check(value, record.get("changed_files"))
    target = Path(workspace) / value["note_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(note(value), indent=2) + "\n")
    state["investigation"] = {**value, "output": record.get("output")}
    if value["outcome"] == "not_reproduced":
        state.update(status="TASK_COMPLETE", phase="COMPLETE", next_stage=None,
                     completed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        return
    state.update(status="RUNNING", phase="DISCOVERING",
                 next_stage=(state.get("workflow") or {}).get("then") or "requirements_gather")


# A small, reproduced bug skips requirements gathering and plan review: the runner turns
# the diagnosis into a one-task contract and approves it under this policy, which the
# user agreed to on 2026-09-27. An independent Validator and the Completion Owner still
# judge the fix, and the regression test must fail on the original code.
ORIGIN = "bugfix_small_correction"
SMALL_FIX_POLICY = ("A reproduced bug the Investigator sized small becomes one Builder task built from the "
                    "diagnosis and runs without plan approval; an independent Validator and the Completion "
                    "Owner must still accept it, with a regression test that fails before the fix.")


def small_correction(state: dict) -> bool:
    found = state.get("investigation") or {}
    return found.get("outcome") == "reproduced" and found.get("fix_size") == "small"


def correction_contract(state: dict) -> dict:
    """A one-milestone, one-criterion build contract derived from the saved diagnosis."""
    found = state["investigation"]
    owned = list(dict.fromkeys(found["affected_paths"] + found["test_paths"] + [found["note_path"]]))
    objective = "Fix the root cause: " + found["root_cause"]
    validation = ["Run the new regression test against the original code: it must fail",
                  "Run it after the fix: it must pass", "Run the project's existing test suite: it must pass"]
    return {
        "intended_outcome": "The reported misbehavior no longer happens: " + found["observed"],
        "intended_user": "The person who reported the bug",
        "deliverables": owned,
        "required_behaviors": [found["invariant"]],
        "important_failure_cases": [found["observed"]],
        "scope_exclusions": ["Changes unrelated to the diagnosed root cause"],
        "constraints": ["Change only " + ", ".join(owned), "Keep every existing test",
                        f"Record the files you changed in the `changed` list of {found['note_path']}"],
        "permission_boundaries": ["Edit only " + ", ".join(owned)],
        "accepted_assumptions": [{"text": f"The diagnosis in {found['note_path']} is correct: {found['root_cause']}",
                                  "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{"id": "C1", "criterion": found["invariant"],
                                 "verification_method": "A regression test that fails on the original code and "
                                                        "passes after the fix, plus the existing test suite",
                                 "human_review": False}],
        "open_blocking_questions": [],
        "end_to_end_flow": ["Reproduce: " + found["reproduction"], objective, *validation],
        "technical_approach": list(found["fix_plan"]) or [objective],
        "milestones": [{"id": "M1", "objective": objective, "acceptance_criteria": ["C1"],
                        "depends_on": [], "affected_paths": owned}],
        "initial_task": {"objective": objective, "affected_paths": owned, "kind": "implement", "milestone_id": "M1",
                         "requirements": [found["invariant"], "Add a regression test in " + ", ".join(found["test_paths"])
                                          + " that fails on the original code and passes after the fix"],
                         "acceptance_criteria": ["C1"], "validation_plan": validation},
    }


def owns(state: dict) -> bool:
    """The run ended at the investigation (the bug did not reproduce)."""
    return (workflows.kind(state) == "bugfix"
            and (state.get("investigation") or {}).get("outcome") == "not_reproduced")


def render(state: dict) -> str:
    found = state.get("investigation") or {}
    lines = ["NOT REPRODUCED — no code was changed",
             "Workspace: " + str(state.get("workspace")),
             "Diagnosis: " + str(Path(state.get("workspace", "")) / found.get("note_path", NOTES_PREFIX)),
             "", "What was tried: " + found.get("reproduction", ""),
             "Conclusion: " + found.get("conclusion", "")]
    lines += ["Question for the reporter: " + question for question in found.get("questions") or []]
    if found.get("output"):
        lines.append("Investigator report: " + str(found["output"]))
    return "\n".join(lines)
