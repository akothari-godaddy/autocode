"""Read-only diagnosis and bounded repair planning; never writes application code.

Owns three stages: ``astra_resolve`` (why did a reviewed build fail, and what bounded
rework fixes it), the bug-fix workflow's ``investigate_bug`` (autocode_bug_job:
reproduce a reported misbehavior and diagnose it before any fix exists), and the
discuss workflow's ``answer_question`` (autocode_discuss_job: answer a question or
weigh a tradeoff from the repository, building nothing)."""
import copy
import json
from pathlib import Path
try:
    from .. import autocode_support as support, autocode_goals as goals, autocode_bug_job as bug_job
    from .. import autocode_discuss_job as discuss_job
except ImportError:
    import autocode_support as support
    import autocode_goals as goals
    import autocode_bug_job as bug_job
    import autocode_discuss_job as discuss_job
from . import autoplanner
from .common import ModelRequest, capped_route, execution_request


def prepare_answer(state):
    """The Analyst inherits the planner model on its own route and session, effort capped
    at medium, with the same scratch-copy rule as the Investigator."""
    state["phase"] = "INVESTIGATING"
    state["settings"]["roles"].setdefault("analyst", capped_route(state["settings"]["roles"]["astra"]))
    prompt, metrics = discuss_job.prompt(
        state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000),
        autoplanner.engine_for(state["settings"], "analyst"))
    return ModelRequest("astra", "analyst", prompt, metrics, discuss_job.SCHEMA, True)


def prepare_investigation(state):
    """The Investigator inherits the planner model on its own route and session, so its
    reproduction context never leaks into later planning or review. It may write, but only
    to a scratch copy of its own; the runner rejects any change to the workspace itself."""
    state["phase"] = "INVESTIGATING"
    state["settings"]["roles"].setdefault("investigator", copy.deepcopy(state["settings"]["roles"]["astra"]))
    prompt, metrics = bug_job.prompt(
        state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000),
        autoplanner.engine_for(state["settings"], "investigator"))
    return ModelRequest("astra", "investigator", prompt, metrics, bug_job.SCHEMA, True)


def apply_job(stage, state, value, record, workspace):
    """Autopilot hands a job stage's validated report here. The Analyst's answer completes
    the run. For the Investigator, a small reproduced bug becomes one Builder task at once;
    anything else continues where bug_job.apply sent it."""
    if stage == discuss_job.STAGE:
        return discuss_job.apply(state, value, record, workspace)
    bug_job.apply(state, value, record, workspace)
    if bug_job.small_correction(state):
        start_small_correction(state, workspace)


def start_small_correction(state, workspace):
    """Install the diagnosis as a one-task contract, approve it under the recorded policy
    (never as the user), and assign the Builder task exactly as a user approval would."""
    try:
        from .. import autocode_dispatch as dispatch
    except ImportError:
        import autocode_dispatch as dispatch
    body = bug_job.correction_contract(state)
    goals.install_draft(state, body, origin=bug_job.ORIGIN)
    goals.validate_body(state, body, ready=True)
    contract = state["goal_contract"]
    event = {"kind": "goal_approval", "actor": "workflow_policy", "policy": bug_job.SMALL_FIX_POLICY,
             "at": support.now(), "token": goals.token(contract)}
    state.setdefault("user_events", []).append(event)
    contract.update(approval_status="approved", approval_event=event)
    state.update(phase="READY_TO_EXECUTE", status="RUNNING", pending_questions=[])
    decision = goals.initial_decision(body)
    goals.assign_task(state, decision, support.snapshot(Path(workspace)))
    state.update(next_action=decision["next_objective"], affected_paths=decision["affected_paths"],
                 next_stage=dispatch.build_stage(state))
    goals.record_decision(state, decision)
    if not goals.approved(state):
        raise ValueError("The small-correction contract did not pass the approval check")

