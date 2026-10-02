"""An explicit, exact Validator report retry after bounded report repairs fail."""
from __future__ import annotations

import copy
from pathlib import Path

try:
    from . import autocode_util as util, autocode_failures as failures
except ImportError:
    import autocode_util as util
    import autocode_failures as failures


ERRORS = frozenset({
    'OpenCode final message is not a JSON report; inspect the saved raw events',
    'Check is not supported by an exact executed Validator event',
})


def inspected_failure(state: dict) -> dict | None:
    """Find a stalled evidence failure after the repair path already retained its history."""
    if (state.get('status') != 'PAUSED_RESOLVER' or state.get('next_stage') != 'sol'
            or any(state.get(key) for key in ('active_stage', 'uncertain_artifacts', 'pending_report_repair'))):
        return None
    record = next((row for row in reversed(state.get('stages', []))
                   if (row.get('original_stage') or row.get('stage')) == 'sol'
                   and row.get('failure_key')), None)
    if not record or not record.get('rejected'):
        return None
    entry = (state.get('failure_history') or {}).get(record['failure_key']) or {}
    identity = entry.get('identity') or {}
    if (not failures.stalled(entry) or entry.get('last_error') not in ERRORS
            or failures.key(identity) != record['failure_key'] or identity.get('stage') != 'sol'
            or identity.get('artifact_hash') != record.get('source_revision')
            or record.get('failure_attempt') not in entry.get('attempts', [])):
        return None
    return record


def offered_attempt(state: dict) -> str | None:
    """Project a stopped report identity; the retry authenticates its saved artifacts."""
    pending = state.get('pending_report_repair') or {}
    original = pending.get('original') or {}
    rejected = pending.get('latest_rejected') or {}
    limit = (state.get('settings') or {}).get('report_repair', {}).get('max_attempts', 2)
    if (state.get('status') not in ('PAUSED_REPEATED_FAILURE', 'PAUSED_RESOLVER')
            or state.get('active_stage') or state.get('uncertain_artifacts')
            or state.get('next_stage') != 'sol' or original.get('stage') != 'sol'
            or pending.get('error') not in ERRORS or not limit or pending.get('attempts') != limit
            or not rejected.get('report_only') or not rejected.get('rejected')
            or rejected.get('original_stage') != 'sol'
            or type(rejected.get('iteration')) is not int or not rejected.get('output')):
        return None
    return f"{rejected['iteration']:03d}/{Path(rejected['output']).stem}"


def retry(state, run_dir, workspace, selected, *, prepare_retry):
    """Authenticate exact evidence and archive exhausted repair history before one retry."""
    if selected != offered_attempt(state) or selected is None:
        raise ValueError('--retry-report must match the exhausted rejected report-only attempt')
    pending = state['pending_report_repair']
    original = pending['original']
    repair = next((row for row in reversed(state.get('stages', []))
                   if row.get('report_only') and row.get('rejected')
                   and row.get('original_stage') == original.get('stage')), None)
    if (not repair or type(repair.get('iteration')) is not int or not repair.get('output')
            or selected != f"{repair['iteration']:03d}/{Path(repair['output']).stem}"
            or repair.get('source_revision') != original.get('source_revision')
            or not repair.get('schema') or not original.get('schema')
            or not Path(repair['schema']).is_file() or not Path(original['schema']).is_file()
            or util.file_hash(repair['schema']) != util.file_hash(original['schema'])
            or util.snapshot(workspace)['revision'] != original.get('source_revision')
            or (state.get('goal_contract') or {}).get('hash') != pending.get('contract_hash')
            or any(not Path(p).is_file() or util.file_hash(p) != h
                   for p, h in pending.get('pins', {}).items())):
        raise ValueError('Saved report inputs changed; reconcile them before retrying')
    candidate = copy.deepcopy(state)
    # Only this explicit exact-report action may normalize the resolver's pause.
    # Plain Resume retains its existing hold, limits and approval requirements.
    candidate['status'] = 'PAUSED_REPEATED_FAILURE'
    if not prepare_retry(candidate, run_dir, workspace, allow_repeated=True):
        raise ValueError('Saved stage cannot be retried as a fresh execution report')
    candidate.setdefault('user_events', []).append({
        'kind': 'report_retry_after_format_fix', 'actor': 'user_cli', 'at': util.now(),
        'attempt_id': selected, 'source_revision': original['source_revision']})
    state.clear()
    state.update(candidate)
    util.atomic_json(Path(run_dir) / 'state.json', state)
