"""The completion gate: whether a Completion Owner's COMPLETE decision may end the run.

It reads the goal contract, the findings ledger and the regression proof, so it sits above them;
autocode_support, which those modules use, no longer imports them for it. See AGENTS.md.
"""
from __future__ import annotations

from pathlib import Path

try:
    from .autocode_util import Paused, criteria_definition, file_hash
except ImportError:
    from autocode_util import Paused, criteria_definition, file_hash


def completion_ready(state, decision, current, *, require_human_reviews=True, require_independent=True):
    human_only_gap = False
    if (require_independent and state.get('settings', {}).get('milestone_checkpoints', {}).get('enabled')
            and state.get('validation', {}).get('reviewer_role') != 'sol'):
        return False
    if require_independent and state.get('settings',{}).get('workflow',{}).get('mode') == 'glm_final_audit_v2':
        review=state.get('validation',{})
        if review.get('reviewer_role')!='astra' or review.get('final_audit') is not True:
            return False
    if state.get("version", 2) >= 3:
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        try:
            goals.execution_guard(state, decision)
        except Paused:
            return False
        contract = state["goal_contract"]
        validation = state.get("validation", {})
        human_ids = [c["id"] for c in contract["body"]["acceptance_criteria"] if c["human_review"]]
        human_only_gap = (bool(human_ids)
                          and goals.human_only_pending_validation(state, validation, human_ids[0]))
        if (validation.get("contract_revision") != contract["revision"]
                or validation.get("contract_hash") != contract["hash"]
                or (state.get("current_task") and validation.get("task_id") != state["current_task"]["id"])
                or (require_human_reviews and goals.missing_human_reviews(state))
                or any(f.get("blocking", True) for f in validation.get("findings", []))
                or any(f.get("blocking", True) for f in decision.get("findings", []))):
            return False
        if "end_to_end_flow" in contract["body"]:
            flow = validation.get("end_to_end_result", {})
            if flow.get("status") != "PASS" or not flow.get("evidence_refs") or not flow.get("summary", "").strip():
                return False
    sol = state.get("validation", {})
    if decision.get("status") not in ("COMPLETE", "TASK_COMPLETE"):
        return False
    if state.get("findings_ledger"):
        try:
            from . import autocode_findings as findings_ledger
        except ImportError:
            import autocode_findings as findings_ledger
        if findings_ledger.blocking_entries(state):
            return False
    criteria = state.get("acceptance_criteria", [])
    if not criteria or criteria_definition(decision.get("acceptance_criteria", [])) != criteria_definition(criteria):
        return False
    if any(c["status"] != "verified" or not c["evidence"].strip() for c in decision["acceptance_criteria"]):
        return False
    if (sol.get("verdict") != "PASS" and not human_only_gap) or sol.get("criteria_revision") != state.get("criteria_revision"):
        return False
    if sol.get("source_revision") != current["revision"] or not sol.get("checks"):
        return False
    outcomes = sol.get("criterion_results", [])
    if sorted(r["id"] for r in outcomes) != sorted(c["id"] for c in criteria):
        return False
    if not human_only_gap and any(r["status"] != "PASS" or not r["evidence_refs"] for r in outcomes):
        return False
    if any(c["exit_code"] != 0 for c in sol["checks"]) or (sol.get("unverified_criteria") and not human_only_gap):
        return False
    if any(f["severity"] in ("critical", "high") for f in sol.get("findings", [])):
        return False
    pins = sol.get("evidence_hashes", {})
    if not pins:
        return False
    try:
        from . import autocode_regression as regression
    except ImportError:
        import autocode_regression as regression
    if not regression.complete(state, current["revision"]):
        return False  # a bug fix needs the runner's passing regression proof for this exact source
    return all(Path(p).is_file() and file_hash(p) == h for p, h in pins.items())
