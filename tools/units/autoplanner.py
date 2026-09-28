"""Autoplanner owns requirements, draft plans and independent plan review."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re

try:
    from .. import autocode_goals as goals, autocode_support as s
except ImportError:
    import autocode_goals as goals
    import autocode_support as s

STAGES = ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
S, SS, obj = goals.STRING, goals.STRINGS, goals.obj
CONCERN = obj({"id": S, "concern": S, "evidence_refs": SS, "requested_change": S,
               "acceptance_test": S, "blocking": {"type": "boolean"}})
RESPONSE = obj({"concern_id": S, "response": S, "evidence_refs": SS,
                "change": S, "acceptance_test": S})
DECISION = obj({"concern_id": S, "decision": S, "rationale": S,
                "acceptance_test": S, "resolved": {"type": "boolean"}})
REQUIREMENT = obj({"id": S, "text": S, "source_quote": S})
CONFLICT = obj({"requirement_ids": SS, "description": S})
CONFLICT_RESOLUTION = obj({"requirement_ids": SS,
    "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]},
    "answer_id": S, "source_quote": S, "resolution": S})
CHANGE = obj({"item": S, "change": {"type": "string", "enum": ["removed", "reworded", "permission_changed"]},
              "basis": {"type": "string", "enum": ["user_answer", "user_feedback", "agent_proposed"]},
              "answer_id": S, "replacement": S})
TRACE = obj({"requirement_id": S, "disposition": {"type": "string", "enum": ["covered", "excluded", "superseded"]},
             "evidence": S})
# New reports use the structured form; this is also the generation schema, so
# the model needs a concrete item shape. A report produced before structured
# assumptions (a plain string item) is still accepted by apply_planning, which
# validates only the structured items; goals.normalize_assumption reads both.
ASSUMPTION = obj({"id": S, "text": S, "kind": goals.QUESTION["properties"]["kind"],
                  "category": goals.QUESTION["properties"]["category"],
                  "convention_ref": S, "rationale": S, "supports": SS})
ASSUMPTIONS = {"type": "array", "items": ASSUMPTION}
IGNORED_REQUIREMENT = obj({"requirement_id": S, "reason": S,
    "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]}, "event_id": S})
SCHEMAS = {
    "requirements_gather": obj({
        "summary": S, "intended_outcome": S, "required_behaviors": SS,
        "constraints": SS, "acceptance_tests": SS, "source_refs": SS,
        "proposed_assumptions": ASSUMPTIONS,
        "open_questions": {"type": "array", "maxItems": 3, "items": goals.QUESTION},
        "requirements": {"type": "array", "items": REQUIREMENT},
        "ignored_statements": SS,
        "conflicts": {"type": "array", "items": CONFLICT},
    }),
    "astra_discovery": obj({"contract": goals.BODY_SCHEMA, "summary": S,
                            "code_refs": SS, "alternatives": SS, "uncertainties": SS,
                            "contract_changes": {"type": "array", "items": CHANGE},
                            "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                            "requirement_trace": {"type": "array", "items": TRACE}}),
    "astra_challenge": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
    "glm_revise": obj({"contract": goals.BODY_SCHEMA, "summary": S, "code_refs": SS,
                       "responses": {"type": "array", "items": RESPONSE},
                       "contract_changes": {"type": "array", "items": CHANGE},
                       "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                       "requirement_trace": {"type": "array", "items": TRACE}}),
    "astra_finalize": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S,
                           "decisions": {"type": "array", "items": DECISION},
                           "contract_changes": {"type": "array", "items": CHANGE},
                           "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                           "requirement_trace": {"type": "array", "items": TRACE}}),
}
# Optional for old saved reports; new prompts require this whenever intent must change.
SCHEMAS["requirements_gather"]["properties"]["proposed_reframes"] = {
    "type": "array", "items": obj({"requirement_id": S, "proposal": S, "question_id": S})}
# Optional; required only when a refreshed handoff drops a requirement the
# previous handoff had (goals.check_requirement_handoff enforces the citation).
SCHEMAS["requirements_gather"]["properties"]["ignored_requirements"] = {
    "type": "array", "items": IGNORED_REQUIREMENT}
# A discoverable question is answered from the workspace, never by the user.
# machine_resolutions are accepted only during the runner's one investigation
# pass, bound to the report that raised the question (handoff_hash). An
# access_blocker records that the source needed is missing or unreadable; the
# question must then remain as a kind="decision" question for the user.
MACHINE_RESOLUTION = obj({"question_id": S, "resolution": S, "source_refs": SS, "handoff_hash": S})
ACCESS_BLOCKER = obj({"question_id": S, "reason": S})
INVESTIGATION_STAGES = ("requirements_gather", "astra_discovery", "glm_revise")
for _stage in INVESTIGATION_STAGES:
    SCHEMAS[_stage]["properties"]["machine_resolutions"] = {"type": "array", "items": MACHINE_RESOLUTION}
    SCHEMAS[_stage]["properties"]["access_blockers"] = {"type": "array", "items": ACCESS_BLOCKER}
# A rejected assumption becomes a runner-owned obligation. The Planner proposes
# how the requirements it supported are still met (remediation_records); only a
# Plan Reviewer decision bound to that exact record's hash discharges it.
REMEDIATION = obj({"obligation_id": S, "assumption_id": S, "approach": S, "evidence_refs": SS,
                   "covered_requirements": SS, "episode_id": S})
OBLIGATION_DECISION = obj({"obligation_id": S, "remediation_hash": S, "resolved": {"type": "boolean"},
                           "rationale": S, "evidence_refs": SS})
for _stage in ("astra_discovery", "glm_revise"):
    SCHEMAS[_stage]["properties"]["remediation_records"] = {"type": "array", "items": REMEDIATION}
for _stage in ("astra_challenge", "astra_finalize"):
    SCHEMAS[_stage]["properties"]["obligation_decisions"] = {"type": "array", "items": OBLIGATION_DECISION}


def enabled(state):
    return bool(state.get("settings", {}).get("joint_planning"))


def is_planning(state, stage):
    return enabled(state) and stage in STAGES


def role_for(state, stage):
    if is_planning(state, stage) and stage == "requirements_gather":
        return "requirements"
    if is_planning(state, stage) and stage in ("astra_discovery", "glm_revise"):
        return "glm"
    return "astra" if stage.startswith("astra") else stage


def route_for(state, stage, role=None):
    """Return the saved model route for a semantic workflow role.

    Completion remains a Plan Reviewer-format decision stage, but it intentionally has
    its own model, reasoning level, and session so plan review and completion
    ownership can be tuned independently.
    """
    if stage in ("astra_resolve", "astra_diagnose"):
        return "resolver"
    if stage == "requirements_gather":
        return "requirements"
    role = role or role_for(state, stage)
    roles = state.get("settings", {}).get("roles", {})
    if stage in ("astra_challenge", "astra_finalize") and "plan_reviewer" in roles:
        return "plan_reviewer"
    if stage in ("astra_review", "astra_checkpoint") and "completion" in roles:
        return "completion"
    return role


def engine_for(settings, role):
    return settings.get("roles", {}).get(role, {}).get("engine", settings.get("engine", "codex"))


# Independent Plan Reviewer route (user 2026-09-26): never the Planner's model.
PINNED_REVIEWER_MODEL = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def start(state):
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.start_planning(state)


def review_call_limit(state):
    limit = state.get("planning", {}).get("review_call_limit", 2)
    if type(limit) is not int or limit < 2:
        raise ValueError("Planning review call limit must be an integer of at least 2")
    return limit


def set_review_call_limit(state, limit):
    """An explicit current-cycle allowance, not a refund or approval."""
    if (not enabled(state) or not state.get("planning")
            or state.get("status") != "PAUSED_PLANNING_BUDGET"
            or state.get("next_stage") not in ("astra_challenge", "astra_finalize")
            or any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts"))):
        raise ValueError("Planning allowance requires a reconciled PAUSED_PLANNING_BUDGET checkpoint")
    previous = review_call_limit(state)
    if type(limit) is not int or limit < previous or limit < state["planning"]["astra_calls"]:
        raise ValueError("Planning review call limit must be a finite integer no smaller than the current limit and usage")
    if limit == previous:
        return
    state["planning"]["review_call_limit"] = limit
    state.setdefault("user_events", []).append({
        "kind": "planning_budget_change", "actor": "user_cli", "at": s.now(),
        "previous_limit": previous, "limit": limit, "calls_used": state["planning"]["astra_calls"],
        "stage": state["next_stage"], "contract_token": goals.token(state["goal_contract"])})


def charge(state, stage):
    if stage not in ("astra_challenge", "astra_finalize"):
        return
    planning = state["planning"]
    limit = review_call_limit(state)
    if planning["astra_calls"] >= limit:
        raise s.Paused("PAUSED_PLANNING_BUDGET", f"{planning['astra_calls']}/{limit} plan-review calls used. "
                       "Inspect the saved exchange; use --planning-review-call-limit N to explicitly increase "
                       "this cycle's total allowance, then --resume-paused; or --feedback for a new cycle. "
                       "No automatic budget extension or approval.")
    planning["astra_calls"] += 1


def _coverage(rows, concerns):
    ids = [row["concern_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != {c["id"] for c in concerns}:
        raise ValueError("Every plan-review concern needs exactly one response/decision using its ID")
    for row in rows:
        if any(isinstance(value, str) and not value.strip() for value in row.values()):
            raise ValueError("Planning responses and decisions must be substantive")


def apply(state, stage, value, record):
    """Compatibility entry; planning transitions belong to Autopilot."""
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.apply_planning(state, stage, value, record)


PROMPTS = {
    "requirements_gather": """You are the Requirements Gatherer, in your own read-only session.
