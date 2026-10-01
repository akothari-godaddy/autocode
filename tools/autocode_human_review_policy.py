"""Cycle-free evidence and authenticated acceptance rules for human review.

The contract module reexports these public predicates. This module does not
write state or turn technical proof into human acceptance.
"""
from __future__ import annotations

try:
    from .autocode_contract_identity import approved, token
    from . import autocode_util as util
except ImportError:
    from autocode_contract_identity import approved, token
    import autocode_util as util


def review_token(state):
    val = state.get("validation", {})
    if not approved(state) or not val.get("source_revision"):
        return None
    return token(state["goal_contract"]) + "@" + val["source_revision"] + ":" + util.digest(val)


def missing_human_reviews(state):
    current = review_token(state)
    return [c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"]
            if c["human_review"] and not review_binding_valid(state, c["id"], current)]


def legacy_review_acceptance(state, criterion, answer_id):
    """Return an authenticated older answer that explicitly accepted a review criterion."""
    answer = state.get("answers", {}).get(answer_id)
    if not isinstance(answer, dict) or answer not in state.get("user_events", []):
        return None
    question = answer.get("question") or {}
    if not isinstance(question, dict):
        return None
    options = question.get("options") or []
    if (answer.get("kind") != "permission_answer" or answer.get("actor") != "user_cli"
            or answer.get("question_id") != answer_id or question.get("id") != answer_id
            or answer.get("contract_token") != token(state["goal_contract"])
            or not isinstance(answer.get("at"), str)
            or not isinstance(options, list) or len(options) != 2
            or not isinstance(options[0], str) or not options[0].startswith(f"Accept {criterion}:")
            or not isinstance(options[1], str) or not options[1].startswith(f"Reject {criterion}:")
            or not isinstance(answer.get("text"), str)
            or not answer["text"].startswith(f"Accept {criterion}.")):
        return None
    return answer


def preserved_review_answers(state, criterion, original):
    """Find later authenticated instructions carrying the old acceptance forward."""
    result = {}
    for answer_id, answer in state.get("answers", {}).items():
        if (not isinstance(answer, dict) or answer not in state.get("user_events", [])
                or answer.get("kind") != "permission_answer" or answer.get("actor") != "user_cli"
                or answer.get("contract_token") != token(state["goal_contract"])
                or not isinstance(answer.get("at"), str) or answer["at"] <= original["at"]):
            continue
        response = answer.get("text")
        if not isinstance(response, str):
            continue
        if f"existing {criterion} acceptance" in response and "do not request another human visual approval" in response.lower():
            result[answer_id] = answer
    return result


def review_binding_valid(state, criterion, current):
    if not current:
        return False
    binding = state.get("human_reviews", {}).get(criterion)
    if (not isinstance(binding, dict) or binding.get("token") != current
            or binding.get("criterion") != criterion or binding not in state.get("user_events", [])):
        return False
    if binding.get("kind") == "human_review":
        return binding.get("actor") == "user_cli"
    if binding.get("kind") != "review_reconciliation" or binding.get("actor") != "runner":
        return False
    original = legacy_review_acceptance(state, criterion, binding.get("answer_id"))
    if not original or binding.get("answer_hash") != util.digest(original):
        return False
    preserved = preserved_review_answers(state, criterion, original)
    receipts = binding.get("preservation_hashes") or {}
    return bool(receipts) and all(
        answer_id in preserved and util.digest(preserved[answer_id]) == digest
        for answer_id, digest in receipts.items())


def human_only_pending_validation(state, validation, criterion):
    """A complete technical review whose only missing results are human acceptance.

    Any number of human-review criteria may be pending together, provided
    every technical criterion passes with evidence and the pending set is
    exactly the human set (a single pending criterion remains the common case).
    """
    criteria = state["goal_contract"]["body"]["acceptance_criteria"]
    human = {row["id"] for row in criteria if row["human_review"]}
    rows = validation.get("criterion_results", [])
    results = {row["id"]: row for row in rows}
    pending_ids = {entry.split(":", 1)[0].split(" ", 1)[0]
                   for entry in validation.get("unverified_criteria", [])}
    if (criterion not in human or not human
            or set(results) != {row["id"] for row in criteria} or len(rows) != len(criteria)
            or validation.get("verdict") != "BLOCKED" or not pending_ids or pending_ids != human
            or validation.get("findings") or validation.get("end_to_end_result", {}).get("status") != "PASS"):
        return False
    return all(row.get("evidence_refs") and
               row.get("status") == ("NOT_VERIFIED" if cid in human else "PASS")
               for cid, row in results.items())
