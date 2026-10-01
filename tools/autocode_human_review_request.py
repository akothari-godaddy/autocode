"""Publish the existing artifact-review question without inventing machine acceptance."""


def queue(state, criteria, current, record, stage, wait_for_user):
    wait_for_user(state,
                  {"kind": "human_review", "criteria": criteria,
                   "decision_needed": "Review the current artifact and explicitly approve the listed criteria",
                   "impact": "Completion requires the declared human acceptance of this validated artifact",
                   "options": [], "discovered": "Independent evidence passed; human review remains",
                   "proposed_delta": ""},
                  origin={"stage": stage, "output": record["output"], "source_revision": current["revision"]},
                  next_stage="astra_review")
