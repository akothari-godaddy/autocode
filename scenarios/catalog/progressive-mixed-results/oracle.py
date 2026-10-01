import sys

from harness.oracle import Check, run as command, run_checks, scratch_copy, tail


def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as copy:
        (copy / "test_journey.py").write_text((scenario.seed / "test_journey.py").read_text())
        for suite in ("test_journey.Skeleton", "test_journey.Recommendation", "test_journey"):
            result = command([sys.executable, "-m", "unittest", suite], copy)
            checks.append(Check(suite, result.returncode == 0, tail(result)))
    checks += run_checks(run, workflow="build", plan_approved=True)
    if run is not None:
        view = run["view"]
        progressive = view.get("progressive") or {}
        stages = run.get("stages", [])
        checks.append(Check("verified_slice_history", [row["slice_id"] for row in
                            progressive.get("demonstrated_slices", [])] == ["S1", "S2"], str(progressive)))
        checks.append(Check("current_cumulative_product_proof", progressive.get("current_whole_product_proof", {})
                            .get("verified") is True, str(progressive)))
        checks.append(Check("ordinary_mixed_failure_repair", "astra_resolve" in stages and
                            stages.count("terra") >= 3 and stages.count("sol") >= 3, str(stages)))
        checks.append(Check("no_manual_failure_recovery", "resume-paused" not in run.get("cli_calls", [])))
    return checks
