"""Correct fix (hidden tests) delivered with a process proportionate to a
three-line bug: no requirements gathering, no plan-review rounds, no questions,
and at most five model stages (investigate, fix, test, review, and one spare)."""
from harness.oracle import python_change_checks, run_checks


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="pager")
    checks += run_checks(run, workflow="bugfix", no_requirements=True, no_plan_review=True,
                         max_questions=0, max_model_stages=5)
    return checks
