"""Autoreview owns independent verification and evidence validation, the review
workflow's Reviewer stage (autocode_review_job) and the design workflow's
Architect stage (autocode_design_job)."""
import copy

try:
    from .. import autocode_design_job as design_job, autocode_goals as goals, autocode_review_job as review_job
except ImportError:
    import autocode_design_job as design_job
    import autocode_goals as goals
    import autocode_review_job as review_job
from . import autoplanner
from .common import ModelRequest, execution_request

STAGE = review_job.STAGE
JOBS = {review_job.STAGE: review_job, design_job.STAGE: design_job}


def prepare(state, stage, state_path, schema_dir):
    if stage == review_job.STAGE:
        # The Reviewer runs on the Validator's route with write access, so it can
        # make and test a scratch copy of its own; the runner rejects the report
        # if the workspace itself changed (review_job.apply).
        state["phase"] = "REVIEWING"
        return job_request(state, review_job, "sol", autoplanner.route_for(state, stage, "sol"))
    if stage == design_job.STAGE:
        # The Architect inherits the Plan Reviewer's model (the planner's own when
        # there is none) on a route of its own, so its session never leaks into
        # later planning. Same scratch-copy rule as the Reviewer.
        state["phase"] = "REVIEWING"
        roles = state["settings"]["roles"]
        roles.setdefault("architect", copy.deepcopy(roles.get("plan_reviewer") or roles["astra"]))
        return job_request(state, design_job, "astra", "architect")
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        raise ValueError(f"Autoreview cannot run {stage}")
    request = execution_request(state, stage, state_path, schema_dir)
    if stage == "sol" and state.get("current_task", {}).get("milestone_ids"):
        request.schema["properties"]["milestone_results"] = {"type": "array", "items": goals.obj({
            "milestone_id": goals.STRING, "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
            "summary": goals.STRING, "evidence_refs": goals.STRINGS})}
        request.schema["required"].append("milestone_results")
    return request


def job_request(state, job, role, route):
    prompt, metrics = job.prompt(
        state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000), autoplanner.engine_for(state["settings"], route))
    return ModelRequest(role, route, prompt, metrics, job.SCHEMA, True)


def apply_job(stage, state, value, record, workspace):
    """Autopilot hands a job stage's validated report here; the job decides how the run continues."""
    JOBS[stage].apply(state, value, record, workspace)
