# Bug 005: distinct failures counted as one loop; a retry could carry an earlier out-of-scope edit

**Found:** 2026-09-29, while AutoCode built the dashboard (milestone MF1)

## 1. Different failures looked like one repeated failure

`autocode_failures` grouped failures by stage, source revision and exception class, and
its count never reset. Almost every rejected report raises `ValueError`, and planning
stages are read-only, so the source never changes during planning. "Missing response to
concern P1", "... P2" and "Malformed test receipt P3" shared one entry, and the third
paused the run as `PAUSED_REPEATED_FAILURE` while the loop was still producing new
information. `failures.repeated(state, {stage, source_revision})` also ignored the error
class, and it gates Resolver diagnosis (`autopilot.py`) and completion recovery.

Fix: the ledger keeps every attempt, grouped as before, and also records each attempt's
signature (error text with only per-attempt paths and long digests normalized, plus the
kind of saved output) and the length of the current run of consecutive identical failures
for that stage and source. A success, a different error or a changed source ends the run.
The repeated-failure decision reads that run length (`failures.stalled`). Ledger entries
saved before this change keep their old cumulative count.
Tests: `tests/test_failures.py`.

## 2. A retry could carry an earlier out-of-scope edit to validation

The serial Builder's assignment gate checked only the current attempt's own tree delta.
A rejected attempt's out-of-scope edit stays in the tree for inspection, so the next
attempt passed the gate if it changed nothing, or only files inside its assignment; the
second case went straight to the Validator with the out-of-scope file still in place.
Partial work kept by timeout and capacity recovery was never gated at all.

Fix: `autocode_assignment` compares the tree the attempt left with the before-snapshot of
the task's earliest Builder attempt, and every changed or deleted path must be inside the
assignment. An earlier attempt whose snapshot cannot be read pauses the run rather than
counting as an empty delta. Restoring the stray file lets the next attempt through. The
saved no-progress route to validation (`recover_retained_candidate`) uses the same check.
Tests: the `test_serial_retry_*` cases in `tests/test_assignment_scenarios.py`.
