"""The bug is fixed (hidden tests), and, in a run, the real Investigator unstuck the Planner:
exactly one investigation of the stuck astra_discovery, a retry recommendation whose diagnosis
names the rejected citation, a real OpenAI model, and the failure history left intact."""
import json

from harness.oracle import Check, load_json, mentions, python_change_checks, run_checks

DIAGNOSIS_FIELDS = ("observed", "reproduction", "root_cause", "affected_paths", "invariant")
NOTE = "docs/bugs/word-count.json"


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="tally")
    note, error = load_json(project / NOTE)
    checks.append(Check("diagnosis_present_and_valid", isinstance(note, dict), error))
    if isinstance(note, dict):
        missing = [field for field in DIAGNOSIS_FIELDS if not note.get(field)]
        checks.append(Check("diagnosis_has_required_fields", not missing, f"missing: {missing}"))
    checks += run_checks(run, workflow="bugfix", no_requirements=True)
    if run is not None:
        checks += investigation_checks(project)
    return checks


def investigation_checks(project):
    states = sorted((project / ".autocode" / "runs").glob("*/state.json"))
    state = json.loads(states[-1].read_text()) if states else {}
    history = state.get("stuck_investigations") or []
    planner = [row for row in history if row.get("stage") == "astra_discovery"]
    checks = [Check("planner_was_investigated_once", len(planner) == 1,
                    f"investigations: {[(row.get('identity'), row.get('outcome')) for row in history]}")]
    row = planner[0] if planner else {}
    text = f"{row.get('diagnosis', '')} {row.get('guidance', '')}"
    checks.append(Check("investigator_recommended_a_retry", row.get("outcome") == "retried",
                        f"outcome {row.get('outcome')!r}: {row.get('diagnosis', '')[:200]}"))
    checks.append(Check("diagnosis_names_the_rejected_citation",
                        mentions(text, ("code_ref", "code ref", NOTE, "citation", "cite")), text[:300]))
    model = row.get("model") or ""
    checks.append(Check("investigator_was_a_real_openai_model", model.startswith("openai/gpt-6"), model))
    kept = [entry for entry in (state.get("failure_history") or {}).values()
            if (entry.get("identity") or {}).get("stage") == "astra_discovery"]
    checks.append(Check("failure_history_kept", bool(kept) and max(e.get("count", 0) for e in kept) >= 1,
                        str([e.get("count") for e in kept])))
    return checks
