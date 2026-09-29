"""Freeze runner-owned mutable state when a stage cites it as evidence."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile


def stable_path(path: Path, run_dir: Path) -> Path:
    """Return an immutable, content-addressed copy of this run's state file."""
    run_dir = Path(run_dir).resolve()
    if path != run_dir / "state.json":
        return path
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    directory = run_dir / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / f"run-state-{digest}.json"
    if not saved.exists():
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".run-state-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, saved)
    if saved.read_bytes() != data:
        raise ValueError("Frozen run-state evidence differs from its content hash")
    return saved
