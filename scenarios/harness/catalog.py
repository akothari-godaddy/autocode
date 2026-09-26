"""The scenario catalog: one directory per scenario under ``scenarios/catalog/``.

    <id>/scenario.toml   title, category, requirements, fake-mode check, budgets
    <id>/brief.md        the request given to AutoCode, verbatim
    <id>/seed/           starting project, committed before the run (optional)
    <id>/oracle.py       check(project, scenario) -> list[Check]
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
CATEGORIES = ("bugfix", "feature", "greenfield", "port", "parallel", "architecture", "figma", "system")
KEYS = {"title", "category", "requires", "fake", "run"}


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
    return Scenario(
        id=scenario_id, dir=root, title=meta["title"], category=meta["category"],
        brief=(root / "brief.md").read_text().strip(), requires=tuple(meta.get("requires", ())),
        fake_check=meta.get("fake", {}).get("check"), max_steps=run.get("max_steps", 40),
        timeout_minutes=run.get("timeout_minutes", 60))


def load_all() -> list[Scenario]:
    return [load(path.name) for path in _dirs()]


def _dirs() -> list[Path]:
    return sorted(path for path in CATALOG.iterdir() if (path / "scenario.toml").is_file())