Inspect the user's idea and relevant workspace source. Return only a requirements handoff:
intended outcome, stated behaviors, constraints, acceptance tests, source references,
up to three genuinely blocking questions, and clearly labeled proposed assumptions.
Do not create a technical approach, milestone, dependency graph, or implementation plan.
Do not treat a proposed default as a user answer. Do not implement.
When previous_requirements_handoff exists, retain its still-relevant requirements and
unanswered questions with stable IDs. A scope correction does not answer unrelated
questions (for example where the real backend lives). Prioritize those blockers over
new optional choices; do not silently replace them when refreshing the handoff.
Preserve the user's literal requested outcome, even if infeasible. Never translate an
absolute guarantee into a weaker measurable promise without asking whether the user
accepts that change. Keep the original in requirements/required_behaviors; put each
suggested replacement in proposed_reframes (requirement_id, proposal, question_id)
with an explicit acceptance question in open_questions. Otherwise use proposed_reframes=[].
Distinguish the desired outcome from implementation instructions. Preserve explicitly
requested technology (for example Redis and three workers); if it appears to be a
suggested solution to a performance goal, ask whether it is mandatory or negotiable.
Do not silently discard it or assume it is the only way to achieve the outcome.
A later explicit correction can supersede an earlier statement: cite the saved event
and ask only about what remains ambiguous. Do not ask the user to repeat a clear correction.
proposed_reframes is only for agent-proposed changes, never user-authored corrections.
A narrow correction leaves unrelated exclusions in force: adding named actions permits
those actions, not every possible control. Do not ask permission to expand beyond them.
Use workspace_inventory to locate relevant existing code, then READ 4-6 key files
before making claims about current behavior. Do not explore indefinitely — read
enough to understand the architecture, then produce your structured output.
A missing package.json or src/ directory does not mean no application exists.
source_refs must include the actual repository-relative files read (optional :line),
not only 'task'; do not claim inspected behavior from filenames alone. A truncated
inventory is not evidence of absence. Use source_refs=[] only for an empty workspace.
Return requirements: each has an id, the requirement text, and a source_quote copied
verbatim from the task or a saved user event. Put requirement-like sentences you are
not carrying (must, must not, never, only, required, exactly) in ignored_statements
with the reason. Put unresolved contradictions in conflicts with the requirement ids.
Do not label an explicit saved clarification or a historical/current distinction as
an unresolved conflict. Preserve the applicable requirements and their provenance.
The runner saves this report as a separate artifact for the Planner.
The requirement_coverage_checklist contains the exact task sentences checked by
the runner. Account for every entry in requirements using a verbatim source_quote,
or in ignored_statements with the exact statement and a substantive reason.
Include requirements from the rest of the task and saved user events as well.
""",
    "astra_discovery": """You are the Planner, in a session separate from the Requirements Gatherer.
