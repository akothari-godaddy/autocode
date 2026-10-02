"""Decode references to saved requirements without rewriting their provenance."""
import copy


def generation_schema(schema, state):
    """Allow an existing requirement ID alone; new requirements still need full rows."""
    saved = ((state or {}).get("requirements_handoff") or {}).get("report") or {}
    ids = [row["id"] for row in saved.get("requirements", [])]
    if not ids or "requirements" not in schema.get("properties", {}):
        return schema
    result = copy.deepcopy(schema)
    item = result["properties"]["requirements"]["items"]
    item["required"] = ["id"]
    item["description"] = (
        "For unchanged saved requirements emit only {id: saved_id}; the runner copies the "
        "entire saved row verbatim. Saved IDs: " + ", ".join(ids) + ". New IDs require id, "
        "text and source_quote. If supplying any text or source_quote, supply both; explicit "
        "rows remain subject to source and user-backed omission validation."
    )
    return result


def validation_schema(schema, state, value):
    """Reject unknown references and incomplete explicit rows before hydration."""
    saved = ((state or {}).get("requirements_handoff") or {}).get("report") or {}
    known = {row["id"] for row in saved.get("requirements", [])}
    if not known or "requirements" not in schema.get("properties", {}):
        return schema
    rows = value.get("requirements")
    if not isinstance(rows, list):
        raise ValueError("requirements must be an array")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("Each requirement needs a nonempty string ID")
        if row["id"] in seen:
            raise ValueError(f"Duplicate requirement id {row['id']}")
        seen.add(row["id"])
        if set(row) == {"id"}:
            if row["id"] not in known:
                raise ValueError(f"Unknown saved requirement reference {row['id']}")
        elif not {"id", "text", "source_quote"} <= row.keys():
            raise ValueError("Explicit requirements need id, text and source_quote; use only id for saved references")
    return generation_schema(schema, state)


def hydrate_report(value, state):
    """Copy authoritative rows only for ID-only references, retaining explicit rows."""
    saved = ((state or {}).get("requirements_handoff") or {}).get("report") or {}
    known = {row["id"]: row for row in saved.get("requirements", [])}
    if not known:
        return value
    result = copy.deepcopy(value)
    result["requirements"] = [copy.deepcopy(known[row["id"]]) if set(row) == {"id"} else row
                              for row in result.get("requirements", [])]
    return result
