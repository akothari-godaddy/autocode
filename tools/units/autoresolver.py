"""Read-only diagnosis and bounded repair planning; never writes application code."""
import copy
import json
from pathlib import Path
try:
    from .. import autocode_support as support, autocode_goals as goals
except ImportError:
    import autocode_support as support
    import autocode_goals as goals
from .common import ModelRequest, execution_request





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
    if stage == 'astra_diagnose':
        return prepare_diagnosis(state, stage, state_path, schema_dir)
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


# Operational diagnosis: a genuinely separate stage and schema from astra_resolve.
# It is not bound to a reviewer REWORK verdict, never touches acceptance_criteria,
# and its output can only recommend "retry" (with guidance) or "escalate" -- never
# an implementation task, a contract change, or a completion claim. The runner
# revalidates the recommendation against the same bounded policy used to admit
# the diagnosis before any retry is authorized (see autocode_resolver_runtime).
DIAGNOSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["diagnosis", "recommendation"],
    "properties": {
        "diagnosis": goals.STRING,
        "recommendation": {
            "type": "object", "additionalProperties": False,
            "required": ["action", "rationale"],
            "properties": {
                "action": {"type": "string", "enum": ["retry", "escalate"]},
                "rationale": goals.STRING,
                "guidance": goals.STRING,
                "evidence_refs": {"type": "array", "items": goals.STRING},
            },
        },
    },
}


def diagnosis_guard(state, workspace):
    goals.execution_guard(state)
    request = state.get('diagnosis_request') or {}
    if (request.get('contract_hash') != state['goal_contract']['hash']
            or request.get('source_revision') != support.snapshot(workspace)['revision']):
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis needs the current source and approved contract')
    for path, digest in request.get('evidence_hashes', {}).items():
        if not Path(path).is_file() or support.file_hash(path) != digest:
            raise support.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis evidence changed; reconcile before diagnosing')


def prepare_diagnosis(state, stage, state_path, schema_dir):
    if stage != 'astra_diagnose':
        raise ValueError(f'Autoresolver cannot run {stage}')
    diagnosis_guard(state, Path(state['workspace']))
    state['settings']['roles'].setdefault('resolver', copy.deepcopy(state['settings']['roles']['astra']))
    # Reuse the Plan Reviewer's handoff context (current source, task, evidence
    # inventory) for input only; the output schema below is unrelated to and far
    # narrower than the reviewer decision schema that context call would imply.
    request = execution_request(state, 'astra_review', state_path, schema_dir)
    instruction, payload = request.prompt.split('CURRENT HANDOFF DATA\n', 1)
    data = json.loads(payload)
    data.update(stage=stage, diagnosis_request=state['diagnosis_request'],
                execution_engine=state['settings']['roles']['resolver'].get('engine', state['settings'].get('engine', 'codex')))
    prompt = ('You are AUTORESOLVER, a read-only failure diagnostician, not a Builder or completion owner. '
              'A stage has failed the same way repeatedly and the bounded report-repair route is already exhausted. '
              'Inspect diagnosis_request (the repeated failure identity, its evidence, and how many times it '
              'recurred) plus the current handoff data. Return a nonempty diagnosis explaining the likely cause. '
              'Recommend "retry" only when you can name a concrete, different action or guidance the next '
              'Builder attempt should follow; recommend "escalate" whenever the cause is unclear, out of scope, '
              'or needs a human decision -- never guess. You cannot approve work, change requirements, weaken '
              'tests, modify source, dispatch a task, or claim completion yourself; this recommendation is '
              'advisory only, and the runner independently validates and bounds it before any retry proceeds.\n'
              + instruction + '\nDiagnosis constraint overrides completion choices: return only diagnosis and recommendation.\n'
              + 'CURRENT HANDOFF DATA\n' + json.dumps(data, indent=2))
    metrics = {**request.metrics, 'estimated_prompt_tokens': (len(prompt) + 3) // 4}
    return ModelRequest('astra', 'resolver', prompt, metrics, DIAGNOSIS_SCHEMA, False)


def validate_diagnosis(state, value, record, workspace):
    diagnosis_guard(state, workspace)
    if record.get('changed_files') or record.get('source_revision') != state['diagnosis_request']['source_revision']:
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis must leave the reviewed source unchanged')
    if not value.get('diagnosis', '').strip():
        raise ValueError('Diagnosis requires a nonempty explanation')
    recommendation = value.get('recommendation') or {}
    if recommendation.get('action') not in ('retry', 'escalate') or not recommendation.get('rationale', '').strip():
        raise ValueError('Diagnosis recommendation requires a rationale and a retry-or-escalate action')


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
