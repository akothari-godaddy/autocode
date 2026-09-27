"""The review workflow: judge an existing change and report findings, changing nothing.

A run recognized as ``review`` (autocode_workflows) goes straight to ``STAGE``:
the Reviewer, on the Validator's route, reads the change the request names (a
patch file, a diff, a branch), the code around it and the tests, runs whatever
it needs in a scratch copy of its own, and returns findings. The runner then:

- rejects the report if the stage changed anything in the workspace outside
  ``review/`` (a review is read-only; the before/after snapshot is the evidence),
- writes ``REPORT_PATH`` from the validated report, so the file always matches
  the schema and the saved report,
- completes the run. No requirements, no plan approval, no Builder.

This module is pure: prompt, schema, and the transition. It imports nothing
from the runner. State keys written: ``review`` (verdict, counts, report path).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

try:
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_workflows as workflows

STAGE = workflows.REVIEW_STAGE
REPORT_PATH = "review/findings.json"
ALLOWED_PREFIXES = ("review/",)
SEVERITIES = ("blocking", "advisory")

FINDING = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "severity", "file", "lines", "summary", "evidence"],
    "properties": {
        "id": {"type": "string"},
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "file": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "integer"}, "maxItems": 2},
        "summary": {"type": "string"},
        "evidence": {"type": "string"},
    },
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "summary", "change_under_review", "findings", "tests_run"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "request_changes"]},
        "summary": {"type": "string"},
        "change_under_review": {"type": "string"},
        "findings": {"type": "array", "items": FINDING},
        "tests_run": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = """You are the Reviewer: an independent engineer asked to judge an existing change before it is merged.
You report findings. You do not fix anything and you do not edit the repository.

What to do:
1. Identify the change under review from the request: a patch file in the repository, a diff, a branch,
   or a described change. Say what you reviewed in change_under_review.
2. Read the change AND its context: the code around it, the README or docs that state how the code is
   supposed to behave, the existing tests. A change can pass its own tests and still break a rule the
   repository states elsewhere.
3. Test where it helps. Make your own scratch copy OUTSIDE the workspace (for example under a temporary
   directory), apply the change there, run the test suite there, and write any targeted test there.
   Never apply the change to, or write files into, the workspace itself; the runner compares the
   workspace before and after and rejects a review that changed it.
4. Report findings, each with a severity:
   - blocking: must be fixed before merge. A behavior that regresses, an invariant that breaks, a
     compatibility change, a defect the change's tests do not catch.
   - advisory: everything else. Style, naming, simplification, a suggestion.
   Point at the file and the line span in the file AS IT WOULD BE AFTER THE CHANGE, and give evidence:
   the rule that is broken, the command you ran and what it printed, the scenario that fails.
5. Verdict: request_changes when there is at least one blocking finding, otherwise approve. Do not
   invent problems to look thorough: a correct change gets approve and, at most, advisory notes.

Return JSON only, matching the schema the runner gives you. The runner saves your report as
review/findings.json in the workspace; you do not write that file.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "execution_engine": engine, "report_path": REPORT_PATH,
            "workspace_inventory": inventory or {},
            # Present because every provider reads them; a review has none of these.
            "goal_contract": None, "current_task": None, "saved_answers": {}}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def stray_changes(changed_files) -> list[str]:
    """Paths the stage changed that a review may not touch."""
    return sorted(path for path in (changed_files or []) if not str(path).startswith(ALLOWED_PREFIXES))


def owns(state: dict) -> bool:
    return workflows.kind(state) == "review"


def apply(state: dict, value: dict, record: dict, workspace) -> None:
    """Enforce read-only-ness, write the findings file, complete the run."""
    stray = stray_changes(record.get("changed_files"))
    if stray:
        raise ValueError("A review must not change the repository; this attempt changed: " + ", ".join(stray))
    counts = {severity: sum(1 for f in value["findings"] if f["severity"] == severity) for severity in SEVERITIES}
    if value["verdict"] == "approve" and counts["blocking"]:
        raise ValueError("A review with blocking findings cannot approve")
    report = {key: value[key] for key in ("verdict", "summary", "change_under_review", "findings", "tests_run")}
    target = Path(workspace) / REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    state["review"] = {**counts, "verdict": value["verdict"], "report_path": REPORT_PATH,
                       "output": record.get("output"), "change_under_review": value["change_under_review"]}
    state.update(status="TASK_COMPLETE", phase="COMPLETE", next_stage=None,
                 completed_at=dt.datetime.now(dt.timezone.utc).isoformat())


def render(state: dict) -> str:
    review = state.get("review") or {}
    lines = [f"REVIEW COMPLETE — {review.get('verdict', '?')}: {review.get('blocking', 0)} blocking, "
             f"{review.get('advisory', 0)} advisory",
             "Reviewed: " + str(review.get("change_under_review", "")),
             "Workspace unchanged: " + str(state.get("workspace")),
             "Findings: " + str(Path(state.get("workspace", "")) / review.get("report_path", REPORT_PATH))]
    if review.get("output"):
        lines.append("Reviewer report: " + str(review["output"]))
    return "\n".join(lines)
