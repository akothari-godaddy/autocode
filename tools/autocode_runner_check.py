"""Durable activity for checks the runner executes without a model.

Only this module writes ``active_runner_check``. Status, the task-run view and
the dashboard read it separately from provider attempts: a test suite must not
look like either an idle Validator or an interrupted model call. The caller
owns the run lock and supplies its normal state persistence function.
"""
from contextlib import contextmanager
import os
from pathlib import Path

try:
    from . import autocode_process as processes, autocode_util as util
except ImportError:
    import autocode_process as processes
    import autocode_util as util


def clear(state, run_dir, persist):
    """Clear an earlier check at a controller's next dispatch, under its run lock."""
    if state.pop("active_runner_check", None) is not None:
        persist(Path(run_dir) / "state.json", state)


@contextmanager
def track(state, run_dir, stage, summary, persist):
    pid = os.getpid()
    owner = processes.identity(processes.process_table({pid})[pid])
    record = {"stage": stage, "summary": summary, "started_at": util.now(),
              "processes": [owner]}
    state["active_runner_check"] = record

    def update(summary, *, command=None, output=None):
        record.update(summary=summary, updated_at=util.now(), command=command,
                      output=str(output) if output else None)
        persist(Path(run_dir) / "state.json", state)

    try:
        update(summary)
        yield update
    finally:
        clear(state, run_dir, persist)
