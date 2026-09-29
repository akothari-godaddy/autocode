"""The review must raise the three planted gaps, raise nothing else as blocking,
put the ordering decision to the user, and leave the repository untouched."""
from harness.oracle import Check, load_json, mentions, only_changed_under, run_checks

PLANTED = {
    "ordering": ((" order", "ordering", "sequence", "seq"), ("kind", "partition", "key")),
    "idempotency": (("idempot", "duplicate", "dedup", "twice", "at-least-once", "at least once", "double"),),
    "migration": (("rollback", "roll back", "revert", "reconcil", "dual-write", "dual write", "cut over", "cutover"),),
}


def areas_of(concern):
    """Every planted area a concern covers. One concern can cover two: a migration gap that causes
    double charges is both migration and idempotency, and counts for each."""
    return {area for area, groups in PLANTED.items() if mentions(concern, *groups)}


def check(project, scenario, run=None):
    report, error = load_json(project / "review" / "design-review.json")
    checks = [Check("design_review_json_present_and_valid", isinstance(report, dict), error)]
    if isinstance(report, dict):
        concerns = [c for c in report.get("concerns") or [] if isinstance(c, dict)]
        blocking = [c for c in concerns if c.get("severity") == "blocking"]
        found = set().union(*(areas_of(c) for c in blocking))
        for area in PLANTED:
            checks.append(Check(f"raises_{area}_as_blocking", area in found))
        false = [c.get("id") or c.get("summary") for c in blocking if not areas_of(c)]
        checks.append(Check("no_invented_blockers", not false, f"blocking but already answered by the design: {false}"))
        questions = [q for q in report.get("questions") or [] if isinstance(q, dict)]
        checks.append(Check("asks_about_ordering_requirement",
                            any(mentions(q.get("question", ""), ("order", "sequence")) for q in questions)))
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="design", no_build=True)
    return checks
