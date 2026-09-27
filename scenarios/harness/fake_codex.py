#!/usr/bin/env python3
"""Scripted stand-in for the ``codex`` CLI, used by ``run --fake``.

It plans from the scenario brief, "builds" by copying the scenario's reference
solution into the workspace, and validates by really running the scenario's
check command. Only the model is fake: AutoCode's CLI, state machine, approval
gates and evidence checks run for real. This proves the harness and AutoCode's
plumbing for a scenario; it says nothing about model quality.

Configuration comes from the JSON file named by SCENARIO_FAKE_CONFIG:
{"title", "brief", "reference", "check", "paths"}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

CONFIG = json.loads(Path(os.environ["SCENARIO_FAKE_CONFIG"]).read_text())
CHECK = CONFIG["check"]
PATHS = CONFIG["paths"]


def requirements() -> list[dict]:
    """One requirement per sentence, quoted verbatim, as AutoCode's planner rules demand."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", CONFIG["brief"].strip()) if part.strip()]
    return [{"id": f"R{number}", "text": sentence, "source_quote": sentence}
            for number, sentence in enumerate(sentences, start=1)]


def source_refs() -> list[str]:
    """AutoCode requires planning to cite real files once the workspace has any."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    return tracked[:8] or ["task"]


def trace() -> list[dict]:
    return [{"requirement_id": row["id"], "disposition": "covered", "evidence": row["text"]}
            for row in requirements()]


def contract(final: bool = False) -> dict:
    body = {
        "intended_outcome": CONFIG["title"],
        "intended_user": "The scenario requester",
        "end_to_end_flow": ["Apply the change", f"Run {CHECK}"],
        "technical_approach": ["Scripted fake provider applies the scenario reference solution"],
        "milestones": [{"id": "M1", "objective": CONFIG["title"], "acceptance_criteria": ["C1"],
                        "depends_on": [], "affected_paths": PATHS}],
        "deliverables": PATHS,
        "required_behaviors": [row["text"] for row in requirements()],
        "important_failure_cases": ["The scenario check command fails"],
        "scope_exclusions": ["Anything outside the scenario brief"],
        "constraints": ["Change only the paths the reference solution touches"],
        "permission_boundaries": ["Read and edit only this scenario workspace"],
        "accepted_assumptions": [{"text": "The reference solution is correct",
                                  "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{"id": "C1", "criterion": "The scenario check command passes",
                                 "verification_method": CHECK, "human_review": False}],
        "open_blocking_questions": [],
    }
    if final:
        body["initial_task"] = {"kind": "implement", "milestone_id": "M1", "objective": CONFIG["title"],
                                "affected_paths": PATHS, "requirements": [requirements()[0]["text"]],
                                "acceptance_criteria": ["C1"], "validation_plan": [CHECK]}
    return body


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def run_check() -> int:
    proc = subprocess.run(CHECK, shell=True, capture_output=True, text=True, timeout=600)
    emit({"type": "item.completed", "item": {
        "id": "check", "type": "command_execution", "command": CHECK,
        "exit_code": proc.returncode, "aggregated_output": (proc.stdout + proc.stderr)[-2000:]}})
    return proc.returncode


def recognize(brief: str) -> dict:
    """The fake's script for the job-recognition stage: keyword rules over the brief.

    This is not a model and says nothing about model quality; it exists so the
    plumbing (the stage runs, its answer reaches the status view) can be proven
    offline. A live profile is what tests recognition itself.
    """
    text = brief.lower()

    def has(*patterns):
        return any(re.search(pattern, text) for pattern in patterns)

    if has(r"\bimplement (it|this|the design)\b", r"has already been .*approved") and not has(r"do(n't| not) implement anything"):
        kind, signal = "build", "implement it / already approved"
    elif has(r"\bdesign\b") and has(r"\breview\b", r"do(n't| not) implement", r"\bdesign how\b", r"^design\b"):
        kind, signal = "design", "design + review/don't implement"
    elif has(r"\breview\b", r"look over", r"safe to merge", r"\bpr[- ]?\d+", r"\.patch\b", r"\bdiff\b"):
        kind, signal = "review", "review/patch"
    elif has(r"\bfix\b", r"figure out why", r"stopped (working|being)", r"\bbug\b", r"duplicat", r"\btwice\b"):
        kind, signal = "bugfix", "fix/why/duplicates"
    elif text.rstrip().endswith("?") or has(r"^should ", r"^why ", r"^what would", r"want to understand", r"the analysis"):
        kind, signal = "discuss", "a question"
    else:
        kind, signal = "build", "no other signal"
    return {"workflow": kind, "reason": f"Scripted keyword rule: {signal}", "signals": [signal]}


def review() -> dict:
    """The fake's review: the findings from the solution it was told to apply (reference or broken).
    The runner writes review/findings.json from this report; the fake writes nothing."""
    path = Path(CONFIG["reference"]) / "review" / "findings.json"
    saved = json.loads(path.read_text()) if path.is_file() else {}
    # Targeted tests in the solution are delivered into the workspace under review/tests/,
    # the one place a review may write (autocode_review_job.TESTS_PREFIX).
    tests = Path(CONFIG["reference"]) / "review" / "tests"
    delivered = []
    if tests.is_dir():
        shutil.copytree(tests, Path.cwd() / "review" / "tests", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        delivered = sorted(f"review/tests/{p.name}" for p in tests.glob("test_*.py"))
    return {"verdict": saved.get("verdict", "approve"), "summary": "Scripted review from the scenario solution",
            "change_under_review": "the change named in the request",
            "findings": [{key: f.get(key, [] if key == "lines" else "") for key in
                          ("id", "severity", "file", "lines", "summary", "evidence")} for f in saved.get("findings", [])],
            "tests_run": [str(t) for t in saved.get("tests_run", [])], "delivered_tests": delivered}


def investigate() -> dict:
    """The fake's investigation: the diagnosis note in the solution it was told to apply, if any.
    A note with "reproduced": false ends the run; anything else hands over to the build pipeline,
    where the fake Builder applies the solution. The runner writes the note; the fake writes nothing."""
    notes = sorted((Path(CONFIG["reference"]) / "docs" / "bugs").glob("*.json"))
    saved = json.loads(notes[0].read_text()) if notes else {}
    reproduced = saved.get("reproduced", True) is not False
    text = lambda *keys: next((str(saved[key]) for key in keys if saved.get(key)), "")
    return {"outcome": "reproduced" if reproduced else "not_reproduced",
            "note_path": f"docs/bugs/{notes[0].name}" if notes else "docs/bugs/scripted-diagnosis.json",
            "observed": text("observed", "observed_by_reporter") or CONFIG["title"],
            "reproduction": text("reproduction", "reproduction_attempted") or "Scripted reproduction",
            "root_cause": text("root_cause") or ("Scripted root cause" if reproduced else ""),
            "affected_paths": (saved.get("affected_paths") or [p for p in PATHS if p.endswith(".py")]) if reproduced else [],
            "invariant": text("invariant") or ("Scripted invariant" if reproduced else ""),
            "conclusion": text("conclusion", "finding", "fix") or "Scripted conclusion",
            "fix_size": "small" if reproduced else "none", "fix_plan": [text("fix")] if reproduced and saved.get("fix") else [],
            "questions": [str(q) for q in saved.get("questions", [])], "tests_run": ["scripted"]}


def report_for(stage: str, data: dict) -> dict:
    if stage == "recognize_workflow":
        return recognize(data.get("task") or CONFIG["brief"])
    if stage == "review_change":
        return review()
    if stage == "investigate_bug":
        return investigate()
    task = data.get("current_task") or {}
    revision = data.get("goal_contract") or {"revision": 0, "hash": ""}
    common = {
        "contract_revision": revision.get("revision", 0), "contract_hash": revision.get("hash", ""),
        "task_id": task.get("id", ""), "deferred_backlog": [],
        "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                         "options": [], "proposed_delta": ""},
    }
    planning = {"code_refs": [ref for ref in source_refs() if ref != "task"], "contract_changes": [], "conflict_resolutions": [], "requirement_trace": trace()}
    if stage == "requirements_gather":
        return {"summary": "Scripted requirements: one per brief sentence",
                "intended_outcome": CONFIG["title"], "required_behaviors": [r["text"] for r in requirements()],
                "constraints": [], "acceptance_tests": [CHECK], "source_refs": source_refs(),
                "proposed_assumptions": [], "open_questions": [], "requirements": requirements(),
                "ignored_statements": [], "conflicts": [], "proposed_reframes": []}
    if stage == "astra_discovery":
        return {"summary": "Scripted plan", "contract": contract(), "alternatives": [], "uncertainties": [], **planning}
    if stage == "astra_challenge":
        return {"summary": "Scripted plan review: no concerns", "concerns": []}
    if stage == "glm_revise":
        return {"summary": "Scripted revision: nothing to revise", "contract": contract(), "responses": [], **planning}
    if stage == "astra_finalize":
        final = {key: value for key, value in planning.items() if key != "code_refs"}
        return {"summary": "Scripted final plan", "contract": contract(final=True), "decisions": [], **final}
    if stage == "terra":
        shutil.copytree(CONFIG["reference"], Path.cwd(), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        code = run_check()
        return {**common, "summary": "Applied the scenario reference solution", "changed_files": PATHS,
                "commands_run": [CHECK], "results": [f"exit {code}"], "remaining_risks": [],
                "evidence_refs": ["event:check"], "addressed_requirements": ["C1"],
                "untested_behavior": [], "recommended_checks": [CHECK]}
    if stage in ("sol", "astra_checkpoint"):
        code = run_check()
        status = "PASS" if code == 0 else "FAIL"
        return {**common, "verdict": status, "checks_run": [CHECK], "findings": [],
                "finding_dispositions": [], "unverified_criteria": [],
                "checks": [{"command": CHECK, "exit_code": code, "evidence_ref": "event:check"}],
                "criterion_results": [{"id": "C1", "status": status, "evidence_refs": ["event:check"]}],
                "end_to_end_result": {"status": status, "summary": f"{CHECK} exited {code}",
                                      "evidence_refs": ["event:check"]}}
    if stage in ("astra_review", "astra_plan", "astra_resolve"):
        return {**common, "status": "COMPLETE",
                "acceptance_criteria": [{"id": "C1", "criterion": "The scenario check command passes",
                                         "status": "verified", "evidence": "event:check"}],
                "evidence": ["event:check"], "next_objective": "", "blocker": "", "plan": [],
                "affected_paths": [], "findings": [], "finding_dispositions": [], "agreed_limitations": [],
                "next_task": {"kind": "none", "milestone_id": "", "requirements": [],
                              "acceptance_criteria": [], "validation_plan": [], "findings": []}}
    raise SystemExit(f"fake_codex: no scripted report for stage {stage!r}")


def main() -> int:
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (scenario fake provider)")
        return 0
    prompt = sys.stdin.read()
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    emit({"type": "thread.started", "thread_id": session})
    if "CURRENT HANDOFF DATA\n" not in prompt:
        emit({"error": "no handoff data"})
        return 0
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    original = data.get("original") or {}
    stage = data.get("stage") or original.get("stage") or ""
    if data.get("report_repair"):
        # Report repairs are answered as the stage that owns them.
        stage = original.get("stage", stage)
    report = report_for(stage, data)
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report))
    emit({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
