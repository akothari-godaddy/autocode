"""The scenario catalog: one directory per scenario under ``scenarios/catalog/``.

    <id>/scenario.toml   title, category, requirements, fake-mode check, budgets, expected outcome
    <id>/brief.md        the request given to AutoCode, verbatim
    <id>/seed/           starting project, committed before the run (optional)
    <id>/oracle.py       check(project, scenario[, run]) -> list[Check]
    <id>/reference/      overlay that makes a correct solution (optional)
    <id>/broken/<name>/  overlays that look plausible but are wrong (optional)
    <id>/hidden/         files only the oracle sees (optional)
"""
from __future__ import annotations

import importlib.util
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "catalog"
# The kind of engineering job. The first eight change or create code; the last
# four are read-only jobs whose deliverable is a report (README, "Workflows").
CATEGORIES = ("bugfix", "feature", "greenfield", "port", "parallel", "architecture", "figma", "system",
              "review", "design", "discuss", "investigate")
# How a correct run ends: with completion, with a stop (a blocker or a question
# the user must answer), or either.
EXPECTED = ("complete", "stop", "any")
KEYS = {"title", "category", "requires", "fake", "run"}
RUN_KEYS = {"max_steps", "timeout_minutes", "expected", "known_failure"}
FAKE_KEYS = {"check", "flags", "fault", "live_investigator"}


@dataclass(frozen=True)
class Scenario:
    id: str
    dir: Path
    title: str
    category: str
    brief: str
    requires: tuple[str, ...]
    fake_check: str | None
    max_steps: int
    timeout_minutes: int
    expected: str = "complete"
    # Why AutoCode is known not to pass this scenario yet. A verdict other than
    # PASS is reported but does not fail the suite; a PASS says to remove the key.
    known_failure: str = ""
    # [fake] extras: CLI flags added to the scripted run, a named scripted fault
    # (scenarios/harness/fake_codex.py), and whether the scripted run still makes a
    # real model call (then it needs --i-authorize-live-model-spend, or is skipped).
    fake_flags: tuple[str, ...] = ()
    fake_fault: str = ""
    fake_live_calls: bool = False

    @property
    def seed(self) -> Path:
        return self.dir / "seed"

    @property
    def reference(self) -> Path | None:
        path = self.dir / "reference"
        return path if path.is_dir() else None

    @property
    def broken(self) -> list[Path]:
        root = self.dir / "broken"
        return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []

    def missing_tools(self) -> list[str]:
        return [tool for tool in self.requires if shutil.which(tool) is None]

    def oracle(self):
        spec = importlib.util.spec_from_file_location(f"oracle_{self.id.replace('-', '_')}", self.dir / "oracle.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check


def load(scenario_id: str) -> Scenario:
    root = CATALOG / scenario_id
    if not (root / "scenario.toml").is_file():
        known = ", ".join(path.name for path in _dirs())
        raise ValueError(f"unknown scenario {scenario_id!r}; known: {known}")
    meta = tomllib.loads((root / "scenario.toml").read_text())
    unknown = set(meta) - KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown keys {sorted(unknown)}")
    if meta.get("category") not in CATEGORIES:
        raise ValueError(f"{scenario_id}: category must be one of {CATEGORIES}")
    run = meta.get("run", {})
    unknown = set(run) - RUN_KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown [run] keys {sorted(unknown)}")
    if run.get("expected", "complete") not in EXPECTED:
        raise ValueError(f"{scenario_id}: [run] expected must be one of {EXPECTED}")
    fake = meta.get("fake", {})
    unknown = set(fake) - FAKE_KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown [fake] keys {sorted(unknown)}")
    return Scenario(
        id=scenario_id, dir=root, title=meta["title"], category=meta["category"],
        brief=(root / "brief.md").read_text().strip(), requires=tuple(meta.get("requires", ())),
        fake_check=meta.get("fake", {}).get("check"), max_steps=run.get("max_steps", 40),
        timeout_minutes=run.get("timeout_minutes", 60), expected=run.get("expected", "complete"),
        known_failure=run.get("known_failure", ""), fake_flags=tuple(fake.get("flags", ())),
        fake_fault=fake.get("fault", ""), fake_live_calls=bool(fake.get("live_investigator", False)))


def load_all() -> list[Scenario]:
    return [load(path.name) for path in _dirs()]


def _dirs() -> list[Path]:
    return sorted(path for path in CATALOG.iterdir() if (path / "scenario.toml").is_file())