For a new run, use requirements_handoff and its saved artifact as your input; do not silently
replace its stated requirements or convert its proposed assumptions into user decisions.
Carry unresolved requirements questions into open_blocking_questions unless saved answers
resolve them. Older saved runs may lack a requirements handoff; only then gather missing
requirements yourself.
Read 5-8 key source files to understand the architecture, then STOP exploring and return
your structured output (code_refs, alternatives, uncertainties, contract). Do not read
every file — the workspace_inventory lists candidates; pick the most relevant ones.
First assess readiness. If any blocking question remains, return a clarification-only
contract: preserve known requirements and questions, set technical_approach=[] and
milestones=[], and do not invent a product, architecture, files, task DAG or initial task.
Only after blocking questions are answered, originate the concrete technical approach,
milestones and acceptance tests. Proposed defaults are not answers.
Mocks may support tests, but cannot replace the real behavior requested by the user.
If the real integration interface or implementation is missing, inspect or ask for it;
do not invent a mock-only deliverable or label real functionality as an accepted limitation.
For every milestone, state depends_on as prerequisite milestone IDs or [] when it can
start independently. Base those edges on actual interfaces, shared files, sequencing
and validation needs. Do not turn milestones into parallel jobs or launch any work.
Declare affected_paths for each milestone, including its tests and shared files.
Autopilot dispatches the Builder scheduler using approved dependencies and disjoint path ownership.
Do not implement. You may challenge assumptions and propose better approaches.
""",
    "astra_challenge": """You are the independent Plan Reviewer, challenging the Planner's draft (first review stage).
