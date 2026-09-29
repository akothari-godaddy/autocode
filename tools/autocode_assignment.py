"""A serial Builder's assignment boundary, measured over the whole assignment.

Checking only the latest attempt's own delta lets a retry launder what an earlier
attempt left behind: attempt one edits a file outside the assignment and is
rejected, the edit stays in the tree for inspection, and attempt two (with no new
edits, or with only in-scope ones) would pass. So the evidence is the retained
worktree against the assignment's starting snapshot, which is the before-snapshot
of the task's earliest Builder attempt, saved as that stage record's before_ref.

Pure over saved snapshot files and stage records. It imports nothing from the
runner (AGENTS.md rule 2); callers pass the stage history.
"""
from __future__ import annotations

import json
from pathlib import Path

BUILDER = "terra"


def contains(root, path):
    return path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/")


def _files(ref):
    try:
        return json.loads(Path(ref).read_text())["files"]
    except (KeyError, TypeError, OSError, ValueError):
        return None


def starting_attempt(stages, record):
    """The task's earliest serial Builder attempt; ``record`` itself when it is the first."""
    task = record.get("task_id")
    return next((row for row in stages if task and row.get("task_id") == task
                 and (row.get("original_stage") or row.get("stage")) == BUILDER
                 and not row.get("runner_owned") and not row.get("batch_id")), record)


def retained_changes(stages, record):
    """Every path changed or deleted since the assignment began, as ``record`` left the tree.

    None when an earlier attempt exists but the snapshots needed to compare against
    it cannot be read: that is missing evidence, never an empty delta.
    """
    first = starting_attempt(stages, record)
    before, after = _files(first.get("before_ref")), _files(record.get("after_ref"))
    if before is None or after is None:
        # With no earlier attempt, this attempt's runner-measured delta is the whole delta.
        return sorted(record.get("changed_files") or []) if first is record else None
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def outside(owned, stages, record):
    """Paths changed outside ``owned`` since the assignment began; None when unprovable."""
    changed = retained_changes(stages, record)
    if changed is None:
        return None
    return [name for name in changed if not any(contains(root, name) for root in owned)]
