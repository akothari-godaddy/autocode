"""Read-only accepted-stage provenance shared by approval and budget recovery."""
from __future__ import annotations

import json
from pathlib import Path


def session(state, accepted, stage):
    """Return the original session, or the accepted output of a sessionless stage.

    The caller authenticates the current contract, artifact pair and source.
    Repair history binds the replacement to the archived original; the repair
    transport's session never substitutes for that original evidence.
    """
    if (not isinstance(accepted, dict) or type(accepted.get("exit_code")) is not int
            or accepted["exit_code"] != 0 or accepted.get("rejected")
            or accepted.get("abandoned") or accepted.get("changed_files")):
        raise ValueError("stage lacks accepted read-only evidence")
    original = accepted
    if accepted.get("report_only"):
        if accepted.get("stage") != stage + "_report_repair" or accepted.get("original_stage") != stage:
            raise ValueError("report repair does not identify the original stage")
        receipt = next((row for row in state.get("report_repair_history", [])
            if row.get("result") == "accepted" and row.get("repair") == accepted), None)
        original = next((row for row in state.get("stages", []) if receipt
            and row.get("output") == receipt.get("original_output")
            and row.get("events") == accepted.get("applied_original_events")), None)
    if (not original or original.get("stage") != stage
            or original.get("role") != accepted.get("role")
            or type(original.get("exit_code")) is not int or original["exit_code"] != 0
            or original.get("abandoned") or original.get("changed_files")
            or original.get("source_revision") != accepted.get("source_revision")):
        raise ValueError("stage lacks matching accepted original provenance")

    saved = original.get("thread_id")
    if saved or accepted.get("report_only"):
        observed = set()
        for line in Path(original["events"]).read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started":
                identity = event.get("thread_id")
            elif event.get("type") in ("step_start", "step_finish", "tool_use", "text", "error"):
                identity = event.get("sessionID")
            else:
                continue
            if identity is not None:
                if type(identity) is not str or not identity:
                    raise ValueError("original event session identity is malformed")
                observed.add(identity)
        if ((saved and (type(saved) is not str or observed != {saved}
                or (original.get("expected_session") and original["expected_session"] != saved)))
                or (not saved and (original.get("supports_sessions") is not False or observed))):
            raise ValueError("stage lacks authenticated original session evidence")
    elif original.get("supports_sessions") is True:
        raise ValueError("stage is missing its original session")
    output = accepted.get("output")
    if type(output) is not str or not output:
        raise ValueError("accepted stage lacks its output identity")
    return saved or output
