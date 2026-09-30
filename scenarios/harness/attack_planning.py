"""Script a plan that forgets unittest discovery's required package marker."""
from pathlib import Path


def install(fake, config, trace):
    original = fake.report_for
    repaired = Path(config["root"]) / "planner-scaffolding-fixed"

    def report(stage, data):
        if config["case"] == "remember_citation_after_clarification":
            return citation_after_clarification(fake, original, stage, data, trace)
        if config["case"] in ("repair_draft_suite_proof", "reject_draft_proof_downgrade"):
            return draft_suite_proof(fake, original, stage, data, trace, config)
        error = str(data.get("error") or "")
        if config["case"] == "repair_scaffolding" and "requires tests/__init__.py" in error:
            repaired.write_text("The Planner adds the required marker before approval.\n")
            trace("scaffolding_repaired", stage=stage, error=error)
        if repaired.exists() and "tests/__init__.py" not in fake.PATHS:
            fake.PATHS.append("tests/__init__.py")
        value = original(stage, data)
        if stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            trace("scaffolding_plan", stage=stage, assigned=list(fake.PATHS), repair=bool(data.get("report_repair")))
        return value

    fake.report_for = report


def citation_after_clarification(fake, original, stage, data, trace):
    """A deterministic analogue of the inventory run: repair, clarify, then replan."""
    if stage == "investigate_stuck":
        attempt = next(row["output"] for row in reversed(data["recent_stages"])
                       if row.get("stage") == "astra_discovery" and row.get("rejected"))
        copied = "run/" + Path(attempt).name
        return {"diagnosis": "The Planner cites .autocode/state.json, a runner-owned file, instead of source.",
                "cause": "stage_output", "guidance": "Cite README.md as durable source, never .autocode/state.json.",
                "recommendation": "retry", "user_question": "", "evidence_refs": [attempt, "README.md"],
                "example": "Given code_refs contains .autocode/state.json; when source citations are checked, "
                           "the runner rejects it because it is runner-owned.",
                "probe": "python3 -c \"import json; from pathlib import Path; "
                         f"assert json.loads(Path('{copied}').read_text())['code_refs'] == ['.autocode/state.json']; "
                         "assert Path('README.md').is_file()\"",
                "untestable": ""}
    value = original(stage, data)
    if stage != "astra_discovery":
        return value
    guided = ("INVESTIGATOR GUIDANCE" in fake.PROMPT
              or "EARLIER PLANNING CORRECTIONS" in fake.PROMPT)
    answered = "Q_AFTER" in data.get("saved_answers", {})
    trace("citation_cycle", guided=guided, answered=answered, repair=bool(data.get("report_repair")))
    value["code_refs"] = ["README.md"] if guided else [".autocode/state.json"]
    if guided and not answered:
        value["contract"].update(technical_approach=[], milestones=[], open_blocking_questions=[{
            "id": "Q_AFTER", "question": "Which greeting should the first example use?",
            "why": "The fixture holds one user decision open after the citation repair.",
            "options": ["Ada", "Grace"], "proposed_default": "Ada", "kind": "decision",
            "category": "behavior", "delegable": True}])
    return value


def draft_suite_proof(fake, original, stage, data, trace, config):
    value = original(stage, data)
    if stage == "astra_discovery":
        value["contract"]["acceptance_criteria"][0]["verification_method"] = ("test: test_c1_recursive_suite"
            if config["case"] == "reject_draft_proof_downgrade" else "python3 -m unittest nonexistent_test.py")
    elif stage == "astra_challenge":
        value["concerns"] = [{"id": "C_SUITE", "concern": "The draft verification command is wrong.",
                              "evidence_refs": ["README.md"], "requested_change": "Use the ordinary suite command.",
                              "acceptance_test": fake.CHECK, "blocking": True}]
    elif stage == "glm_revise":
        value["responses"] = [{"concern_id": "C_SUITE", "response": "Correct only the draft proof method.",
                               "evidence_refs": ["README.md"], "change": "Run the existing suite command directly.",
                               "acceptance_test": fake.CHECK}]
    elif stage == "astra_finalize":
        value["decisions"] = [{"concern_id": "C_SUITE", "decision": "Use the ordinary suite command.",
                               "rationale": "The required behavior has not changed.",
                               "acceptance_test": fake.CHECK, "resolved": True}]
    if stage in ("astra_discovery", "glm_revise", "astra_finalize"):
        trace("draft_proof", stage=stage, method=value["contract"]["acceptance_criteria"][0]["verification_method"])
    return value
