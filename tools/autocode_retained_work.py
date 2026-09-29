"""Classify Builder work retained across attempts on one assignment."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_assignment as assignment
except ImportError:
    import autocode_assignment as assignment


def fresh_candidate(state, record, current_revision):
    """Return in-scope retained paths for fresh review, without claiming they pass.

    A no-diff retry may follow an interrupted or rejected Builder that left useful
    source changes. Compare the whole assignment with its earliest saved snapshot;
    an empty files dictionary is a valid baseline. The current tree and saved after
    snapshot must still describe the same source revision.
    """
    task = state.get("current_task") or {}
    owned = task.get("affected_paths") or []
    if (record.get("changed_files") or task.get("kind") != "implement"
            or not owned or not state.get("goal_contract")):
        return None
    paths = assignment.retained_changes(state.get("stages", []), record)
    if not paths or assignment.outside(owned, state.get("stages", []), record) != []:
        return None
    try:
        after = json.loads(Path(record["after_ref"]).read_text())
    except (KeyError, OSError, ValueError):
        return None
    revision = record.get("source_revision")
    if (not isinstance(after, dict) or not revision or after.get("revision") != revision
            or current_revision != revision):
        return None
    return {"source_revision": revision, "retained_paths": paths}


def validated_candidate(state, value, record, workspace, current_revision):
    """Recognize a previously validated retained source for fresh review."""
    if record.get('changed_files') or not isinstance(value.get('changed_files'), list):
        return None
    declared = set(value['changed_files'])
    affected = set((state.get('current_task') or {}).get('affected_paths') or [])
    if (not declared or not affected or not declared <= affected
            or not value.get('commands_run') or not value.get('evidence_refs')
            or any(not (Path(workspace) / path).is_file() for path in declared)
            or assignment.outside(list(affected), state.get('stages', []), record) != []):
        return None
    revision = current_revision
    criteria = {row['id'] for row in (state.get('goal_contract') or {}).get('body', {}).get('acceptance_criteria', [])}
    if not criteria or (state.get('current_task') or {}).get('source_revision') != revision:
        return None
    for archived in reversed(state.get('validation_archive', [])):
        validation = archived.get('validation') or {}
        results = {row.get('id'): row.get('status') for row in validation.get('criterion_results', [])}
        if (validation.get('verdict') == 'PASS' and validation.get('source_revision') == revision
                and criteria <= {cid for cid, status in results.items() if status == 'PASS'}):
            return {'source_revision': revision, 'validation_output': validation.get('output'),
                    'criteria': sorted(criteria), 'declared_paths': sorted(declared)}
    return None