Inspect additional source when needed. Check every dependency edge, missing prerequisite,
cycle and claimed independent milestone against source evidence and interface ownership.
Check affected_paths for every milestone; overlapping writes must not be called independent.
Identify missing requirements, unsupported assumptions, unnecessary complexity and weak tests.
Compare the original task and saved user events with the handoff and contract, not just
the contract with itself. Flag weakened guarantees, unaccepted reframes, missed existing
functionality and proposed solutions treated as settled choices. Require explicit user
acceptance for changes to the requested outcome; useful suggestions alone cannot resolve them.
If real functionality is demonstrated only by a mock, raise a blocking concern requiring
the actual integration plan or a user decision about scope. An 'unverified' assumption
does not authorize replacing real behavior with a prototype.
Give concise, numbered concerns, evidence references,
requested changes and acceptance tests. Do not manufacture objections or write a second essay.
""",
    "glm_revise": """You are the Planner, investigating the Plan Reviewer's concerns. Respond to EVERY concern by ID
with evidence_refs, reasoning, the concrete change (or evidence-backed pushback) and a test.
Revise the complete contract, including depends_on for every milestone, and identify what changed.
You are a planning partner, not merely
a coder: retain your approach where source evidence supports it. Never hide unresolved questions.
If a concern exposes an unknown real integration or a proposed reduction to mock-only
scope, ask a blocking question. Do not settle it by adding an agent_proposed assumption
that the requested real behavior will remain unverified. Testing mocks is not implementing
the real requirement. Preserve the user's outcome until they explicitly change it.
""",
    "astra_finalize": """You are the independent Plan Reviewer, making the final planning decision (final review stage).