def guard(state, workspace):
    goals.execution_guard(state)
    request = state.get('resolution_request') or {}
    if (request.get('contract_hash') != state['goal_contract']['hash']
            or request.get('task_id') != state.get('current_task', {}).get('id')
            or request.get('source_revision') != support.snapshot(workspace)['revision']):
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Repair diagnosis needs the current reviewed source and task')
    for path, digest in request.get('evidence_hashes', {}).items():
        if not Path(path).is_file() or support.file_hash(path) != digest:
            raise support.Paused('PAUSED_STALE_HANDOFF', 'Repair evidence changed; review again before resolving')


def prepare(state, stage, state_path, schema_dir):
    if stage == bug_job.STAGE:
        return prepare_investigation(state)
    if stage == discuss_job.STAGE:
        return prepare_answer(state)
    if stage != 'astra_resolve':
        raise ValueError(f'Autoresolver cannot run {stage}')
    guard(state, Path(state['workspace']))
    # Inherit the planner model, not its conversation. A dedicated route keeps
    # resolver sessions separate from planning, completion and implementation.
    state['settings']['roles'].setdefault('resolver', copy.deepcopy(state['settings']['roles']['astra']))
    request = execution_request(state, 'astra_review', state_path, schema_dir)
    instruction, payload = request.prompt.split('CURRENT HANDOFF DATA\n', 1)
    data = json.loads(payload)
    data.update(stage=stage, resolution_request=state['resolution_request'],
                execution_engine=state['settings']['roles']['resolver'].get('engine', state['settings'].get('engine', 'codex')))
    schema = copy.deepcopy(request.schema)
    schema['properties']['status']['enum'] = ['REWORK', 'BLOCKED']
    schema['properties']['diagnosis'] = goals.STRING
    schema['required'].append('diagnosis')
    prompt = ('You are AUTORESOLVER, a read-only failure diagnostician, not a Builder or completion owner. '
              'Inspect the rejected build, review findings and exact evidence. Return a nonempty diagnosis '
              'and one bounded REWORK next_task with defect evidence and concrete validation_plan retests. '
              'The runner exports this task as a one-node repair DAG. Preserve the whole integrated batch. '
              'Return the complete unchanged acceptance_criteria list from the handoff; select the repair subset only in next_task.acceptance_criteria. '
              'Criterion statuses and evidence remain owned by the reviewer, not the resolver. '
              'Do not approve work, change requirements, weaken tests, or modify source. '
              'If scope or permission must change, return BLOCKED with a structured user_request; never grant it yourself.\n'
              + instruction + '\nResolver constraint overrides completion choices: only REWORK or BLOCKED.\n'
              + 'CURRENT HANDOFF DATA\n' + json.dumps(data, indent=2))
    metrics = {**request.metrics, 'estimated_prompt_tokens': (len(prompt) + 3) // 4}
    return ModelRequest('astra', 'resolver', prompt, metrics, schema, False)


def validate(state, value, record, workspace):
    guard(state, workspace)
    if record.get('changed_files') or record['source_revision'] != state['resolution_request']['source_revision']:
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Resolver must leave the reviewed source unchanged')
    if value.get('status') not in ('REWORK', 'BLOCKED') or not value.get('diagnosis', '').strip():
        raise ValueError('Resolver requires a diagnosis and a REWORK or BLOCKED decision')
    if value['status'] == 'REWORK' and (not value.get('evidence') or value.get('next_task', {}).get('kind') != 'implement'):
        raise ValueError('Resolver must supply an evidence-backed implementation repair')


def preserve_review_criteria(state, value):
    """A focused diagnosis may omit criteria, but cannot redefine or verify them."""
    authoritative = state['acceptance_criteria']
    by_id = {row['id']: row for row in authoritative}
    seen = set()
    for row in value['acceptance_criteria']:
        cid = row['id']
        if cid in seen:
            raise support.Paused('PAUSED_INVALID_OUTPUT', 'Duplicate acceptance IDs')
        seen.add(cid)
        if cid not in by_id or row['criterion'] != by_id[cid]['criterion']:
            raise support.Paused('PAUSED_CRITERIA_CHANGE', 'Repair cannot change approved acceptance criteria')
    # Preserve the last review's order, statuses and evidence, including omitted
    # criteria. Diagnosis supplies repair instructions, not a new review verdict.
    return {**value, 'acceptance_criteria': copy.deepcopy(authoritative)}
