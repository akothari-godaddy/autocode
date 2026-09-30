"""Constrain review report generation to runner-owned identities and approved literals.

These constraints help a provider copy the saved contract exactly. Runtime guards
still require the complete ordered criteria and independently verified evidence.
"""
import copy


def review_generation_schema(schema, state, stage):
    """Constrain runner-owned identity at generation, not by accepting bad reports."""
    result = copy.deepcopy(schema)
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        return result
    props = result.get("properties", {})
    contract = state.get("goal_contract") or {}
    for field, value in (("contract_hash", contract.get("hash")),
                         ("contract_revision", contract.get("revision")),
                         ("task_id", (state.get("current_task") or {}).get("id", ""))):
        if field in props and value is not None:
            props[field] = {**props[field], "enum": [value]}
    source = "sol" if stage == "sol" else "astra"
    own = [r["id"] for r in state.get("findings_ledger", [])
           if r.get("source") == source and r.get("status") == "open"]
    for field in ("findings", "finding_dispositions"):
        fields = props.get(field, {}).get("items", {}).get("properties", {})
        if "id" in fields:
            fields["id"] = {**fields["id"], "enum": ["", *own]}
    criteria = state.get("acceptance_criteria") or []
    fields = props.get("acceptance_criteria", {}).get("items", {}).get("properties", {})
    if criteria and fields:
        for key in ("id", "criterion"):
            if key in fields:
                fields[key] = {**fields[key], "enum": [row[key] for row in criteria]}
    return result


