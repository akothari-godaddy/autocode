"""Autoreview owns independent verification and evidence validation, and the
review workflow's Reviewer stage (autocode_review_job)."""
try:
    from .. import autocode_goals as goals, autocode_review_job as review_job
except ImportError:
    import autocode_goals as goals
    import autocode_review_job as review_job
from . import autoplanner
from .common import ModelRequest, execution_request

STAGE = review_job.STAGE


def prepare(state, stage, state_path, schema_dir):
    if stage == STAGE:
        # The Reviewer runs on the Validator's route with write access, so it can
        # make and test a scratch copy of its own; the runner rejects the report
        # if the workspace itself changed (review_job.apply).
        state["phase"] = "REVIEWING"
        route = autoplanner.route_for(state, stage, "sol")
        prompt, metrics = review_job.prompt(
            state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
            state["settings"].get("context_soft_tokens", 10000),
            autoplanner.engine_for(state["settings"], route))
        return ModelRequest("sol", route, prompt, metrics, review_job.SCHEMA, True)
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        raise ValueError(f"Autoreview cannot run {stage}")
    request = execution_request(state, stage, state_path, schema_dir)
    if stage == "sol" and state.get("current_task", {}).get("milestone_ids"):
        request.schema["properties"]["milestone_results"] = {"type": "array", "items": goals.obj({
            "milestone_id": goals.STRING, "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
            "summary": goals.STRING, "evidence_refs": goals.STRINGS})}
        request.schema["required"].append("milestone_results")
    return request


def apply(state, value, record, workspace):
    """Autopilot hands the Reviewer's validated report here; the review completes the run."""
    review_job.apply(state, value, record, workspace)
