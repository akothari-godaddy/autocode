"""Choose replay policy without changing a Validator's reported verdict."""
try:
    from . import autocode_check_replay as check_replay
    from . import autocode_progressive_state as progressive
    from . import autocode_human_review_policy as human_review
except ImportError:
    import autocode_check_replay as check_replay
    import autocode_progressive_state as progressive
    import autocode_human_review_policy as human_review


def replay(state, value, workspace, run_dir, record, scratch_run):
    progressive_run = progressive.enabled(state)
    claimed_pass = any(row["status"] == "PASS" for row in value.get("criterion_results", []))
    human_ids = [row["id"] for row in (state.get("goal_contract") or {}).get("body", {}).get("acceptance_criteria", [])
                 if row.get("human_review")]
    human_pending = (state.get("version", 2) >= 3 and bool(human_ids)
                     and human_review.human_only_pending_validation(state, value, human_ids[0]))
    if value["verdict"] != "PASS" and not (human_pending or (progressive_run and claimed_pass)):
        return None
    return check_replay.replay(
        value["checks"], workspace, run_dir, record, scratch_run, approved_state=state,
        progressive_context=progressive.context(state),
        allow_reported_failures=progressive_run and value["verdict"] == "FAIL")
