"""Workflow recognition: what kind of engineering job a request is.

AutoPilot runs ``STAGE`` first on every new run, before any requirements or
planning stage, and saves the answer under ``state["workflow"]``; the status
view reports it as ``workflow`` (docs/task-run.md). There are five kinds,
described in scenarios/README.md ("Workflows"). The user never picks one; the
request itself is read. This module is pure: it builds the prompt and schema
and interprets the report. It imports nothing from the runner.

State key written here (and read by autocode_run_view, autopilot):
    workflow: {"kind": one of WORKFLOWS or None, "reason": str, "signals": [str],
               "source": "model" | "user", "then": the stage to run after recognition}
"""
from __future__ import annotations

import json
from pathlib import Path

STAGE = "recognize_workflow"
WORKFLOWS = ("build", "bugfix", "review", "design", "discuss")
# Workflows with their own first stage. The others continue into the build
# pipeline (the stage saved as ``then`` when recognition began) for now.
REVIEW_STAGE = "review_change"
INVESTIGATE_STAGE = "investigate_bug"
DESIGN_STAGE = "review_design"
DISCUSS_STAGE = "answer_question"
# A build that implements an existing, approved design document starts by checking
# the design against the repository (autocode_design_check_job).
DESIGN_CHECK_STAGE = "check_design"
FIRST_STAGE = {"review": REVIEW_STAGE, "bugfix": INVESTIGATE_STAGE, "design": DESIGN_STAGE,
               "discuss": DISCUSS_STAGE}

# Who may approve a goal contract. Normally only the user (actor "user_cli"). A
# contract a workflow built under a policy the user agreed to carries one of these
# origins and is approved by actor "workflow_policy", recorded with the policy text
# (autocode_bug_job.SMALL_FIX_POLICY). goals.approved and the resolver both ask here.
POLICY_ORIGINS = ("bugfix_small_correction",)


def approval_actor_ok(origin, approval) -> bool:
    actor = (approval or {}).get("actor")
    return actor == "user_cli" or (actor == "workflow_policy" and origin in POLICY_ORIGINS)

DESCRIPTIONS = {
    "build": "Make or change something: a feature, a new tool, a behavior change. The user wants working code "
             "at the end. Steps: understand the requirements, plan, review the plan, build, test, review.",
    "bugfix": "Something misbehaves and the user reports it (an error, a wrong result, 'why does X happen'). "
              "The user wants the cause found and fixed. Steps: investigate and reproduce, diagnose, fix, test, review.",
    "review": "Judge an existing change: a patch, a pull request, a diff, a branch. The user wants findings, not "
              "edits. Steps: read the change and its context, test where useful, report findings.",
    "design": "Judge or produce an architecture or design: review a design document, propose how something should "
              "be structured, 'design this but do not implement it'. Nothing is built. Steps: understand, challenge, design.",
    "discuss": "A question, a tradeoff or an investigation: 'should we use X or Y', 'why does the code do this', "
               "'what would break if'. The user wants an answer with evidence, not code. Steps: investigate, answer.",
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["workflow", "reason", "signals"],
    "properties": {
        "workflow": {"type": "string", "enum": list(WORKFLOWS)},
        "reason": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
        # Optional so older reports stay valid; generation schemas require every field.
        "design_document": {"type": "string"},
    },
}

PROMPT = """You are the job recognizer for an AI engineering team. You read one request from a user and decide
which ONE kind of job it is, so the right specialists are assigned. You do not do the job.

The five kinds, with what the user wants at the end of each:
""" + "\n".join(f"- {name}: {text}" for name, text in DESCRIPTIONS.items()) + """

How to decide:
- Go by what the user wants to receive: code (build), a fix plus its cause (bugfix), findings about an
  existing change (review), a design or a design review with nothing built (design), or an answer (discuss).
- "Fix", "figure out why", "stopped working", "creates duplicates", a described misbehavior: bugfix, even
  when the user also names a suspected cause.
- "Review", "look over", "is it safe to merge", a named patch, PR or diff: review. Reviewing a design
  document is design, not review.
- design means the user wants a DESIGN back: a new design produced ("design how X should work",
  "how should we structure", "sketch the architecture, don't implement") or an existing design
  document reviewed.
- discuss means the user wants an ANSWER back: a choice between named options ("should we use A or B",
  "stay X or move to Y"), a reason ("why does the code do X") or a consequence ("what would break if").
  This holds for architecture questions too, and when the user asks for the analysis or recommendation
  to be written down (a decision record or note), as long as nothing is to be built or fixed and no
  design document is to be produced or reviewed. "I want the analysis, not code" is discuss.
- When a request asks for several things, choose the kind of the FIRST thing that must happen. "Review
  this and fix what you find" starts as review; "why does this fail, then fix it" starts as bugfix.
- Do not guess build when unsure. Build is the most expensive path; the other kinds are cheaper and can
  lead to a build later in the same conversation.

Return JSON only: {"workflow": one of build|bugfix|review|design|discuss, "reason": one sentence,
"signals": the words or phrases in the request that decided it, "design_document": for a build that asks
to implement an EXISTING design document as written (approved, decided, "don't redesign it"), that
document's path in the repository; otherwise ""}. Read nothing but the request and the file listing
below; do not open files.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    # goal_contract, current_task and saved_answers are empty by definition here (nothing has
    # been planned yet); they and execution_engine are present because every provider reads
    # them from the packet.
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "execution_engine": engine, "workspace_inventory": inventory or {},
            "goal_contract": None, "current_task": None, "saved_answers": {},
            # A request with a Figma design is still recognized by what the user wants back.
            **({"figma_file": state["settings"]["figma_file"]}
               if (state.get("settings") or {}).get("figma_file") else {})}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def begin(state: dict, then: str) -> None:
    """Make recognition the first stage of a new run; ``then`` is the stage that follows it."""
    state["workflow"] = {"kind": None, "reason": "", "signals": [], "source": None, "then": then}
    state["next_stage"] = STAGE


def apply(state: dict, value: dict, record: dict) -> None:
    """Save the recognized kind and hand over to the stage recognition deferred."""
    if value.get("workflow") not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {value.get('workflow')!r}; expected one of {WORKFLOWS}")
    # ``then`` stays the build pipeline's entry stage: a workflow with its own first
    # stage (FIRST_STAGE) may still hand over to the build pipeline afterwards.
    then = (state.get("workflow") or {}).get("then") or "requirements_gather"
    state["workflow"] = {"kind": value["workflow"], "reason": value.get("reason", ""),
                         "signals": list(value.get("signals") or []), "source": "model",
                         "output": record.get("output"), "then": then}
    design = approved_design(state, value)
    if design:
        state["workflow"]["design_document"] = design
    state.update(status="RUNNING",
                 next_stage=DESIGN_CHECK_STAGE if design else FIRST_STAGE.get(value["workflow"]) or then)


def approved_design(state: dict, value: dict) -> str:
    """The approved design a build asks to implement, if the recognizer named one that exists."""
    design = str(value.get("design_document") or "").strip()
    if value.get("workflow") != "build" or not design:
        return ""
    parts = Path(design).parts
    if Path(design).is_absolute() or ".." in parts or not (Path(state.get("workspace") or ".") / design).is_file():
        return ""
    return design


def kind(state: dict) -> str | None:
    """The recognized workflow, or None before recognition (and for runs that predate it)."""
    return (state.get("workflow") or {}).get("kind")
