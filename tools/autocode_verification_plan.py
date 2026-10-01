"""Executable commands and prerequisites from an approved verification plan.

Natural-language methods stay with the Validator. Commands are plain shell
commands or backtick snippets explicitly requested for execution. Quoted
documentation examples are not commands. Imports only the standard library.
"""
from pathlib import Path, PurePosixPath
import re
import shlex

# Plain text (no backticks) is a command only when all of it is one: prose after a command makes the whole
# method prose, left to the Validator. Live bugfix-trivial runs (Claude models, 2026-09-30) approved
# "python3 -m unittest -v passes; Validator reads the diff" and "Run python3 -m unittest -v via capture and
# read the diff"; the runner replayed each sentence as a shell command, it could never pass, and the run paused.
PROSE = re.compile(r"[;,]|(?:^|\s)(?:and|or|then|via|passes|pass|reads?|should|must|the|with|using|while|which|"
                   r"that|confirms?|verif(?:y|ies)|inspects?|shows?|prints?|outputs?|returns?)(?=\s|$)", re.IGNORECASE)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
# `python3 -c doing a topological sort` is prose: the code after -c is one quoted argument, and unquoted code
# followed by more words is a sentence. A live architecture run approved it as AC3's method, the runner replayed
# it as a command (NameError), and the run paused (2026-09-30); another wrote `python3 -c: Kahn topological sort`.
UNQUOTED_CODE = re.compile(r"\s-c:|\s-c\s+[^\s'\"]\S*\s+\S")

RUNNERS = frozenset({"pytest", "npm", "npx", "yarn", "pnpm", "go", "cargo", "ruby", "bundle",
                     "node", "deno", "bun", "uv", "make", "cmake", "ctest", "dotnet", "mvn", "gradle",
                     "sh", "bash"})


def executable(text):
    text = text.strip()
    try:
        words = shlex.split(text)
    except ValueError:
        return False
    if not words:
        return False
    name = PurePosixPath(words[0]).name
    if name in ("sh", "bash") or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name):
        return not UNQUOTED_CODE.search(text)
    return name in RUNNERS


def commands(method):
    text = str(method).strip()
    snippets = list(re.finditer(r"`([^`]+)`", text))
    if snippets:
        result, previous = [], 0
        for snippet in snippets:
            prefix = text[previous:snippet.start()].strip()
            requested = (not prefix and not result) or bool(re.search(
                r"\b(?:runs?|executes?|invokes?)\s*$", prefix, re.IGNORECASE))
            requested |= bool(result) and prefix.lower() in ("and", ",", ", and")
            command = snippet.group(1).strip()
            if requested and executable(command):
                result.append(command)
            previous = snippet.end()
        return result
    if text.lower().startswith("run "):
        text = text[4:].strip()
    return [text] if executable(text) and not PROSE.search(QUOTED.sub("", text)) else []


def approved_commands(state):
    body = (state.get("goal_contract") or {}).get("body") or {}
    task = state.get("current_task") or {}
    ids = set(task.get("acceptance_criteria") or [])
    methods = list(task.get("validation_plan") or [])
    methods += [row.get("verification_method", "") for row in body.get("acceptance_criteria") or []
                if not row.get("human_review") and (not ids or row.get("id") in ids)]
    return list(dict.fromkeys(command for method in methods for command in commands(method)))


def package_markers(command):
    """Markers required by unittest discovery with an explicit top-level directory."""
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    if not any(words[i:i + 2] == ["-m", "unittest"] and "discover" in words[i + 2:]
               for i in range(len(words) - 2)):
        return []
    options = {"start": ".", "top": None}
    for i, word in enumerate(words):
        for key, flags in (("start", ("-s", "--start-directory")), ("top", ("-t", "--top-level-directory"))):
            if word in flags and i + 1 < len(words):
                options[key] = words[i + 1]
            for flag in flags:
                if word.startswith(flag + "="):
                    options[key] = word[len(flag) + 1:]
                elif len(flag) == 2 and word.startswith(flag) and word != flag:
                    options[key] = word[2:]
    if options["top"] is None:
        return []
    start, top = PurePosixPath(options["start"]), PurePosixPath(options["top"])
    if start.is_absolute() or top.is_absolute() or ".." in start.parts or ".." in top.parts:
        return []
    try:
        relative = start.relative_to(top)
    except ValueError:
        return []
    return [str(top.joinpath(*relative.parts[:i], "__init__.py")) for i in range(1, len(relative.parts) + 1)]


def require_scaffolding(workspace, paths, methods):
    """Refuse an impossible assignment before approval; never expand its scope."""
    if not workspace:
        return
    for method in methods:
        for command in commands(method):
            for marker in package_markers(command):
                if (Path(workspace) / marker).is_file():
                    continue
                if any(marker == root.rstrip("/") or marker.startswith(root.rstrip("/") + "/") for root in paths):
                    continue
                raise ValueError(f"Verification command `{command}` requires {marker}, which does not exist and "
                                 "is outside affected_paths. Assign the package marker explicitly or use a test "
                                 "command compatible with the approved scope before asking for approval.")
