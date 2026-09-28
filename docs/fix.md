# Fix a bug report: `autocode fix`

[← Back to README](../README.md)

`autocode fix` takes one bug report and returns a local branch with a fix, a
regression test, a patch and pull-request text. The runner decides whether the
fix is proven by **executing tests**, not by reading a model's report, so a
typical fix costs one or two model calls instead of the nine or more that the
full plan → approve → build → validate → complete loop needs.

```sh
# A GitHub issue (URL, owner/repo#N, or #N resolved through the origin remote)
autocode fix https://github.com/owner/repo/issues/123 --workspace /path/to/clone

# A written report
autocode fix "Parsing an empty config file raises KeyError instead of using defaults"
autocode fix --issue-file bug.md
```

Nothing is pushed, merged or commented anywhere. The command prints the branch,
patch and PR text; publishing them is your decision.

## When to use it, and when not

Use `fix` when the request is a defect with observable wrong behavior: a
crash, a wrong result, a missed validation, a regression. The contract is
narrow: reproduce with a test, fix the root cause, break nothing else.

Use the normal `autocode "…"` workflow for new features, behavior changes that
need a product decision, architecture work, or anything where "what should
happen" is not already clear from the report. `fix` stops with `NEEDS_INPUT`
instead of guessing when the Builder reports that the bug does not reproduce or
needs a decision.

The job type is chosen by the command you run. `fix` does not reinterpret the
workflow mode, task-lane mode or Figma routing of the main runner, and it never
changes a saved run of the main workflow.

## What happens

```text
report ──> baseline ──> Builder ──> verify ──> [Reviewer] ──> branch + patch + PR text
             (runner)   (1 call)    (runner)    (0–1 call)
                           ^           │             │
                           └─ failure ─┴── blocking ─┘   bounded by --max-attempts
```

1. **Intake.** A GitHub issue is fetched with its comments through the REST API
   (`GITHUB_TOKEN` or `GH_TOKEN` is used when set; needed for private
   repositories and rate limits). Issue text is third-party input: it reaches
   the model as quoted data with an instruction to ignore embedded requests.
2. **Isolation.** A new worktree and branch `autocode/fix-<issue>-<id>` are
   created from `--base` (default `HEAD`) under `.autocode/worktrees/`. Your
   checkout's files are not modified; `/.autocode/` is added to its local
   `.git/info/exclude` so run records never reach a `git add -A`. Ignored
   dependency directories (`node_modules`, `.venv`, `venv`) in the checkout are
   linked into the worktree so tests can run; they are never part of the fix.
   The worktree's own code (and its `src/`) goes first on `PYTHONPATH`, so an
   editable install of your checkout cannot shadow it. `--in-place` works in a
   clean checkout instead and leaves the change uncommitted.
3. **Baseline, before any model call.** The project's test command runs on the
   base revision. If it cannot run at all (the command is not found, no test
   passes, a pytest or unittest run reports no results, or it times out), the
   run stops with `ENV_BROKEN` and no model spend.
   Pre-existing failing tests are recorded and passed to the Builder as "not
   yours to fix".
4. **Builder (one call).** One agent turn investigates, writes a regression
   test that fails for the reported reason, fixes the root cause with a minimal
   diff, and runs the tests. It reports a short diagnosis (observed behavior,
   reproduction, root cause, affected paths, the invariant the fix upholds).
