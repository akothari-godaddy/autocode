"""Drive AutoCode's public CLI through one scenario, serving its gates like a user.

The driver only calls the CLI and reads ``state.json``; it never writes state.
Gates served: clarifying questions (answered with AutoCode's proposed default,
and recorded), plan approval, human-review acceptance, and planning-budget
feedback. Anything else that stops the run is left for the verdict to judge.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import profiles
from .project import overlay_paths

REPO = Path(__file__).resolve().parents[2]
FAKE_PROVIDER = Path(__file__).resolve().parent / "fake_codex.py"
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Codex-engine flags for fake runs. The fake ignores models, but AutoCode's
# Codex path wants bare GPT names and distinct builder and verifier models.
FAKE_FLAGS = ["--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
              "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol",
              "--completion-model", "gpt-6-astra", "--glm-model", "gpt-5.6-sol",
              "--plan-reviewer-model", "gpt-6-astra"]


class DriveError(RuntimeError):
    """The harness could not take the run any further."""


def fake_setup(scenario, root: Path, solution: Path) -> tuple[list[str], dict]:
    """Put the scripted provider on PATH as ``codex``; return CLI flags and environment."""
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_PROVIDER, bindir / "codex")
    (bindir / "codex").chmod(0o755)
    config = root / "fake-config.json"
    config.write_text(json.dumps({"title": scenario.title, "brief": scenario.brief,
                                  "reference": str(solution), "check": scenario.fake_check,
                                  "paths": overlay_paths(solution)}))
    return FAKE_FLAGS, {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                        "SCENARIO_FAKE_CONFIG": str(config)}


def live_setup(profile_name: str) -> tuple[list[str], dict]:
    return profiles.flags(profiles.resolve(profile_name)), {}


class Driver:
    def __init__(self, project: Path, root: Path, flags: list[str], env: dict, *,
                 autocode: list[str], max_steps: int, timeout_seconds: int):
        self.project, self.root, self.flags, self.autocode = project, root, flags, autocode
        self.env = {**os.environ, "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1", **env}
        self.max_steps, self.deadline = max_steps, time.monotonic() + timeout_seconds
        self.steps: list[dict] = []
        self.answers: list[dict] = []
        self.run_dir: Path | None = None
        self.log = root / "steps.jsonl"

    def state(self) -> dict:
        path = self.run_dir / "state.json" if self.run_dir else None
        return json.loads(path.read_text()) if path and path.is_file() else {}

    def call(self, kind: str, *extra: str, task: str | None = None) -> None:
        if len(self.steps) >= self.max_steps:
            raise DriveError(f"step budget used up after {len(self.steps)} CLI calls")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DriveError(f"time budget used up after {len(self.steps)} CLI calls")
        cmd = [*self.autocode, *([task] if task else []), "--workspace", str(self.project),
               "--no-chat", "--in-place", *(["--run-dir", str(self.run_dir)] if self.run_dir else []),
               *self.flags, *extra]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, env=self.env, cwd=self.root, capture_output=True, text=True,
                                  timeout=remaining)
        except subprocess.TimeoutExpired:
            raise DriveError(f"{kind} was still running when the time budget ran out") from None
        step = {"kind": kind, "args": list(extra), "exit": proc.returncode,
                "seconds": round(time.monotonic() - started, 1),
                "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-1500:]}
        self.steps.append(step)
        with self.log.open("a") as handle:
            handle.write(json.dumps(step) + "\n")
        # 0 = finished, 2 = paused at a gate; both are normal CLI outcomes.
        if proc.returncode not in (0, 2):
            raise DriveError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}")

    def drive(self, brief: str) -> dict:
        self.call("start", task=brief)
        runs = self.project / ".autocode" / "runs"
        candidates = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime) if runs.is_dir() else []
        if not candidates:
            last = self.steps[-1]
            raise DriveError("the first CLI call did not create a run: "
                             + (last["stderr_tail"] or last["stdout_tail"]).strip()[-500:])
        self.run_dir = candidates[-1].parent
        while True:
            state = self.state()
            status = state.get("status", "")
            if status in COMPLETE or (status.startswith("PAUSED_") and status != "PAUSED_PLANNING_BUDGET"):
                return state
            if self.serve_gate(state):
                continue
            self.call("resume")
            after = self.state()
            if after.get("status") in COMPLETE:
                continue
            if all(after.get(key) == state.get(key) for key in ("status", "next_stage", "iteration", "phase")):
                raise DriveError(f"no progress at {status!r} (next_stage={after.get('next_stage')!r})")

    def serve_gate(self, state: dict) -> bool:
        """Answer one gate from the saved state; False when there is none to answer."""
        status = state.get("status", "")
        if status == "PAUSED_PLANNING_BUDGET":
            self.call("feedback", "--feedback", "The previous planning cycle used up its review budget. "
                      "Produce a complete final plan now and finalize it.")
            return True
        if status == "AWAITING_GOAL_APPROVAL":
            if not state.get("displayed_goal"):
                raise DriveError("AWAITING_GOAL_APPROVAL without a displayed plan to approve")
            self.call("approve-plan", "--approve-goal", state["displayed_goal"])
            return True
        answered = {row["id"] for row in self.answers}
        questions = [q for q in state.get("pending_questions") or [] if q.get("id") not in answered]
        if questions and status in ("DISCOVERING", "RUNNING", "WAITING_FOR_USER"):
            for question in questions:
                options = question.get("options") or []
                answer = question.get("proposed_default") or (options[0] if options else "yes")
                self.answers.append({"id": question.get("id"), "question": question.get("question"),
                                     "why": question.get("why"), "answer": answer})
                self.call("answer", "--answer", f"{question.get('id')}={answer}")
            return True
        request = state.get("user_request") or {}
        if request.get("kind") == "human_review":
            token = state.get("displayed_review") or state.get("displayed_goal")
            for criterion in request.get("criteria", []):
                self.call("accept-review", "--accept-review", f"{criterion}={token}" if token else criterion)
            return True
        return False


def default_autocode() -> list[str]:
    return [sys.executable, str(REPO / "tools" / "autocode.py")]


def metrics(state: dict) -> dict:
    """Stage count, model time and tokens, from the run's own stage records."""
    stages = state.get("stages") or []
    tokens = {"input": 0, "output": 0, "unknown_stages": 0}
    for stage in stages:
        usage = (stage.get("metrics") or {}).get("provider_tokens") or {}
        if usage.get("input_tokens") is None and stage.get("stage") != "orchestrator":
            tokens["unknown_stages"] += 1
        tokens["input"] += usage.get("input_tokens") or 0
        tokens["output"] += usage.get("output_tokens") or 0
    return {"stages": len(stages), "stage_names": [stage.get("stage") for stage in stages],
            "model_seconds": round(sum(stage.get("duration_seconds") or 0 for stage in stages), 1),
            "tokens": tokens}
