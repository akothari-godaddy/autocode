"""Materialize a scenario's starting project as a fresh Git repository."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .oracle import IGNORED

GIT_IDENTITY = ["-c", "user.name=Scenario", "-c", "user.email=scenario@example.test"]


def git(project: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(project), *GIT_IDENTITY, *args],
                          check=True, capture_output=True, text=True).stdout


def materialize(seed: Path, project: Path, *overlays: Path) -> Path:
    """Copy and commit the seed, then lay overlays on top without committing them."""
    if seed.is_dir():
        shutil.copytree(seed, project, ignore=IGNORED)
    else:
        project.mkdir(parents=True)
    git(project, "init", "-q", "-b", "main")
    git(project, "add", "-A")
    git(project, "commit", "-q", "--allow-empty", "-m", "Scenario seed")
    for overlay in overlays:
        shutil.copytree(overlay, project, dirs_exist_ok=True, ignore=IGNORED)
    return project


def overlay_paths(overlay: Path) -> list[str]:
    return sorted(str(path.relative_to(overlay)) for path in overlay.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts)
