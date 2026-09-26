"""CLI for building a multi-component system from an architecture record.

    autocode components ARCHITECTURE --workspace REPO [--auto-approve] \\
        [--integrate TARGET] [--engine codex] [model options...]

``ARCHITECTURE`` (absolute, or relative to ``--workspace``) holds
``components.json``, ``dependency_trace.json`` and ``contracts/*.schema.json``
— the design one AutoCode task can already produce; see
scenarios/catalog/architecture-two-services. ``--workspace`` is an existing Git
repository with that architecture already committed. See docs/task-run.md and
``autocode_multicomponent.py`` for what this drives.

Known limitation: a component already stopped for input, or already built,
lives in ``<workspace>/.autocode-components/<id>``; this command does not yet
resume a partial build across separate invocations (there is no saved manifest
of the last attempt). A stopped component's own run is still an ordinary
AutoCode run — inspect and resume it directly with
``autocode --workspace WORKSPACE --run-dir RUN_DIR --status`` using the
``run_dir`` this command prints, then rerun with the worktree removed to
rebuild it, or leave it and integrate the rest.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

try:
    from . import autocode_multicomponent as mc
except ImportError:
    import autocode_multicomponent as mc


def _resolve(path: Path, workspace: Path) -> Path:
    return path if path.is_absolute() else (workspace / path)


def _status(result: "mc.ComponentResult") -> str:
    if result.ready_to_integrate:
        return "done"
    if result.view is None:
        return "error"
    return "needs_input"


def _ensure_worktree(repo: Path, target: Path) -> None:
    """Create ``target`` as a fresh worktree from HEAD if it does not already exist."""
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-c", "user.name=AutoCode", "-c", "user.email=autocode@localhost",
                   "worktree", "add", str(target), "HEAD"], cwd=repo, check=True, capture_output=True, text=True)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("architecture", type=Path,
                        help="architecture directory, absolute or relative to --workspace")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="Git repository with the architecture already committed (default: cwd)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="approve each component's plan and reviews, and answer questions with AutoCode's "
                             "own proposed defaults, without asking a person. Pass this only when a person has "
                             "delegated that decision for this build; it is not itself that decision.")
    parser.add_argument("--integrate", type=Path, metavar="TARGET",
                        help="combine finished components into this worktree of the same repository, relative "
                             "to --workspace unless absolute; created fresh from HEAD if it does not exist")
    parser.add_argument("--engine", choices=["codex", "gocode", "opencode"])
    parser.add_argument("--provider", help="see docs/providers.md")
    parser.add_argument("--joint-planning", action="store_true",
                        help="separate requirements, planning and independent review per component; default for "
                             "new OpenCode/GoCode runs, opt-in for --engine codex (see docs/models.md)")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--options", default="", metavar="FLAGS",
                        help="extra flags passed to every component's `autocode` invocation verbatim, "
                             "shell-quoted, e.g. --options \"--terra-model x --sol-model y\" — see "
                             "docs/models.md and `autocode --help` for the full set")
    parser.add_argument("--max-advances", type=int, default=20, metavar="N",
                        help="CLI relaunches per component before an honest stop (default: 20)")
    parser.add_argument("--timeout", type=int, metavar="SECONDS", help="wall-clock budget per component")
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    if not (workspace / ".git").exists():
        parser.error(f"workspace is not a Git repository: {workspace}")
    try:
        architecture = mc.Architecture.load(_resolve(args.architecture, workspace))
        architecture.batches()  # fail fast on a cycle before starting anything
    except mc.ArchitectureError as error:
        parser.error(str(error))

    existing = sorted(cid for cid in architecture.components if (workspace / ".autocode-components" / cid).exists())
    if existing:
        parser.error(f"a worktree already exists for {', '.join(existing)}; this command does not yet resume a "
                     f"partial build (see this module's docstring). Inspect it directly with `autocode --workspace "
                     f"{workspace} --run-dir <its run dir> --status`, or remove .autocode-components/<id> to "
                     f"rebuild it from scratch.")

    options: list[str] = []
    for flag, value in (("--engine", args.engine), ("--provider", args.provider),
                        ("--reasoning-effort", args.reasoning_effort)):
        if value:
            options += [flag, value]
    if args.joint_planning:
        options.append("--joint-planning")
    options += shlex.split(args.options)

    build = mc.MultiComponentBuild(workspace, architecture, options=tuple(options), timeout=args.timeout,
                                   max_advances=args.max_advances)
    results = build.build(auto_approve=args.auto_approve)

    summary = {"components": {cid: {"status": _status(result), "workspace": str(result.workspace),
                                    "run_dir": str(result.run.run_dir) if result.run else None,
                                    "view": result.view, "error": result.error}
                              for cid, result in results.items()}}
    statuses = {info["status"] for info in summary["components"].values()}
    exit_code = 1 if "error" in statuses else 2 if "needs_input" in statuses else 0

    if args.integrate:
        target = _resolve(args.integrate, workspace)
        try:
            _ensure_worktree(workspace, target)
        except subprocess.CalledProcessError as error:
            parser.error(f"could not prepare integration worktree {target}: {error.stderr}")
        summary["integration"] = build.integrate(target)
        if summary["integration"]["failed"]:
            exit_code = max(exit_code, 1)

    print(json.dumps(summary, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(cli())
