"""Protect agreed behavior while permitting proof repairs in unapproved drafts.

Pure functions over contract bodies and user events. No runner imports or writes.
"""
from __future__ import annotations

try:
    from . import autocode_protected_text as protected, autocode_test_cases as test_cases
except ImportError:
    import autocode_protected_text as protected, autocode_test_cases as test_cases

PLANNER_ORIGINS = {"glm_draft", "glm_revise", "astra_finalize", "astra_discovery"}
PROTECTED_LISTS = ("required_behaviors", "scope_exclusions", "constraints", "important_failure_cases")


def draft_proof_corrections(state: dict, before: dict, after: dict) -> set[str]:
    """A draft with no approval receipt may change how it proves identical behavior.

    A method correction cannot remove a criterion, alter its literal behavior or
    human-review requirement, or change a current/invalidated approved contract.
    Other protected items and permissions still go through the ordinary guard.
    """
    contract = state.get("goal_contract") or {}
    if (contract.get("approval_status") != "draft" or contract.get("approval_event")
            or any(event.get("kind") == "goal_approval" for event in state.get("user_events", []))):
        return set()
    old = {row["id"]: row for row in before.get("acceptance_criteria", [])}
    new = {row["id"]: row for row in after.get("acceptance_criteria", [])}
    return {cid for cid in old.keys() & new.keys()
            if old[cid]["criterion"] == new[cid]["criterion"]
            and old[cid].get("human_review") == new[cid].get("human_review")
            and str(new[cid].get("verification_method") or "").strip()
            and old[cid]["verification_method"] != new[cid]["verification_method"]}


def saved_user_basis(state, basis, answer_id):
    if basis == "user_answer":
        return bool(answer_id) and answer_id in state.get("answers", {})
    if basis == "user_feedback":
        return bool(answer_id) and any(event.get("id") == answer_id and event in state.get("user_events", [])
                                       for event in state.get("brief_feedback", []))
    return False


def revision_guard(state, body, changes, origin):
    """A planner revision may not drop protected text or widen permissions on its own."""
    previous_contract = state.get("goal_contract") or {}
    previous = previous_contract.get("body")
    if origin not in PLANNER_ORIGINS or not previous:
        return
    # A new draft may replace an unapproved one. Revising the current draft, or
    # replacing an approved contract, cannot drop protected text on its own.
    if origin in ("glm_draft", "astra_discovery") and previous_contract.get("approval_status") != "approved":
        return
    if not isinstance(changes, list):
        raise ValueError("Planner revision needs contract_changes")
    protected.restore_spelling(previous, body, {raw.get("item") for raw in changes if isinstance(raw, dict)}, PROTECTED_LISTS)
    proof_corrections = draft_proof_corrections(state, previous, body)
    protected_changes = []
    for raw in changes:
        if not isinstance(raw, dict) or raw.get("change") not in ("removed", "reworded", "permission_changed"):
            raise ValueError("contract_changes entries need item, change, basis and answer_id")
        if (raw.get("item") in proof_corrections and raw["change"] == "reworded"
                and raw.get("basis") == "agent_proposed" and not raw.get("answer_id")):
            continue  # Older reports declared this engineering correction as a contract delta.
        protected_changes.append(raw)
        basis = raw.get("basis")
        if not saved_user_basis(state, basis, raw.get("answer_id")):
            raise ValueError("Changing a protected contract item needs a saved user answer or feedback event")
    declared = {}
    for raw in protected_changes:
        declared.setdefault(raw["item"], []).append(raw)

    def consume(item, kind):
        rows = declared.get(item, [])
        match = next((row for row in rows if row["change"] == kind), None)
        if match is None:
            raise ValueError(f"Planner revision drops or changes {item!r} without a user-backed contract change")
        rows.remove(match)
        if kind == "reworded":
            replacement = str(match.get("replacement", "")).strip()
            if not replacement:
                raise ValueError(f"Rewording {item!r} needs the replacement text")
            return "user", replacement
        return "user", None

    for key in PROTECTED_LISTS:
        for item in previous.get(key, []):
            if item in body.get(key, []):
                continue
            _, replacement = consume(item, "reworded" if any(row["change"] == "reworded" for row in declared.get(item, [])) else "removed")
            if replacement and replacement not in body.get(key, []):
                raise ValueError(f"Rewording {item!r} must appear in {key}")
    old_criteria = {row["id"]: (row["criterion"], test_cases.proof(row["verification_method"])) for row in previous.get("acceptance_criteria", [])}
    new_criteria = {row["id"]: (row["criterion"], test_cases.proof(row["verification_method"])) for row in body.get("acceptance_criteria", [])}
    for cid, text in old_criteria.items():
        if new_criteria.get(cid) == text or (cid in proof_corrections and not declared.get(cid)):
            continue
        consume(cid, "removed" if cid not in new_criteria else "reworded")
    previous_permissions = previous.get("permission_boundaries", [])
    if previous_permissions and set(previous_permissions) != set(body.get("permission_boundaries", [])):
        changed = set(previous.get("permission_boundaries", [])) ^ set(body.get("permission_boundaries", []))
        for item in changed:
            consume(item, "permission_changed")
    if any(rows for rows in declared.values()):
        raise ValueError("contract_changes contains an item that was not changed in the protected contract")
