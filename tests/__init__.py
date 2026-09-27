"""AutoCode test package.

The runtime lives in tools/ and is imported by its top-level module names
(``import autocode``), so this package puts tools/ on sys.path before any
test module loads. Run the gate with ``python3 tools/run_suite.py``.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
for _p in (str(ROOT), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# A handful of tests execute a real shell to prove POSIX quoting semantics
# (Codex/OpenCode wrap recorded commands as `<login-shell> -lc '...'`, and
# same_command() has to match that verbatim). zsh is macOS's default login
# shell and the closest match to what those providers actually record, but
# it isn't installed on every CI runner or contributor machine (notably
# most Linux distros), so fall back to another real POSIX shell rather than
# hard-coding a path that may not exist. All of `-lc` and quoting-error-code
# behavior is standard across zsh/bash/dash for what these tests check.
LOGIN_SHELL = shutil.which("zsh") or shutil.which("bash") or shutil.which("sh")
