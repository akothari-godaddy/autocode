# Stress battery findings, 2026-09-29

A fault-injection battery was run against the main checkout at `5cfeecc1`
(branch `claude/bold-edison-xjaciz`, then 77 commits behind master, with other
sessions' uncommitted edits in `tools/`). It used `test-scenarios/lib.sh` and a
garbage-injecting Codex shim; offline fixture only, no model spend. Eleven probes:
corrupted `state.json` (truncated, garbage, missing key), concurrent double resume,
hung provider against `--max-stage-seconds`, garbage provider output (invalid file,
empty file, stdout flood), SIGKILL with a dirty workspace, SIGTERM mid-stage, a
unicode-and-space workspace path, a deleted run dir, a missing run dir.

**Caveats.** The battery script lived in `/tmp/autocode-stress/` and was not
committed, so none of this can be re-run as it was. Finding 1 below means the
harness's default entry ran a different checkout's code, and the battery did not
record which entry each probe used. Treat the "held up" results as unconfirmed for
master until the probes are ported and rerun with the corrected `lib.sh`.

## The runner held up

Every corruption or fault path refused honestly rather than claiming completion:

- Truncated or garbage `state.json`: `--status` and `--resume-paused` both fail with
  a clean parse error, no new run dir, no provider launch.
- Two simultaneous `--resume-paused` on one run dir: exactly one proceeds, the other
  is refused explicitly, and the stage history stays a stable prefix.
- A provider that hangs forever: `--max-stage-seconds 20` fired, no hang.
- Garbage provider output (invalid or empty): the failure is recorded,
  `PAUSED_PROVIDER_UNCERTAIN` / `WAITING_FOR_USER` with an explicit remedy
  (`--abandon-stage 001/terra-01`). No false completion, no raw tracebacks in ~20 logs.
- SIGTERM mid-stage: the driver exits, the provider is reaped, state stays atomic,
  and resume pauses for reconciliation ("uncertain stage must be inspected, never
  automatically replayed").
- Unicode + space workspace path: the build completes and the artifact is correct.
- Missing run dir: clean nonzero rejection.

## Findings

### 1. The installed `autocode` command runs whichever checkout it was last installed from

**Status:** harness fixed (this change); the install itself is still a hazard.

`~/.local/bin/autocode` is a pipx editable install. Its `autocode_cli` package
resolved to `~/.codex/worktrees/protect-provider-event-logs/autocode/tools` when the
battery ran, and earlier the same day to `~/.codex/worktrees/install-latest-master/`.
It moves whenever someone reinstalls from another checkout. `test-scenarios/lib.sh`
preferred that command and its comment claimed it was "an editable mapping to this
checkout", so the suite silently tested other code.

Symptom: scenario-03 with the default entry wedged its fixture at
`PAUSED_INVALID_OUTPUT`. The foreign checkout had `autocode_check_replay.py`, which
re-runs the Validator's cited checks as shell, and the old checkout's
`tools/fake_codex.py` cited `fixture:` pseudo-commands. On master the fixture cites
real commands, so that particular wedge no longer applies; the mismatch does.

The hazard reaches live runs too: the hierarchical-planning run was resumed through
the installed command on 2026-09-29 (`activity.jsonl` records `program: "autocode"`
invocations at 18:00 UTC), so it executed whatever the install pointed at, not the
checkout its operator believed.

`lib.sh` now uses the installed command only when its `autocode_cli` resolves to this
checkout's `tools/`; otherwise it runs `tools/autocode.py` with the repo's `.venv`
interpreter (which has psutil), else `python3`. It prints the chosen entry on stderr.
`AUTOCODE_BIN` still overrides. Reinstalling pipx from the checkout you mean to test
remains the operator's job.

### 2. `state.json` is not schema-checked on load

**Status:** open, low severity; belongs with the planned typed `RunState`
(`AGENTS.md`, architecture rule 4).

Truncation and invalid JSON are refused, but a syntactically valid file missing the
`stages` key is accepted and the run re-plans over partial history. It pauses
honestly at the next question, so there is no false completion, but there is no
refusal either. Hand-crafted corruption only.

### 3. Deleting a live run dir does not stop the run

**Status:** open; probably intended, and part of the missing liveness tracking.

`atomic_json` creates the parent directory on every state write, so the driver
recreates a deleted run dir and keeps building. The supported ways to stop a run are
a queued pause (`autocode intervention submit --kind pause`) or killing the driver.
The run dir is not the source of truth for liveness; nothing is. The same gap lets a
dead runner leave `state.json` saying `RUNNING` indefinitely.

### 4. `latest_run` in `test-scenarios/lib.sh` broke on paths with spaces

**Status:** fixed (this change).

`ls -t $hits` split the unquoted list on whitespace, so the unicode-path probe got a
false FAIL from the harness rather than from AutoCode. It now uses `find -print0 |
xargs -0 ls -t`.

### 5. The harness's answer step predates `--resolver-token`

**Status:** open (found while verifying this change).

`scenario-01-ambiguous-brief.sh` answers the fixture's question with a bare
`--answer`, which master rejects: "Answers require the current --resolver-token
shown by AutoResolver". The scenario therefore ends at REVIEW ("did not reach
AWAITING_GOAL_APPROVAL after answers") with either version of `lib.sh`. The
`test-scenarios/` answer and approval steps need the token dance that
`scenarios/harness/driver.py` already does.

## Not covered

`astra_plan` garbage (the fault never fired: the probe run pauses at questions before
planning), disk-full during state writes, and live-model behavior. The
state-corruption and concurrent-resume probes are worth porting into
`test-scenarios/` if they should become routine; until then the results above are a
one-off.
