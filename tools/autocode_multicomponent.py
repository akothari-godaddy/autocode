"""Build a multi-component system from an architecture record.

Given the design one AutoCode task can already produce (components, contracts
and a dependency graph — see scenarios/catalog/architecture-two-services), this
drives one independent AutoCode task run per component, in parallel within
each dependency batch, then combines the finished components' changes into
one workspace.

docs/task-lanes.md already runs several tasks; its own limit is the gap this
fills: "Autocode does not guess how to merge parallel source changes." Here,
combining is safe because every component builds against the same committed
contracts, not against another component's code — components with no
dependency between them touch disjoint directories by construction (each
owns `components/<id>/`, checked after the fact, not merely requested), so
parallel work cannot collide, and a dependent component sees its
dependency's *contract*, never its implementation.

AutoCode itself never commits a Builder's changes (see README: "dirty source
changes matter, not only Git commits"), so a component's work exists only as
an uncommitted diff in its own worktree. Integration here uses the same
technique AutoCode's own internal parallel-milestone orchestrator uses
(tools/autocode_dispatch.py): snapshot a worktree's current state into a
detached commit without touching its index or HEAD, diff that against the
worktree's starting commit, and apply the diff elsewhere. It is reimplemented
here, independently, rather than imported, so this module stays outside the
core's import cycle (AGENTS.md rule 5) and is testable on its own.

This is a new layer, not a modification of the single-task engine: it drives
task runs only through `autocode_taskrun.TaskRun` and never imports the
runner's internals or reads state.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
import threading
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .autocode_taskrun import TaskRun, TaskRunError
    from .autocode_workspaces import keep_out_of_git
except ImportError:
    from autocode_taskrun import TaskRun, TaskRunError
    from autocode_workspaces import keep_out_of_git

_WORKTREE_LOCK = threading.Lock()
GIT_IDENTITY = ("-c", "user.name=AutoCode", "-c", "user.email=autocode@localhost")
EXCLUDE = (":(exclude).autocode", ":(exclude).autocode-ui", ":(exclude,glob)**/__pycache__/**",
          ":(exclude,glob)**/*.pyc")
# Component ids and contract names become path segments (a worktree directory, a
# branch name, a contract filename); an architecture file is data a model wrote,
# not trusted input, so reject anything that could escape its intended directory
# (a slash, a leading dot, ".."). Same pattern already used for task-lane ids in
# autocode_tasks.py.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _check_safe_name(kind: str, name: str) -> str:
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or ".." in name:
        raise ArchitectureError(f"{kind} {name!r} must be a plain name (letters, digits, '.', '_', '-', "
                                f"no '..', max 64 chars) — it becomes a directory, branch and file name")
    return name


class ArchitectureError(ValueError):
    """The architecture record is malformed or has no valid build order."""


@dataclass(frozen=True)
class Component:
    id: str
    description: str
    requirements: tuple[str, ...]
    depends_on: tuple[str, ...]
    publishes_contracts: tuple[str, ...]
    consumes_contracts: tuple[str, ...]

    @property
    def owned_prefix(self) -> str:
        return f"components/{self.id}/"


@dataclass
class ComponentResult:
    component: Component
    workspace: Path
    base_commit: str | None = None
    run: TaskRun | None = None
    view: dict | None = None
    error: str | None = None

    @property
    def ready_to_integrate(self) -> bool:
        return self.view is not None and self.view.get("done", False) and not self.error


@dataclass
class Architecture:
    components: dict[str, Component] = field(default_factory=dict)
    contracts_dir: Path | None = None

    @classmethod
    def load(cls, directory: Path) -> "Architecture":
        directory = Path(directory)
        raw = _read_json(directory / "components.json")
        if not isinstance(raw, list) or not raw:
            raise ArchitectureError(f"{directory}/components.json must be a nonempty array")
        components = {}
        for row in raw:
            if not isinstance(row, dict) or not row.get("id"):
                raise ArchitectureError("each component needs an id")
            component_id = _check_safe_name("component id", row["id"])
            publishes = tuple(_check_safe_name("contract name", name) for name in row.get("publishes_contracts", []))
            consumes = tuple(_check_safe_name("contract name", name) for name in row.get("consumes_contracts", []))
            components[component_id] = Component(
                id=component_id, description=row.get("description", ""),
                requirements=tuple(row.get("requirements", [])), depends_on=tuple(row.get("depends_on", [])),
                publishes_contracts=publishes, consumes_contracts=consumes)
        for component in components.values():
            unknown = [dep for dep in component.depends_on if dep not in components]
            if unknown:
                raise ArchitectureError(f"{component.id} depends_on unknown component(s) {unknown}")
        return cls(components=components, contracts_dir=directory / "contracts")

    def batches(self) -> list[list[Component]]:
        """Components grouped so a batch's members share no dependency between them,
        and every earlier batch is complete before the next one is considered."""
        remaining = dict(self.components)
        done: set[str] = set()
        result: list[list[Component]] = []
        while remaining:
            ready = [c for c in remaining.values() if all(dep in done for dep in c.depends_on)]
            if not ready:
                raise ArchitectureError(f"dependency cycle among {sorted(remaining)}")
            result.append(sorted(ready, key=lambda c: c.id))
            for component in ready:
                done.add(component.id)
                del remaining[component.id]
        return result


def component_brief(component: Component, architecture: Architecture) -> str:
    """The brief given to the task run building one component.

    Embeds the actual contract schemas, not just their names, so the Builder
    has ground truth for both what it must publish and what it may assume
    about a dependency's data, without reading another component's code.
    """
    lines = [
        f"Implement the {component.id} component of a larger system: {component.description}",
        f"Requirements this component is responsible for: {', '.join(component.requirements) or '(none declared)'}.",
        f"Own only the directory {component.owned_prefix}; do not create or edit any file outside it.",
    ]
    for name in component.publishes_contracts:
        schema = _read_json(architecture.contracts_dir / f"{name}.schema.json")
        lines.append(f"This component publishes the `{name}` contract. Other components will send or store data "
                     f"matching this JSON Schema: {json.dumps(schema)}")
    for name in component.consumes_contracts:
        schema = _read_json(architecture.contracts_dir / f"{name}.schema.json")
        lines.append(f"This component consumes the `{name}` contract, published by another component being built "
                     f"separately. Assume only this JSON Schema about it, nothing about its implementation: "
                     f"{json.dumps(schema)}")
    lines.append("Do not implement or stub another component's directory; integration happens separately.")
    return " ".join(lines)


class MultiComponentBuild:
    """Drives one task run per component of an architecture, then combines them.

    ``repo`` is an existing Git repository with a committed HEAD; the
    architecture files should already be committed there (as, for example,
    the output of an architecture-only task run). Each component gets its own
    worktree under ``repo/.autocode-components/<id>``, created fresh from HEAD.
    """

    def __init__(self, repo: Path, architecture: Architecture, *, options: tuple[str, ...] = (),
                 env: dict | None = None, timeout: float | None = None, max_advances: int = 20):
        self.repo = Path(repo).resolve()
        self.architecture = architecture
        self.options, self.env, self.timeout, self.max_advances = options, env, timeout, max_advances
        self.results: dict[str, ComponentResult] = {}

    def build(self, *, auto_approve: bool = False) -> dict[str, ComponentResult]:
        """Run every not-yet-attempted component, in dependency batches, in parallel
        within a batch. A component already in ``self.results`` (from a previous
        call) is left untouched; clear its entry to retry it.

        With ``auto_approve``, plan approval, review acceptance and clarifying
        questions are answered automatically with AutoCode's own proposed
        defaults — appropriate only when a person has delegated that decision
        to this build, as scenario runs do. Without it, a component that needs
        a decision stops with its ``view`` reporting what it needs; the caller
        resolves it through the returned ``ComponentResult.run`` and calls
        ``build`` again to continue the rest.
        """
        # A worktree directory removed without `git worktree remove`/`prune` (by hand,
        # or by a crashed earlier attempt) leaves its registration behind; the next
        # `git worktree add` for that path then fails outright. Since callers are
        # expected to have already checked no `.autocode-components/<id>` directory
        # exists (autocode_components.cli does), any registration still around at this
        # point is exactly that stale case, safe to clear before building anything.
        _git(self.repo, "worktree", "prune")
        for batch in self.architecture.batches():
            pending = [c for c in batch if c.id not in self.results]
            if not pending:
                continue
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                for result in pool.map(lambda c: self._build_one(c, auto_approve), pending):
                    self.results[result.component.id] = result
        return self.results

    def _build_one(self, component: Component, auto_approve: bool) -> ComponentResult:
        workspace = self.repo / ".autocode-components" / component.id
        try:
            base_commit, run = self._new_worktree_run(component, workspace)
            view = run.status()
            while auto_approve and not view["done"] and view["needs"]["kind"] != "resume":
                kind = view["needs"]["kind"]
                view = run.advance_until_input(self.max_advances) if kind == "continue" else _serve(run, view["needs"])
            error = None if view["done"] else f"stopped needing {view['needs']}"
            return ComponentResult(component, workspace, base_commit, run=run, view=view, error=error)
        except TaskRunError as error:
            return ComponentResult(component, workspace, None, error=str(error))
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error)).strip()
            return ComponentResult(component, workspace, None, error=f"could not prepare a worktree: {detail}")

    def _new_worktree_run(self, component: Component, workspace: Path) -> tuple[str, TaskRun]:
        keep_out_of_git(self.repo, ".autocode-components")
        workspace.parent.mkdir(parents=True, exist_ok=True)
        branch = f"components/{component.id}-{uuid.uuid4().hex[:8]}"
        # Components in one batch start in parallel threads, but `git worktree add` on one
        # repository is not safe to run concurrently (ref and worktree-metadata locks), so
        # only the git setup is serialized; the component runs themselves stay parallel.
        with _WORKTREE_LOCK:
            base_commit = _git(self.repo, "rev-parse", "HEAD")
            _git(self.repo, "worktree", "add", "-b", branch, str(workspace), base_commit)
        run = TaskRun.start(workspace, component_brief(component, self.architecture),
                            options=self.options, env=self.env, timeout=self.timeout)
        return base_commit, run

    def integrate(self, target: Path) -> dict:
        """Apply every finished component's changes into ``target``, an existing
        worktree of the same repository checked out at (or ahead of) the commit
        every component was built from. Ownership is re-checked here, not just
        requested in the brief: a component whose diff touches anything outside
        its own directory is refused rather than silently combined. Stops at the
        first patch that fails ownership or fails to apply, leaving earlier
        components already applied; nothing is committed, matching how AutoCode
        leaves a single task's own work for review before it is committed.
        """
        finished = sorted((r for r in self.results.values() if r.ready_to_integrate), key=lambda r: r.component.id)
        if not finished:
            return {"target": str(target), "integrated": [], "failed": None, "detail": "no finished component"}
        integrated = []
        for result in finished:
            snapshot = _snapshot_commit(result.workspace)
            changed = _git(result.workspace, "diff", "--name-only", result.base_commit, snapshot).splitlines()
            outside = [path for path in changed if not path.startswith(result.component.owned_prefix)]
            if outside:
                return {"target": str(target), "integrated": integrated, "failed": result.component.id,
                        "detail": f"changed files outside {result.component.owned_prefix}: {outside}"}
            patch = _git_bytes(result.workspace, "diff", "--binary", result.base_commit, snapshot)
            if patch:
                check = subprocess.run(["git", "apply", "--check", "--binary", "-"], cwd=target,
                                       input=patch, capture_output=True)
                if check.returncode != 0:
                    return {"target": str(target), "integrated": integrated, "failed": result.component.id,
                            "detail": check.stderr.decode(errors="replace")[-800:]}
                subprocess.run(["git", "apply", "--binary", "-"], cwd=target, input=patch, check=True)
            integrated.append(result.component.id)
        return {"target": str(target), "integrated": integrated, "failed": None}


def _serve(run: TaskRun, need: dict) -> dict:
    kind = need["kind"]
    if kind == "approve_plan":
        return run.approve_plan(need["token"])
    if kind == "answer":
        for question in need["questions"]:
            options = question.get("options") or []
            answer = question.get("proposed_default") or (options[0] if options else "yes")
            run.answer(question["id"], answer)
        return run.status()
    if kind == "review":
        for criterion in need["criteria"]:
            run.approve_review(criterion, need["token"])
        return run.status()
    if kind == "planning_budget":
        return run.feedback("The previous planning cycle used up its review budget. "
                            "Produce a complete final plan now and finalize it.")
    raise TaskRunError(f"no automatic way to serve a {kind!r} need")


def _snapshot_commit(workspace: Path) -> str:
    """A commit capturing the worktree's full current state — including uncommitted
    and untracked changes — without touching its actual index or HEAD. The scratch
    index lives outside the workspace: inside it, `git add -A` would pick up the
    index file itself as an untracked change before it could be removed."""
    index = Path(tempfile.gettempdir()) / f"autocode-multicomponent-index-{uuid.uuid4().hex}"
    env = {"GIT_INDEX_FILE": str(index), "GIT_AUTHOR_NAME": "AutoCode", "GIT_AUTHOR_EMAIL": "autocode@localhost",
          "GIT_COMMITTER_NAME": "AutoCode", "GIT_COMMITTER_EMAIL": "autocode@localhost"}
    try:
        _git(workspace, "read-tree", "HEAD", env=env)
        _git(workspace, "add", "-A", "--", ".", *EXCLUDE, env=env)
        tree = _git(workspace, "write-tree", env=env)
        return _git(workspace, "commit-tree", tree, "-p", "HEAD", "-m", "Component snapshot", env=env)
    finally:
        index.unlink(missing_ok=True)


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    import os
    full_env = {**os.environ, **env} if env else {**os.environ}
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, check=True, env=full_env,
                          capture_output=True, text=True).stdout.strip()


def _git_bytes(cwd: Path, *args: str) -> bytes:
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, check=True, capture_output=True).stdout


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ArchitectureError(f"{path}: {error}") from None