5. **Verification (no model).** See [below](#how-a-fix-is-verified). A failure
   becomes the next Builder prompt verbatim. When the provider supports
   sessions, the repair resumes the same session and sends only the feedback.
6. **Reviewer (optional, read-only).** An independent model, by default the
   engine's Validator model and so a different model from the Builder, reads
   the report, the diff and the executed results. It looks for what tests cannot show: symptom-only fixes,
   the same bug left in related paths, unintended behavior or API changes.
   A review blocks unless its verdict is `APPROVE` and it names no critical or
   high finding (severity is read case-insensitively); blocking findings go
   back to the Builder. Once a Reviewer has spoken, every later candidate is
   reviewed again, however small. If the Reviewer changes any file, the run
   stops with `ERROR`.
7. **Result.** On `READY` the fix branch gets one commit, built from the base
   revision with exactly the verified files (anything else the Builder staged
   or committed is left out) and without running commit hooks, so the commit
   is the tree that was verified. In every case with a change, `patch.diff`
   (byte-exact) and `PR.md` are written to the run directory, and the
   verification evidence is kept. If a later attempt breaks a candidate that
   the runner had already proven, the worktree is put back to the proven one,
   re-verified, and reported as `NEEDS_REVIEW`. Any runner failure ends in a
   recorded `ERROR`, never a run left `RUNNING`.

### Cost controls

| Control | Default | Effect |
| --- | --- | --- |
| `--max-attempts N` | 3 | Builder calls, including repairs. |
| `--escalate-model M` | none | Use a stronger model only for the final attempt, and only after a failure. The escalated attempt starts a fresh session. |
| `--review auto\|always\|never` | `auto` | `auto` skips review only for a tiny code change in one source file (≤ 15 changed lines) whose regression is proven by named tests, with no non-code or binary files changed and no earlier review in the run; a human reads it in the PR anyway. |
| `--stage-timeout`, `--idle-timeout` | 1800 s, 600 s | Per model call; a timeout is never success. Work left in the tree is still verified. |
| `--test-timeout` | 900 s | Per test command. |
| `--dry-run` | off | Load the issue and run the baseline without calling a model. |

A malformed or missing Builder report does **not** trigger a report-repair
call. The runner verifies the workspace directly, using detected commands.

Every model call is recorded with its role, model, duration and token usage,
and the summary line reports totals:

```text
Cost: 2 model call(s) {'builder': 1, 'reviewer': 1}; model 212.4s, verification 9.8s, wall 231.0s; tokens in=… out=…
```

## How a fix is verified

The verifier (`tools/autocode_verify.py`) never executes in the Builder's
workspace. It assembles two scratch worktrees from the recorded diff:

| Tree | Source | Tests |
| --- | --- | --- |
| candidate | base + all changes | candidate |
| base-with-tests | base only | candidate's new and changed test files |

* **Regression proof (fail-to-pass).** The new or changed test files must
  **fail** in base-with-tests and **pass** in candidate. The test code is
  identical in both trees, so the difference comes from the fix. With per-test
  results, at least one named test must flip, and those tests are recorded as
  `fail_to_pass`. A test in the same files that already fails on the pristine base
  (for example one that needs network access) is reported but does not block the
  fix or count as its proof. Any other failure on the candidate does block it.
  A test counts only if it ran: a module that fails to import on base (for
  example because the test imports a name the fix adds) is not a reproduction,
  and a skipped or deselected test is not a pass.
* **No regressions (pass-to-pass).** With per-test results (pytest via JUnit
  XML, unittest via its verbose output), every test that passed on base must
  pass on the candidate: not fail, and not be skipped, deselected, renamed or
  missing. A pytest or unittest run that reports no results at all (for example
  because the process exited early with status 0) is `UNVERIFIED`. Without
  per-test results the exit code decides when base was green, and the outcome
  is `UNVERIFIED` when it was not.
* **Weaker proof is flagged.** When the regression proof rests on exit codes
  instead of named tests, or non-code or binary files changed, the verification
  lists why a person or the Reviewer must read the change, and `--review auto`
  reviews it.
* **Test integrity.** A candidate is rejected if it deletes existing test files,
  removes existing Python `def test*` functions, changes only tests, or adds no
  test. `--allow-no-test` downgrades a missing test to `UNVERIFIED`; it never
  makes it `READY`.
* **Binding.** The candidate snapshot is hashed before and after verification.
  A verdict belongs to exactly one candidate.

Test files are recognized by name: `test_*.py`, `*_test.py`, `conftest.py`,
`*_test.go`, `*.test.[jt]s(x)`, `*.spec.[jt]s(x)`, `*.snap`, `*_spec.rb`,
`FooTest.java` or `TestFoo.java` (case-sensitive, so `Latest.java` is product
code), and by the locations listed under test commands below.

### Test commands

| Project | Suite | Regression (changed tests only) |
| --- | --- | --- |
| pytest (configured and importable) | `python -m pytest -q` | `python -m pytest -q <files>` |
| unittest | `python -m unittest discover -v` | `python -m unittest -v <files>` |
| Go | `go test ./...` | `go test <changed test packages>` |
| Jest / Vitest / Mocha | `npm test` (or the runner) | `npx --no-install <runner> <files>` |
| RSpec | `bundle exec rspec` | `bundle exec rspec <specs>` |
| Cargo, `make test`, `npm test` | detected | none: the whole suite proves the flip, which needs a green base |

`--test-command` and `--regression-command` override detection, and `--python`
selects the interpreter (default: the checkout's `.venv`/`venv`, then
`python3`). A Builder-reported regression command is used only when detection
has none and the command names a changed test file, and even then only to
give the Builder feedback: a Builder-chosen command never produces `PASS`, so
such a run ends `UNVERIFIED` unless you pass the command yourself.

Test files by location: `tests/`, `__tests__/`, `__snapshots__/` and `testdata/`
anywhere; `test/` and `spec/` only at the repository root (so `django/test/` and
`numpy/testing/` count as product code); and Maven or Gradle `src/test/`.

The verifier also runs standalone on any checkout, for example a human's or
another agent's fix:

```sh
autocode verify-fix --workspace /path/to/checkout --base origin/main
```

## Statuses

| Status | Meaning | Exit |
| --- | --- | ---: |
| `READY` | Regression proven, no suite regressions, review passed or not required. Committed on the fix branch. | 0 |
| `NEEDS_REVIEW` | Tests prove the fix, but the Reviewer's blocking findings remain after the attempt budget. | 2 |
| `UNVERIFIED` | A patch exists, but the runner could not prove it (reasons recorded). | 2 |
| `NEEDS_INPUT` | The Builder could not reproduce the bug or needs a decision. | 2 |
| `ENV_BROKEN` | The test command does not run on the base revision; no model was called. | 2 |
| `FAILED` | No verified fix within the attempt budget. | 1 |
| `ERROR` | A provider or runner failure; logs retained. | 1 |

`READY` means the checks above passed for that commit. It does not mean the
fix is correct in every case, that the project will accept it, or that you
should merge it without reading it.

## Files

```text
<project>/.autocode/fix/<run-id>/
  fix.json                 status, settings, attempts, every model call with tokens and seconds
  issue.json               the report as fetched
  baseline/                suite on the base revision
  attempts/NN/build.*      prompt, events, report, schema of each Builder call
  attempts/NN/review.*     the same for each Reviewer call
  attempts/NN/verify/      logs of every command the verifier ran
  attempts/NN/verification.json
  patch.diff, PR.md
```

`autocode fix --status <run-dir>` prints a saved run without launching anything.

## Measuring it

The task-type scenarios can be driven through `fix` and scored by the same
independent oracle as the full workflow:

```sh
# Offline harness check: the fixture writes the scenario's reference delivery.
python3 tools/live_trial.py BUGFIX-01 --mode fix --profile fixture

# Authorized live trial; the evidence bundle records cost next to the verdict.
python3 tools/live_trial.py BUGFIX-01 --mode fix --profile glm53-mimo --i-authorize-live-model-spend
```

In fix mode, the profile's Builder and Validator routes become the Builder and
Reviewer. `READY` maps to complete; `NEEDS_*`, `UNVERIFIED` and `ENV_BROKEN`
map to an honest stop.

## Limits, stated plainly

- **No live-model result is recorded yet.** Offline tests cover the loop, the
  verifier's negative controls and the harness. How often a given model reaches
  `READY` on real issues is unmeasured.
- A regression test proves that the reported behavior changed, not that the fix
  is complete or general. The Reviewer and your own review cover the rest.
- The verifier executes the project's tests locally. Like the rest of AutoCode,
  it is for trusted workspaces and is not a sandbox.
- Detection covers the common layouts above. Monorepos, tox/nox matrices, and
  tests that need services or network usually need `--test-command`.
- Flaky tests can flip a verdict. Nothing is retried automatically.
- Fix runs are not yet shown in the dashboard.
