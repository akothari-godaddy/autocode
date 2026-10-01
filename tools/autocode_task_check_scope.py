"""Positive executable-target scope for tasks inside a reviewed delivery slice.

This is not a shell sandbox or an English-intent classifier. A task cannot use
an unreviewed verification target merely by paraphrasing its objective or by
adding presentation flags to its command. New targets require reviewed slice
refinement; narrow unittest selections from an authorized target remain valid.
"""
import re
import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_progressive_plan as rules
    from . import autocode_verification_plan as verification
except ImportError:
    import autocode_progressive_plan as rules
    import autocode_verification_plan as verification


def command_target(command):
    words = shlex.split(command)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", PurePosixPath(words[0]).name):
        words[0] = "python"
    unittest = words[:3] == ["python", "-m", "unittest"]
    presentation = ("-v", "-q", "--verbose", "--quiet")
    # Without parsing option arity, an argument may be a filter value, not a
    # test name. Preserve option-bearing commands rather than broaden them.
    if (unittest and "discover" not in words[3:]
            and not any(word.startswith("-") and word not in presentation for word in words[3:])):
        words = words[:3] + [word for word in words[3:] if word not in presentation]
        words[3:] = [word.removeprefix("./").removesuffix(".py").replace("/", ".")
                     for word in words[3:]]
    return tuple(words)


def _covered(target, allowed):
    if target == allowed:
        return True
    if (target[:3] == allowed[:3] == ("python", "-m", "unittest")
            and "discover" not in target[3:] and "discover" not in allowed[3:]
            and target[3:] and allowed[3:]
            and not any(word.startswith("-") for word in target[3:] + allowed[3:])):
        return all(any(word == parent or word.startswith(parent + ".") for parent in allowed[3:])
                   for word in target[3:])
    return False


def require_reviewed_targets(methods, checks):
    allowed = {command_target(command) for check in checks
               for command in rules.check_commands(check)}
    if not methods:
        raise ValueError("progressive task requires an explicit reviewed validation target")
    for method in methods:
        commands = verification.commands(method)
        if not commands:
            raise ValueError("progressive task validation needs an explicit reviewed command")
        for command in commands:
            rules.check_commands({"id": "task-scope", "method": command})
            target = command_target(command)
            if not any(_covered(target, current) for current in allowed):
                raise ValueError("task validation target is not authorized by the active reviewed slice: " + command)
