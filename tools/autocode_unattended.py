"""Run AutoCode for an unattended caller, such as another coding agent.

The caller can start a run, continue one, or read its status. It can never
make a decision that belongs to the operator: answering or delegating
questions, approving a plan, accepting completion, resuming a pause,
retrying or diagnosing a failed stage, or submitting feedback. AutoCode's
own deterministic controller still decides every transition; this wrapper
only refuses those flags, runs AutoCode once with no terminal input, and
prints the saved status when it stops.

Exit codes follow AutoCode: 0 when an action was saved or the task
completed, 2 when AutoCode stopped for the operator (or refused the call).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

# Every flag that records an operator decision or recovers from a pause.
OPERATOR_FLAGS = (
    "--answer", "--feedback", "--delegate", "--delegate-all", "--reject-assumption",
    "--approve-goal", "--edit-goal", "--approve-review", "--reconcile-review",
    "--accept-completion", "--review-token", "--resume-paused", "--retry-failed-stage",
    "--diagnose-failed-stage", "--retry-builder", "--retry-report", "--abandon-stage",
    "--accept-transport-change", "--planning-review-call-limit", "--migrate-only",
    "--chat",
)
# Subcommands that submit interventions or run other flows.
OPERATOR_SUBCOMMANDS = ("tasks", "ui", "compare-baseline", "capture", "registry", "intervention")

STOP_NOTICE = """\
AUTOCODE STOPPED FOR THE OPERATOR (exit {rc}).
Report the output above to the operator verbatim and stop.
Do not edit files, answer questions, approve, retry or resume on the operator's behalf."""


def refused(argv: list[str]) -> str | None:
    """Return why argv is refused, or None when it is allowed."""
    if argv and argv[0] in OPERATOR_SUBCOMMANDS:
        return f"the '{argv[0]}' subcommand is operator-only"
    for arg in argv:
        if arg == "--":
            break
        if not arg.startswith("--") or len(arg) <= 2:
            continue
        name = arg.split("=", 1)[0]
        # argparse accepts unambiguous prefixes, so also refuse abbreviations.
        # --no-chat is allowed: it is forced anyway.
        if name == "--no-chat":
            continue
        for flag in OPERATOR_FLAGS:
            if flag.startswith(name):
                return f"{flag} is an operator decision; run autocode directly to make it"
    return None


def autocode_command() -> list[str]:
    override = os.environ.get("AUTOCODE_UNATTENDED_COMMAND")
    if override:
        return override.split()
    return [sys.executable, str(Path(__file__).resolve().with_name("autocode.py"))]


def option_value(argv: list[str], flag: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def run(argv: list[str]) -> int:
    reason = refused(argv)
    if reason:
        print(f"autocode-unattended: refused: {reason}", file=sys.stderr)
        return 2
    command = autocode_command()
    status_only = "--status" in argv or "--dry-run" in argv
    process = subprocess.Popen([*command, *argv, "--no-chat"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    run_dir = option_value(argv, "--run-dir")
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        match = re.match(r"Run: (.+)$", line.rstrip("\n"))
        if match:
            run_dir = match.group(1)
    rc = process.wait()
    if status_only:
        return rc
    if run_dir:
        print("\n--- autocode status ---", flush=True)
        status = [*command, "--run-dir", run_dir, "--status"]
        workspace = option_value(argv, "--workspace")
        if workspace:
            status += ["--workspace", workspace]
        result = subprocess.run(status, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, check=False)
        sys.stdout.write(result.stdout)
    if rc != 0:
        print("\n" + STOP_NOTICE.format(rc=rc), flush=True)
    return rc


def cli() -> int:
    if sys.argv[1:2] in (["-h"], ["--help"]):
        argparse.ArgumentParser(
            prog="autocode-unattended",
            description=__doc__.split("\n\n")[1],
            epilog="Takes AutoCode's own arguments, minus operator decisions: "
                   + ", ".join(OPERATOR_FLAGS)).print_help()
        return 0
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())
