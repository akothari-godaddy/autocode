"""Helpers for scenario oracles.

An oracle judges the delivered project from the outside: it runs the project's
commands, runs hidden tests against a scratch copy, and reads files. It never
imports AutoCode and never trusts the run's own reports or the model's tests.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

IGNORED = shutil.ignore_patterns(".git", ".autocode", "__pycache__", "*.pyc")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def run(cmd: list[str], cwd: Path, *, timeout: int = 120, input: str | None = None) -> subprocess.CompletedProcess:
    """Run a command without raising: timeouts exit -1, a missing binary exits 127."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input)
    except subprocess.TimeoutExpired as error:
        out = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        return subprocess.CompletedProcess(cmd, -1, out, f"TIMEOUT after {timeout}s")
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(cmd, 127, "", str(error))


def tail(proc: subprocess.CompletedProcess, limit: int = 600) -> str:
    return f"exit {proc.returncode}: " + (proc.stdout + proc.stderr).strip()[-limit:]


@contextmanager
def scratch_copy(project: Path):
    """A throwaway copy of the delivered project, so checks cannot alter it."""
    with tempfile.TemporaryDirectory(prefix="oracle-") as tmp:
        target = Path(tmp) / "project"
        shutil.copytree(project, target, ignore=IGNORED)
        yield target


def python_tests(cwd: Path, start: str = "tests", timeout: int = 300) -> subprocess.CompletedProcess:
    return run([sys.executable, "-m", "unittest", "discover", "-s", start, "-t", "."], cwd, timeout=timeout)


def hidden_tests(copy: Path, hidden: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run the scenario's hidden unittest files from the root of a scratch copy."""
    target = copy / "_oracle_hidden"
    shutil.copytree(hidden, target, ignore=IGNORED)
    (target / "__init__.py").touch()
    return python_tests(copy, start="_oracle_hidden", timeout=timeout)


def test_names(root: Path) -> set[str]:
    """Names of test functions defined under ``root``."""
    names = set()
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        names.update(node.name for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef) and node.name.startswith("test"))
    return names


def non_stdlib_imports(project: Path) -> list[str]:
    """Imports that are neither standard library nor part of the project."""
    # Top-level modules and directories (namespace packages included) are the project's own code.
    local = {path.stem for path in project.glob("*.py")} | {path.name for path in project.iterdir() if path.is_dir()}
    foreign = []
    for path in sorted(project.rglob("*.py")):
        if {".git", ".autocode", "_oracle_hidden"} & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as error:
            foreign.append(f"{path.relative_to(project)}: syntax error {error}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            for name in (name.split(".")[0] for name in names):
                if name not in sys.stdlib_module_names and name not in local:
                    foreign.append(f"{path.relative_to(project)}: imports {name}")
    return foreign


def python_change_checks(project: Path, scenario, package: str) -> list[Check]:
    """Standard checks for a change to an existing Python project with a seed and hidden tests.

    The project's tests pass, the hidden tests pass, no existing test was removed,
    only the standard library is used, and the delivered tests fail when run
    against the seed's version of ``package`` — so they actually cover the change.
    """
    checks = []
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    with scratch_copy(project) as copy:
        shutil.rmtree(copy / package, ignore_errors=True)
        shutil.copytree(scenario.seed / package, copy / package, ignore=IGNORED)
        against_seed = python_tests(copy)
        checks.append(Check("new_tests_fail_on_original_code", against_seed.returncode != 0,
                            "delivered tests fail on the original code" if against_seed.returncode
                            else "delivered tests still pass on the original code"))
    missing = sorted(test_names(scenario.seed / "tests") - test_names(project / "tests"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks
