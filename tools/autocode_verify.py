"""Model-free verification of a bug-fix candidate.

The runner, not a model, decides whether a fix is proven. Two properties are
checked by executing the project's own tests in scratch worktrees:

* Regression proof (fail-to-pass). The candidate's new or changed test files
  are run twice with identical test code: once over the base revision's source
  and once over the candidate's source. They must fail on base and pass on the
  candidate. Because the only difference between the two trees is the
  non-test change, the flip is caused by the fix, not by the tests.
* No regressions (pass-to-pass). The project suite must pass on the candidate.
  When the suite already fails on base, per-test results (pytest, unittest)
  must show that nothing which passed on base fails now; without per-test
  results the outcome stays UNVERIFIED rather than being guessed.

The builder's workspace is never executed in: both runs happen in detached
worktrees assembled from the recorded diff, and the candidate snapshot is
checked before and after, so a verdict belongs to exactly one candidate.
Missing evidence is UNVERIFIED, never PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

try:
    from . import autocode_support as support
except ImportError:
    import autocode_support as support

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"
TEST_DIRS = frozenset({"test", "tests", "testing", "__tests__", "spec", "specs", "testdata", "test_data"})
TEST_NAME = re.compile(
    r"^(test_.*\.py|.*_tests?\.py|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?|.*_test\.go"
    r"|.*_(spec|test)\.rb|.*Tests?\.(java|kt|cs|swift|scala)|test_.*\.(rb|sh))$", re.I)
PYTHON_TEST_MODULE = re.compile(r"^(test_.*|.*_tests?)\.py$")
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")
TAIL_CHARS = 4000
DEFAULT_TIMEOUT = 900


# --- classification --------------------------------------------------------

def is_test_path(path: str) -> bool:
    """True for files that belong to the test suite rather than the product."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if any(part.lower() in TEST_DIRS for part in parts[:-1]):
        return True
    return bool(TEST_NAME.match(parts[-1]))