Settle EVERY concern by ID using the Planner's evidence-backed responses and source inspection as needed.
Confirm that milestone dependencies are complete and acyclic, and that [] is used only
for genuinely independent work. Do not schedule or launch milestones.
Return the proposed final contract and concise decisions/rationales/tests. Include initial_task
in the contract: objective, affected_paths, kind (implement or validate), milestone_id,
requirements, acceptance_criteria IDs, validation_plan. Its milestone must have depends_on [].
Make it a substantial, coherent,
executable milestone including related changes, tests, local fixes and evidence.
If blocked with no safe first task, use kind=none and empty task strings/lists.
Unresolved decisions MUST appear in open_blocking_questions, never silently become assumptions.
There is no further debate round. The user must approve this exact plan before implementation.
""",
}


QUESTION_POLICY = """
QUESTION CLASSIFICATION. Every question object carries kind, category and delegable.
kind="discoverable" only when the answer is a fact in the workspace you have not read yet
(where something is configured, which interface exists). Prefer reading it now; the runner
never shows a discoverable question to the user. kind="decision" for a choice only the user
can make. category names what the answer changes: cost, quota, permission,
external_side_effect, requested_outcome, behavior, technical or other.
delegable=true only when proposed_default is a safe choice the user may accept wholesale;
always false for cost, quota, permission, external_side_effect and requested_outcome.
Where the report has machine_resolutions and access_blockers, use [] unless
investigation_request is present.
"""

ASSUMPTION_POLICY = """
ASSUMPTIONS. Each proposed_assumptions entry is {id, text, kind, category, convention_ref,
rationale, supports}. Give it a stable id (A1, A2, ...) and keep ids across refreshes.
Use kind="inferable" with a convention_ref (repository path:line, or a saved event id) and a
rationale that establish the convention. supports lists the requirement ids it underpins.
Never mark cost, quota, permission, external_side_effect or requested_outcome inferable; ask a
decision question instead. When previous_requirements_handoff exists and you drop one of its
requirements, list it in ignored_requirements as {requirement_id, reason, basis, event_id}
citing the saved user answer or feedback event that authorizes it; otherwise use [].
"""

INVESTIGATION_POLICY = """
INVESTIGATION PASS. investigation_request lists discoverable questions from your previous
report (prior_report), bound to handoff_hash. This is the only investigation pass in this
clarification episode. For each question, do exactly one of:
- read the workspace and add a machine_resolutions entry {question_id, resolution,
  source_refs (existing repository files you read, optional :line), handoff_hash}, and remove
  the question from your questions (only for category technical or other);
- keep it as a kind="decision" question when it is really the user's choice;
- if the source needed is missing or unreadable, keep it as a kind="decision" question and add
  an access_blockers entry {question_id, reason}.
Anything still discoverable after this pass is shown to the user as a decision. Otherwise
return the complete report as before.
"""


OBLIGATION_POLICY = """
REJECTED ASSUMPTIONS. deferred_obligations lists assumptions the user rejected; never rely on a
rejected assumption again, even reworded. An open obligation of kind human_decision must be asked
as a kind="decision" question whose id is the obligation id; the plan stays clarification-only
until the user answers it. For an open remediation obligation, the Planner may add a
remediation_records entry {obligation_id, assumption_id, approach, evidence_refs,
covered_requirements (exactly the obligation's supports, each covered in requirement_trace),
episode_id (clarification_episode.id)}. The Plan Reviewer must add one obligation_decisions entry
{obligation_id, remediation_hash, resolved, rationale, evidence_refs} for every pending_review
obligation, using its current remediation_hash; resolved=false in the first review needs a
blocking concern citing the obligation id. At final review, any obligation still unresolved is
asked as a decision question under its id, and initial_task.kind must be "none". Otherwise use
[] for remediation_records and obligation_decisions.
"""


def workspace_inventory(workspace, task, limit=40, scan_limit=5000):
    """Bounded filesystem inventory; works in repositories and ordinary directories."""
    root = Path(workspace)
    ignored = {".git", ".autocode", ".venv", "venv", "node_modules", "__pycache__",
               ".next", "dist", "build", ".cache"}
    words = set(re.findall(r"[a-z]{3,}", task.lower()))
    candidates = []
    truncated = False
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ignored and not Path(directory, d).is_symlink())
        for name in sorted(files):
            path = Path(directory, name)
            if name in ignored or name.endswith((".pyc", ".png", ".jpg", ".lock")) or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            score = sum(word in relative.lower() for word in words)
            candidates.append((-score, relative))
            if len(candidates) >= scan_limit:
                truncated = True
                break
        if truncated:
            break
    candidates.sort()
    return {"files": [name for _, name in candidates[:limit]],
            "truncated": truncated or len(candidates) > limit,
            "instruction": "File names are navigation hints, not evidence of behavior. Read relevant files."}


def context(state, stage, state_path):
    exchange = copy.deepcopy(state.get("planning", {}))
    for entry in exchange.get("reports", {}).values():
        # Current contract is included once. Older full drafts stay retrievable
        # via the artifact path; every concern, response and decision remains in
        # the handoff because later review stages must account for every ID.
        entry["report"].pop("contract", None)
        # Trim verbose fields from older reports to keep the prompt bounded.
        report = entry.get("report") or {}
        for key in ("code_refs", "alternatives", "uncertainties", "summary"):
            if isinstance(report.get(key), list) and len(report[key]) > 5:
                report[key] = report[key][:5]
            elif isinstance(report.get(key), str) and len(report[key]) > 500:
                report[key] = report[key][:500] + "…"
    packet = {"task": state["task"], "workspace": state["workspace"], "state_file": str(state_path),
              "joint_planning": True, "execution_engine": engine_for(state["settings"], route_for(state, stage)),
              "stage": stage,
              "goal_contract": None if stage == "requirements_gather" else state.get("goal_contract"),
              "requirements_handoff": None if stage == "requirements_gather" else state.get("requirements_handoff"),
              "requirements_history": None if stage == "requirements_gather" else [
                  {"output": entry.get("output"),
                   "requirements": [{"id": row["id"], "source_quote": row.get("source_quote", "")}
                                    for row in (entry.get("report") or {}).get("requirements", [])],
                   "conflicts": (entry.get("report") or {}).get("conflicts", [])}
                  for entry in state.get("requirements_history", [])
                  if (entry.get("report") or {}).get("conflicts")],
              "saved_answers": state.get("answers", {}), "brief_feedback": state.get("brief_feedback", []),
               "planning": exchange,
               "budget": f"{review_call_limit(state)} plan-review calls in this cycle, including failed attempts; "
                         "only an explicit operator action can extend the allowance"}
    if stage == "requirements_gather":
        packet["requirement_coverage_checklist"] = goals.cue_sentences(state.get("task"))
    if state["settings"].get("figma_file"):
        packet["figma_file"] = state["settings"]["figma_file"]
    packet['user_events'] = state.get('user_events', [])
    if stage == "requirements_gather":
        packet['previous_requirements_handoff'] = state.get('requirements_handoff')
    if stage in ("requirements_gather", "astra_discovery"):
        packet['workspace_inventory'] = workspace_inventory(state['workspace'], state['task'])
    try:
        from .. import autocode_figma as figma
    except ImportError:
        import autocode_figma as figma
    figma_instruction = figma.instructions(state["settings"])
    planning_policy = "" if stage == "requirements_gather" else (
        goals.DECISION_PROVENANCE + goals.CONTRACT_REFERENCES + s.MILESTONE_POLICY)
    clarification_policy = ("" if stage == "astra_challenge" else QUESTION_POLICY) + (
        ASSUMPTION_POLICY if stage == "requirements_gather" else "")
    if stage != "requirements_gather":
        packet["deferred_obligations"] = state.get("deferred_obligations", [])
        packet["clarification_episode"] = state.get("clarification_episode")
        clarification_policy += OBLIGATION_POLICY
    request = state.get("investigation_request")
    if request and request.get("stage") == stage:
        # Correctness must not depend on provider-session memory: the pass gets
        # everything it needs explicitly.
        packet["investigation_request"] = request
        clarification_policy += INVESTIGATION_POLICY
    prompt = (PROMPTS[stage] + figma_instruction + planning_policy + clarification_policy + s.COMMON
              + "\nWork read-only; return the report, the runner saves it.\nCURRENT HANDOFF DATA\n"
              + json.dumps(packet, indent=2))
    return prompt, {"estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
                    "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000)}


def prepare(state, stage, state_path, schema_dir):
    from .common import ModelRequest
    if stage not in STAGES:
        raise ValueError(f"Autoplanner cannot run {stage}")
    joint = is_planning(state, stage)
    state["phase"] = "PLANNING" if joint else "DISCOVERING"
    prompt, metrics = context(state, stage, state_path) if joint else s.context_packet(state, stage, state_path)
    role = role_for(state, stage)
    return ModelRequest(role, route_for(state, stage, role), prompt, metrics,
                        SCHEMAS[stage] if joint else goals.DISCOVERY_SCHEMA, False)


def apply_result(state, stage, value, record):
    """Compatibility entry; Autopilot consumes the planner result."""
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.apply_planning_result(state, stage, value, record)
