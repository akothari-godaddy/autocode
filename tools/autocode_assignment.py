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


def _same_attempt(row, attempt):
    """One provider attempt can appear in several saved rows (a checkpoint copy, its
    archived row, a repaired report's original); they share the launch time."""
    return row is attempt or (bool(attempt.get("started_at")) and all(
        row.get(key) == attempt.get(key) for key in ("started_at", "stage", "iteration")))


def _snapshot(stages, attempt, key):
    """``attempt``'s saved snapshot, from whichever copy of its row still points at it.

    Archiving a rejected attempt moves its files and rewrites only the row it archives.
    """
    for row in (attempt, *stages):
        if _same_attempt(row, attempt):
            files = _files(row.get(key))
            if files is not None:
                return files
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
    before, after = _snapshot(stages, first, "before_ref"), _snapshot(stages, record, "after_ref")
    if before is None or after is None:
        # With no earlier attempt, this attempt's runner-measured delta is the whole delta.
        return sorted(record.get("changed_files") or []) if _same_attempt(first, record) else None
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def outside(owned, stages, record):
    """Paths changed outside ``owned`` since the assignment began; None when unprovable."""
    changed = retained_changes(stages, record)
    if changed is None:
        return None
    return [name for name in changed if not any(contains(root, name) for root in owned)]