def _git(cwd, *args, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _ignored(path: str) -> bool:
    # Top-level dependency links are runner-made (link_dependencies), never part of a fix.
    return (path.startswith((".autocode/", ".autocode-ui/")) or "/__pycache__/" in f"/{path}"
            or path.endswith(".pyc") or path in DEPENDENCY_DIRS)


def changed_files(workspace, base) -> dict[str, str]:
    """Every path whose content differs from ``base``: committed, staged, dirty or untracked."""
    changes: dict[str, str] = {}
    tokens = _git(workspace, "diff", "--name-status", "-z", "--no-renames", base, "--").split("\0")
    for status, path in zip(tokens[0::2], tokens[1::2]):
        if path:
            changes[path] = {"A": "added", "D": "deleted"}.get(status[:1], "modified")
    for path in _git(workspace, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path:
            changes[path] = "added"
    return {path: status for path, status in sorted(changes.items()) if not _ignored(path)}


def diff_stats(workspace, base, changes) -> dict:
    lines = {}
    for line in _git(workspace, "diff", "--numstat", "--no-renames", base, "--").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] in changes:
            lines[parts[2]] = (int(parts[0]) if parts[0].isdigit() else 0, int(parts[1]) if parts[1].isdigit() else 0)
    for path, status in changes.items():
        file = Path(workspace) / path
        if path not in lines and status == "added" and file.is_file():
            try:
                lines[path] = (len(file.read_text(errors="replace").splitlines()), 0)
            except OSError:
                pass
    source = [p for p in changes if not is_test_path(p)]
    return {"files": len(changes), "source_files": len(source), "test_files": len(changes) - len(source),
            "lines_added": sum(a for a, _ in lines.values()), "lines_removed": sum(r for _, r in lines.values()),
            "source_lines_changed": sum(sum(lines.get(p, (0, 0))) for p in source)}


def removed_python_tests(workspace, base, changes) -> list[str]:
    """Names of ``def test*`` functions deleted from modified Python test files."""
    removed = []
    pattern = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(", re.M)
    for path, status in changes.items():
        if not (path.endswith(".py") and is_test_path(path)) or status == "added":
            continue
        before = set(pattern.findall(_git(workspace, "show", f"{base}:{path}", check=False)))
        after_file = Path(workspace) / path
        after = set(pattern.findall(after_file.read_text(errors="replace"))) if after_file.is_file() else set()
        removed += [f"{path}::{name}" for name in sorted(before - after)]
    return removed


# --- project test frameworks -----------------------------------------------

def python_for(project) -> str:
    project = Path(project)
    for relative in (".venv/bin/python", "venv/bin/python"):
        candidate = project / relative
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("python3") or sys.executable


def _read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _python_can_import(python, module):
    try:
        return subprocess.run([python, "-c", f"import {module}"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Framework:
    """How to run the whole suite and a targeted subset for one project."""

    def __init__(self, name, suite, *, python=None, runner=None, note=""):
        self.name, self.suite, self.python, self.runner, self.note = name, suite, python, runner, note

    @property
    def per_test(self):
        return self.name in ("pytest", "unittest")

    def targeted(self, test_paths):
        files = sorted(test_paths)
        if self.name == "pytest":
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return (f"{shlex.quote(self.python)} -m pytest -q -p no:cacheprovider "
                    + " ".join(map(shlex.quote, modules))) if modules else None
        if self.name == "unittest":
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return (f"{shlex.quote(self.python)} -m unittest -v "
                    + " ".join(map(shlex.quote, modules))) if modules else None
        if self.name == "go":
            packages = sorted({"./" + str(PurePosixPath(p).parent) if str(PurePosixPath(p).parent) != "." else "."
                               for p in files if p.endswith("_test.go")})
            return ("go test " + " ".join(map(shlex.quote, packages))) if packages else None
        if self.name in ("jest", "vitest", "mocha"):
            scripts = [p for p in files if re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", p) or p.endswith((".js", ".ts"))]
            if not scripts:
                return None
            verb = {"jest": "jest", "vitest": "vitest run", "mocha": "mocha"}[self.name]
            return f"npx --no-install {verb} " + " ".join(map(shlex.quote, scripts))
        if self.name == "rspec":
            specs = [p for p in files if p.endswith("_spec.rb")]
            return (f"{self.runner} " + " ".join(map(shlex.quote, specs))) if specs else None
        return None

    def to_dict(self):
        return {"name": self.name, "suite": self.suite, "python": self.python, "note": self.note}


def detect_framework(root, *, python=None) -> Framework | None:
    """Best-effort detection from the project's own configuration files."""
    root = Path(root)
    files = [p for p in _git(root, "ls-files", "-z", check=False).split("\0") if p]
    names = {PurePosixPath(p).name for p in files}
    has_python = any(p.endswith(".py") for p in files)
    if has_python:
        python = python or python_for(root)
        pyproject, setup_cfg, tox = _read(root / "pyproject.toml"), _read(root / "setup.cfg"), _read(root / "tox.ini")
        requirements = " ".join(_read(root / p) for p in files if re.match(r"(.*/)?requirements.*\.(txt|in)$", p))
        configured = ("pytest.ini" in names or "conftest.py" in names or "[tool.pytest" in pyproject
                      or "[tool:pytest]" in setup_cfg or "[pytest]" in tox or re.search(r"\bpytest\b", requirements + pyproject))
        if configured and _python_can_import(python, "pytest"):
            return Framework("pytest", f"{shlex.quote(python)} -m pytest -q -p no:cacheprovider "
                             "--continue-on-collection-errors", python=python)
        tests = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
        if tests:
            note = "pytest is configured but not importable; using unittest" if configured else ""
            if any("/" not in p for p in tests) or (root / "tests" / "__init__.py").is_file() \
                    or (root / "test" / "__init__.py").is_file():
                start = ""
            elif (root / "tests").is_dir():
                start = " -s tests"
            elif (root / "test").is_dir():
                start = " -s test"
            else:
                start = ""
            return Framework("unittest", f"{shlex.quote(python)} -m unittest discover -v{start}", python=python, note=note)
    if "go.mod" in files:
        return Framework("go", "go test ./...")
    if "package.json" in files:
        try:
            package = json.loads(_read(root / "package.json") or "{}")
        except ValueError:
            package = {}
        deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        script = package.get("scripts", {}).get("test", "")
        suite = "npm test --silent" if script and "no test specified" not in script else None
        for name in ("vitest", "jest", "mocha"):
            if name in deps:
                return Framework(name, suite or f"npx --no-install {'vitest run' if name == 'vitest' else name}")
        if suite:
            return Framework("npm", suite)
    if "Cargo.toml" in files:
        return Framework("cargo", "cargo test")
    if "Gemfile" in files and any(p.endswith("_spec.rb") for p in files):
        return Framework("rspec", "bundle exec rspec", runner="bundle exec rspec")
    if "Makefile" in files and re.search(r"^test\s*:", _read(root / "Makefile"), re.M):
        return Framework("make", "make test")
    return None


# --- execution --------------------------------------------------------------

def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_command(command, cwd, log_path, *, timeout=DEFAULT_TIMEOUT, env=None) -> dict:
    """Run one shell command in its own process group and return a receipt."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ if env is None else env, PYTHONDONTWRITEBYTECODE="1", CI="1")
    started = time.monotonic()
    timed_out = False
    with log_path.open("wb") as output:
        process = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                   env=environment)
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process)
            process.wait()
            exit_code = None
        finally:
            _kill_group(process)  # descendants that outlived the shell
    data = log_path.read_bytes()
    return {"command": command, "exit_code": exit_code, "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 2), "output": str(log_path),
            "output_sha256": hashlib.sha256(data).hexdigest(),
            "tail": data[-TAIL_CHARS:].decode("utf-8", "replace")}


def _with_results(framework, command, xml_path):
    if framework and framework.name == "pytest" and " -m pytest" in command:
        return f"{command} --junitxml={shlex.quote(str(xml_path))}"
    return command


def per_test_results(framework, receipt, xml_path) -> dict | None:
    """Failing test ids and the number of tests run, or None when unavailable."""
    if not framework or not framework.per_test:
        return None
    if framework.name == "pytest":
        if not Path(xml_path).is_file():
            return None
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            return None
        failed, total = [], 0
        for case in tree.iter("testcase"):
            total += 1
            if case.find("failure") is not None or case.find("error") is not None:
                failed.append(f"{case.get('classname', '')}::{case.get('name', '')}")
        return {"failed": sorted(set(failed)), "total": total}
    text = Path(receipt["output"]).read_text(errors="replace")
    ran = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
    if not ran:
        return None
    failed = re.findall(r"^(?:FAIL|ERROR): (\S+) \(([\w.]+)\)", text, re.M)
    return {"failed": sorted({f"{owner}::{name}" if not owner.endswith("." + name) else owner
                              for name, owner in failed}), "total": int(ran[-1])}


# --- scratch trees ----------------------------------------------------------

def link_dependencies(source_root, tree):
    """Expose ignored dependency directories (node_modules, venvs) to a scratch tree."""
    if not source_root:
        return
    for name in DEPENDENCY_DIRS:
        source, target = Path(source_root) / name, Path(tree) / name
        if source.is_dir() and not target.exists() and not target.is_symlink():
            target.symlink_to(source.resolve(), target_is_directory=True)


def make_tree(repo, base, destination, overlay_root, changes, *, dependencies_from=None):
    destination = Path(destination)
    if destination.exists():
        remove_tree(repo, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--detach", str(destination), base)
    for path, status in changes.items():
        target = destination / path
        if status == "deleted":
            if target.is_file() or target.is_symlink():
                target.unlink()
            continue
        source = Path(overlay_root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.is_file():
            target.unlink()
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        elif source.is_file():
            shutil.copy2(source, target)
    link_dependencies(dependencies_from, destination)
    return destination


def remove_tree(repo, destination):
    _git(repo, "worktree", "remove", "--force", str(destination), check=False)
    if Path(destination).exists():
        shutil.rmtree(destination, ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)


# --- verification -----------------------------------------------------------

def _mentions_tests(command, test_paths):
    for path in test_paths:
        posix = PurePosixPath(path)
        tokens = {path, posix.name, posix.stem, str(posix.with_suffix("")).replace("/", ".")}
        if str(posix.parent) not in ("", "."):
            tokens.add(str(posix.parent))
        if any(token and token in command for token in tokens):
            return True
    return False


def select_commands(framework, test_paths, *, suite_command=None, regression_command=None, reported=None):
    """Choose commands: explicit flags, then detection, then the builder's report.

    A builder-reported regression command is used only when it names at least
    one changed test file; otherwise it could check something other than the
    tests (for example grep the source) and prove nothing.
    """
    reported = reported or {}
    notes = []
    suite, suite_source = suite_command, "explicit" if suite_command else None
    if not suite and framework:
        suite, suite_source = framework.suite, f"detected:{framework.name}"
    if not suite and str(reported.get("test_command", "")).strip():
        suite, suite_source = reported["test_command"].strip(), "builder"
    regression, regression_source = regression_command, "explicit" if regression_command else None
    if not regression and framework:
        derived = framework.targeted(test_paths)
        if derived:
            regression, regression_source = derived, f"derived:{framework.name}"
    builder_regression = str(reported.get("regression_command", "")).strip()
    if not regression and builder_regression:
        if _mentions_tests(builder_regression, test_paths):
            regression, regression_source = builder_regression, "builder"
        else:
            notes.append("Ignored the builder's regression command because it does not name a changed test file")
    return {"suite": suite, "suite_source": suite_source, "regression": regression,
            "regression_source": regression_source, "notes": notes}


def run_suite(framework, command, tree, evidence_dir, label, *, timeout):
    xml = Path(evidence_dir) / f"{label}.junit.xml"
    receipt = run_command(_with_results(framework, command, xml), tree, Path(evidence_dir) / f"{label}.log",
                          timeout=timeout)
    receipt["results"] = per_test_results(framework, receipt, xml)
    return receipt


def baseline(workspace, base, run_dir, *, framework, suite_command, timeout=DEFAULT_TIMEOUT,
             dependencies_from=None) -> dict:
    """Run the suite once on the pristine base revision (cached by the caller)."""
    evidence = Path(run_dir) / "baseline"
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "baseline", workspace, {},
                     dependencies_from=dependencies_from)
    try:
        receipt = run_suite(framework, suite_command, tree, evidence, "suite-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    return {"base": base, "command": suite_command, "receipt": receipt, "health": suite_health(receipt)}


def suite_health(receipt) -> str:
    """passing | failing_tests (some pass) | failing (no per-test detail) | broken | timeout."""
    results = receipt.get("results")
    if receipt["timed_out"]:
        return "timeout"
    if receipt["exit_code"] == 0:
        return "passing"
    if results is None:
        return "failing"
    return "failing_tests" if results["total"] > len(results["failed"]) else "broken"


def verify(workspace, base, run_dir, *, framework=None, suite_command=None, regression_command=None,
           reported=None, base_suite=None, timeout=DEFAULT_TIMEOUT, dependencies_from=None,
           allow_no_test=False) -> dict:
    """Verify the candidate in ``workspace`` against ``base``; see module docstring."""
    workspace, run_dir = Path(workspace), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    before = support.snapshot(workspace)["revision"]
    changes = changed_files(workspace, base)
    tests = [p for p in changes if is_test_path(p)]
    sources = [p for p in changes if not is_test_path(p)]
    test_changes = {p: changes[p] for p in tests}
    fail, unverified, notes = [], [], []
    checks: dict[str, dict] = {}  # command receipts only
    proof: dict = {}
    commands = select_commands(framework, [p for p in tests if changes[p] != "deleted"],
                               suite_command=suite_command, regression_command=regression_command,
                               reported=reported)
    notes += commands["notes"]
    if not changes:
        fail.append("No change: the candidate is identical to the base revision")
    elif not sources:
        fail.append("Only test files changed; a fix must change product code")
    deleted = [p for p in tests if changes[p] == "deleted"]
    if deleted:
        fail.append("Existing test files were deleted: " + ", ".join(deleted))
    removed = removed_python_tests(workspace, base, changes)
    if removed:
        fail.append("Existing tests were removed: " + ", ".join(removed[:20]))
    if not [p for p in tests if changes[p] != "deleted"]:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, so the bug is not shown to be reproduced")

    runnable_tests = [p for p in tests if changes[p] != "deleted"]
    trees = {}
    try:
        if changes and (commands["regression"] or commands["suite"]):
            trees["candidate"] = make_tree(workspace, base, run_dir / "scratch" / "candidate", workspace, changes,
                                           dependencies_from=dependencies_from)
        if trees and sources and runnable_tests:
            trees["base_with_tests"] = make_tree(workspace, base, run_dir / "scratch" / "base-with-tests",
                                                 workspace, test_changes, dependencies_from=dependencies_from)
        # Regression proof: identical tests, base source versus candidate source.
        if trees and runnable_tests and commands["regression"]:
            on_candidate = run_suite(framework, commands["regression"], trees["candidate"], run_dir,
                                     "regression-on-candidate", timeout=timeout)
            checks["regression_on_candidate"] = on_candidate
            on_base = None
            if "base_with_tests" in trees:
                on_base = run_suite(framework, commands["regression"], trees["base_with_tests"], run_dir,
                                    "regression-on-base", timeout=timeout)
                checks["regression_on_base"] = on_base
            _judge_regression(on_candidate, on_base, fail, notes, proof, known_failures=lambda: _pre_existing(
                framework, commands, changes, runnable_tests, workspace, base, run_dir, checks,
                timeout=timeout, dependencies_from=dependencies_from))
        elif "base_with_tests" in trees and commands["suite"]:
            # No targeted command: the whole suite proves the flip when base was green.
            if base_suite is None or base_suite["health"] != "passing":
                unverified.append("No targeted regression command, and the base suite is not green, "
                                  "so a fail-to-pass flip cannot be attributed to the new tests")
            else:
                on_base = run_suite(framework, commands["suite"], trees["base_with_tests"], run_dir,
                                    "suite-on-base-with-tests", timeout=timeout)
                checks["regression_on_base"] = on_base
                if on_base["exit_code"] == 0:
                    fail.append("The new tests pass on the unfixed base code, so they do not reproduce the bug")
        elif tests and sources:
            unverified.append("No command to run the regression tests; pass --regression-command")

        # No regressions: the suite on the candidate, compared with base.
        if "candidate" in trees and sources and commands["suite"]:
            reuse = checks.get("regression_on_candidate") if commands["suite"] == commands["regression"] else None
            on_candidate = reuse or run_suite(framework, commands["suite"], trees["candidate"], run_dir,
                                              "suite-on-candidate", timeout=timeout)
            checks["suite_on_candidate"] = on_candidate
            _judge_suite(on_candidate, base_suite, fail, unverified, notes)
        elif sources:
            unverified.append("No project test command was found; existing behavior was not checked "
                              "(pass --test-command)")
    finally:
        for tree in trees.values():
            remove_tree(workspace, tree)
    after = support.snapshot(workspace)["revision"]
    if after != before:
        unverified.append("The candidate changed while it was being verified; verify again")
    verdict = FAIL if fail else UNVERIFIED if unverified else PASS
    return {"verdict": verdict, "failures": fail, "unverified": unverified, "notes": notes,
            "base": base, "source_revision": before,
            "changes": changes, "test_files": tests, "source_files": sources,
            "stats": diff_stats(workspace, base, changes),
            "commands": {k: commands[k] for k in ("suite", "suite_source", "regression", "regression_source")},
            "framework": framework.to_dict() if framework else None,
            "baseline": ({"health": base_suite["health"], "exit_code": base_suite["receipt"]["exit_code"],
                          "output": base_suite["receipt"]["output"]} if base_suite else None),
            "fail_to_pass": proof.get("fail_to_pass"), "checks": checks}


def _judge_regression(on_candidate, on_base, fail, notes, proof, *, known_failures):
    """Judge the targeted runs. With per-test results, a test that already fails on the
    pristine base (for example one needing a network) in the same file neither blocks a
    correct fix nor counts as its proof; without them, exit codes decide."""
    candidate = on_candidate.get("results")
    base = (on_base or {}).get("results")
    if candidate is not None and not on_candidate["timed_out"]:
        failed = set(candidate["failed"])
        if candidate["total"] == 0:
            fail.append("The regression command ran no tests")
        elif failed:
            known = known_failures()
            unexplained = sorted(failed - (known or set()))
            if unexplained:
                fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
            else:
                notes.append("Tests in the changed files that already fail on base were not counted: "
                             + ", ".join(sorted(failed)[:20]))
        if on_base is not None and base is not None and not on_base["timed_out"]:
            flipped = sorted(set(base["failed"]) - failed)
            proof["fail_to_pass"] = flipped
            if not flipped:
                fail.append("No test fails on the unfixed base code and passes with the fix, "
                            "so the tests do not reproduce the bug")
            return
    elif on_candidate["exit_code"] != 0:
        fail.append("The regression tests fail on the candidate" + (" (timed out)" if on_candidate["timed_out"] else ""))
    if on_base is None:
        return
    if on_base["exit_code"] == 0:
        fail.append("The regression tests also pass on the unfixed base code, so they do not reproduce the bug")
    elif on_base["timed_out"]:
        notes.append("The regression tests timed out on the base code; counted as a failure on base")


def _pre_existing(framework, commands, changes, runnable_tests, workspace, base, run_dir, checks, *,
                  timeout, dependencies_from):
    """Failures of the changed test files' base versions on the pristine base, by test id."""
    if not framework or not framework.per_test or not str(commands["regression_source"]).startswith("derived"):
        return None
    existing = [p for p in runnable_tests if changes[p] == "modified"]
    command = framework.targeted(existing)
    if not command:
        return set()
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "base", workspace, {},
                     dependencies_from=dependencies_from)
    try:
        receipt = run_suite(framework, command, tree, run_dir, "regression-files-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    checks["regression_files_on_base"] = receipt
    return set(receipt["results"]["failed"]) if receipt.get("results") else None


def _judge_suite(on_candidate, base_suite, fail, unverified, notes):
    if on_candidate["timed_out"]:
        fail.append("The project suite timed out on the candidate")
        return
    candidate = on_candidate.get("results")
    base_receipt = (base_suite or {}).get("receipt") or {}
    base_results = base_receipt.get("results")
    if candidate and base_results and candidate["total"] < base_results["total"]:
        fail.append(f"Fewer tests ran on the candidate ({candidate['total']}) than on base "
                    f"({base_results['total']}); tests may have been removed, disabled or not importable")
    if on_candidate["exit_code"] == 0:
        return
    if base_suite is None:
        unverified.append("The project suite fails on the candidate and there is no base run to compare with")
        return
    if base_receipt.get("exit_code") == 0:
        fail.append("The project suite passes on base but fails on the candidate")
        return
    if candidate and base_results:
        new = sorted(set(candidate["failed"]) - set(base_results["failed"]))
        if new:
            fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
        else:
            notes.append(f"{len(base_results['failed'])} test(s) already failed on base; none newly fail")
        return
    unverified.append("The project suite already fails on base and per-test results are unavailable, "
                      "so new failures cannot be ruled out (narrow it with --test-command)")


def feedback(result, *, limit=3000) -> str:
    """A compact, model-readable account of a failed or unverified verification."""
    lines = [f"Verdict: {result['verdict']}"]
    if (result.get("framework") or {}).get("note"):
        lines.append(f"Note: {result['framework']['note']}")
    lines += [f"- FAIL: {reason}" for reason in result["failures"]]
    lines += [f"- UNVERIFIED: {reason}" for reason in result["unverified"]]
    lines += [f"- Note: {note}" for note in result["notes"]]
    if result.get("fail_to_pass"):
        lines.append("Fail-to-pass tests: " + ", ".join(result["fail_to_pass"][:20]))
    commands = result["commands"]
    lines.append(f"Regression command ({commands['regression_source']}): {commands['regression']}")
    lines.append(f"Suite command ({commands['suite_source']}): {commands['suite']}")
    for label, receipt in result["checks"].items():
        lines.append(f"\n## {label}: exit {receipt['exit_code']}{' (timed out)' if receipt['timed_out'] else ''}")
        lines.append(receipt["tail"][-limit:])
    return "\n".join(lines)


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="autocode verify-fix",
                                     description="Model-free check that a bug fix is proven by a regression test")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Checkout holding the candidate fix")
    parser.add_argument("--base", default="HEAD", help="Revision without the fix (default HEAD)")
    parser.add_argument("--test-command", help="Shell command that runs the project suite")
    parser.add_argument("--regression-command", help="Shell command that runs only the new or changed tests")
    parser.add_argument("--python", help="Interpreter for Python projects (default: project venv, then python3)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Seconds per command")
    parser.add_argument("--out", type=Path, help="Directory for logs and verification.json")
    parser.add_argument("--allow-no-test", action="store_true", help="Report UNVERIFIED instead of FAIL without a test")
    args = parser.parse_args(argv)
    workspace = Path(_git(args.workspace, "rev-parse", "--show-toplevel").strip())
    base = _git(workspace, "rev-parse", "--verify", args.base + "^{commit}").strip()
    out = (args.out or Path(tempfile.mkdtemp(prefix="autocode-verify-"))).resolve()
    if out.is_relative_to(workspace) and not out.is_relative_to(workspace / ".autocode"):
        raise ValueError("--out inside the checkout would change the candidate; use a path outside it "
                         "or under .autocode/")
    framework = detect_framework(workspace, python=args.python)
    suite = args.test_command or (framework.suite if framework else None)
    base_suite = (baseline(workspace, base, out, framework=framework, suite_command=suite, timeout=args.timeout,
                           dependencies_from=workspace) if suite else None)
    result = verify(workspace, base, out, framework=framework, suite_command=args.test_command,
                    regression_command=args.regression_command, base_suite=base_suite, timeout=args.timeout,
                    dependencies_from=workspace, allow_no_test=args.allow_no_test)
    support.atomic_json(out / "verification.json", result)
    print(feedback(result, limit=800))
    print(f"\nEvidence: {out / 'verification.json'}")
    return {PASS: 0, UNVERIFIED: 2}.get(result["verdict"], 1)


if __name__ == "__main__":
    raise SystemExit(cli())
