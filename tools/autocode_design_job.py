"""The design workflow's first stage: an Architect judges a design before anyone builds it.

A run recognized as ``design`` (autocode_workflows) starts with ``STAGE``. The
Architect reads the design document the request names and the code the design
changes, then reports in one of two modes:

- ``review``: the request asks to judge an existing design. The runner rejects
  the report if the stage changed anything in the workspace outside ``review/``,
  writes ``REPORT_PATH`` from the validated report (goals the design meets,
  concerns with severities, questions only the user can answer) and completes
  the run. Nothing is built.
- ``propose``: the request asks for a new design. For now that is produced by
  the build pipeline, as before this stage existed: the run continues at the
  stage saved when recognition began.

Pure module: prompt, schema, transition, rendering. Imports nothing from the
runner. State key written: ``design_review``.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

try:
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_workflows as workflows

STAGE = workflows.DESIGN_STAGE
REPORT_PATH = "review/design-review.json"
ALLOWED_PREFIXES = ("review/",)
SEVERITIES = ("blocking", "advisory")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
CONCERN = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "area", "severity", "summary", "evidence"],
    "properties": {"id": TEXT, "area": TEXT, "severity": {"type": "string", "enum": list(SEVERITIES)},
                   "summary": TEXT, "evidence": TEXT},
}
QUESTION = {
    "type": "object", "additionalProperties": False, "required": ["id", "question", "options"],
    "properties": {"id": TEXT, "question": TEXT, "options": TEXTS},
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["mode", "design_under_review", "verdict", "summary", "satisfied", "concerns", "questions"],
    "properties": {
        "mode": {"type": "string", "enum": ["review", "propose"]},
        "design_under_review": TEXT,
        "verdict": {"type": "string", "enum": ["approve", "request_changes", "not_applicable"]},
        "summary": TEXT,
        "satisfied": TEXTS,
        "concerns": {"type": "array", "items": CONCERN},
        "questions": {"type": "array", "items": QUESTION},
    },
}

PROMPT = """You are the Architect: a senior engineer asked to judge a design before anyone builds it.
You report. You do not write code and you do not edit the design or anything else in the repository.

First decide the mode:
- review: the request asks you to review, challenge or assess an existing design (a document in the
  repository, or one pasted in the request). Do steps 1-5.
- propose: the request asks you to produce a NEW design. Return mode propose, verdict not_applicable,
  design_under_review "", and empty lists; the design is produced by a later stage.

1. Read the design and state its goals. Read the code it changes: the current implementation often
   states requirements the design must keep (ordering, idempotency, compatibility, invariants in
   docstrings and READMEs). A design that reads well can still contradict the code it replaces.
2. Challenge it on correctness, failure modes, operations, scale and migration (including how to roll
   back). You may make a scratch copy OUTSIDE the workspace to run code; never write into the workspace.
   The runner compares the workspace before and after and rejects a review that changed anything
   outside review/.
3. satisfied: the goals the design meets as written, each with why.
4. concerns, each with a severity:
   - blocking: the design cannot be approved until this is resolved (it breaks a requirement, loses or
     duplicates data, has no way back).
   - advisory: worth fixing, not a reason to stop.
   Give evidence: the part of the design and the code that shows it. A concern the design already
   answers is not a concern. Do not pad the list to look thorough.
5. questions: only decisions the requester must make (for example which consumers need strict ordering),
   each with the realistic options.
verdict: request_changes when there is at least one blocking concern, otherwise approve.

Return JSON only, matching the schema the runner gives you. The runner saves your report as
review/design-review.json; you do not write that file.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "execution_engine": engine, "report_path": REPORT_PATH, "workspace_inventory": inventory or {},
            # Present because every provider reads them; nothing is planned yet.
            "goal_contract": None, "current_task": None, "saved_answers": {}}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    stray = sorted(path for path in (changed_files or []) if not str(path).startswith(ALLOWED_PREFIXES))
    if stray:
        raise ValueError("A design review must not change the repository; this attempt changed: " + ", ".join(stray))
    if value["mode"] == "propose":
        if value["concerns"] or value["satisfied"] or value["verdict"] != "not_applicable":
            raise ValueError("A request for a new design is handed on, not reviewed: leave the review fields empty")
        return
    if not value["design_under_review"].strip():
        raise ValueError("A design review must say which design it reviewed")
    blocking = sum(1 for concern in value["concerns"] if concern["severity"] == "blocking")
    if value["verdict"] == "not_applicable" or (value["verdict"] == "approve") != (blocking == 0):
        raise ValueError("verdict must be request_changes exactly when there is a blocking concern")


def apply(state: dict, value: dict, record: dict, workspace) -> None:
    check(value, record.get("changed_files"))
    if value["mode"] == "propose":
        state["design_review"] = {"mode": "propose", "output": record.get("output")}
        state.update(status="RUNNING", phase="DISCOVERING",
                     next_stage=(state.get("workflow") or {}).get("then") or "requirements_gather")
        return
    report = {key: value[key] for key in ("design_under_review", "verdict", "summary", "satisfied",
                                           "concerns", "questions")}
    target = Path(workspace) / REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    counts = {severity: sum(1 for c in value["concerns"] if c["severity"] == severity) for severity in SEVERITIES}
    state["design_review"] = {"mode": "review", **counts, "verdict": value["verdict"], "report_path": REPORT_PATH,
                              "questions": len(value["questions"]), "design_under_review": value["design_under_review"],
                              "output": record.get("output")}
    state.update(status="TASK_COMPLETE", phase="COMPLETE", next_stage=None,
                 completed_at=dt.datetime.now(dt.timezone.utc).isoformat())


def owns(state: dict) -> bool:
    """The run ended at the design review (a review, not a request for a new design)."""
    return workflows.kind(state) == "design" and (state.get("design_review") or {}).get("mode") == "review"


def render(state: dict) -> str:
    found = state.get("design_review") or {}
    lines = [f"DESIGN REVIEW COMPLETE — {found.get('verdict', '?')}: {found.get('blocking', 0)} blocking, "
             f"{found.get('advisory', 0)} advisory, {found.get('questions', 0)} question(s) for you",
             "Reviewed: " + str(found.get("design_under_review", "")),
             "Workspace unchanged: " + str(state.get("workspace")),
             "Report: " + str(Path(state.get("workspace", "")) / found.get("report_path", REPORT_PATH))]
    if found.get("output"):
        lines.append("Architect report: " + str(found["output"]))
    return "\n".join(lines)
