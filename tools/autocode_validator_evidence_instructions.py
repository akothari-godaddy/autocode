"""Explain the exact shell evidence contract without changing its enforcement."""
from __future__ import annotations


def instruction(*, report_only: bool = False) -> str:
    """Guide execution or report-only repair using the same outer-event semantics."""
    if report_only:
        return (
            "Validator report repair: Copy command, exit_code and evidence_ref verbatim "
            "from the matching original_executed_checks row. That exit_code belongs to the "
            "outer shell event, not an application launched inside it. Keep the entire "
            "command including cd, redirections, pipelines, tee, echo and heredocs. Do not "
            "rerun checks, shorten commands, invent event IDs or replace an outer exit with "
            "an inner application result. Unsupported acceptance remains NOT_VERIFIED.\n"
        )
    return (
        "Validator shell evidence: checks[].command must copy the entire outer shell command "
        "from its completed shell event, including cd, redirections, pipelines, tee, echo "
        "and heredocs. checks[].exit_code is the integer exit of that exact outer shell event; "
        "an inner application's return code is a separate observation. Prefer direct checks "
        "with the shell tool's workdir set to workspace instead of adding cd or output wrappers. "
        "Avoid tee or trailing echo that masks a failing check. For expected application errors, "
        "use a unittest or subprocess probe to assert the expected return code, stdout and stderr; "
        "the probe itself must exit nonzero if any assertion fails. Copy the completed event ID "
        "exactly. Preserve failed exploratory attempts in checks_run and logs; do not report "
        "an unchecked printout as passing verification.\n"
    )
