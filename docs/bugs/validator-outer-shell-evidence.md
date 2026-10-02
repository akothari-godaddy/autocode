# Validator reports must identify the executed shell command

A local Luna/Haiku bug-fix trial produced passing tests, but Haiku's Validator
report shortened a wrapped unittest command and assigned an application's exit
code to a different outer shell command. Both report repairs repeated the
mismatch. Exact executed-event validation correctly rejected those reports.

Validator execution guidance now recommends direct checks with the shell tool's
working directory, and asserting subprocess probes for expected error exits.
Reports must retain the entire executed command and its outer event exit code.
Report-only repair guidance requires verbatim saved command, exit and event ID;
it cannot rerun checks or manufacture acceptance. Evidence validation is unchanged.

Focused tests cover execution and repair guidance and its inclusion in actual
stage prompts for both Codex and OpenCode. The local model trial remains separate
from these offline tests; a passing unit test is not a completed live task.
