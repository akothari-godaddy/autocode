# Scenarios

Realistic engineering tasks for AutoCode, each with an independent **oracle**
that judges the delivered project from the outside. The oracle, not AutoCode's
own completion claim, decides whether the work is right.

```sh
PY=.venv/bin/python                               # AutoCode needs psutil from the project virtualenv
$PY scenarios/run.py list                         # the catalog
$PY scenarios/run.py check                        # prove every oracle (seconds; no AutoCode, no models)
$PY scenarios/run.py run --fake                   # every scenario through AutoCode with a scripted model (under a minute, no spend)
$PY scenarios/run.py run bugfix-iso-weeks --profile glm53-mimo --i-authorize-live-model-spend
$PY -m unittest scenarios/test_harness.py         # the harness's own tests (under a minute)
```

Results land in `.scenario-runs/<time>-<id>-<mode>/`: `result.json` (verdict,
every oracle check, CLI calls, answers given on the user's behalf, stages,
model time and tokens), `steps.jsonl`, the final `state.json`, and the
delivered `project/`, kept for inspection.

## Three levels

| Level | Command | What it proves | Cost |
| --- | --- | --- | --- |
| Oracle check | `check` | The oracle rejects the untouched seed, accepts the reference solution, and rejects each plausible-but-wrong variant in `broken/`. | seconds |
| Fake run | `run --fake` | AutoCode's real CLI, planning gates, approval, build, validation and completion work end to end for this kind of task. The scripted model plans from the brief and applies the reference solution. It says nothing about model quality. | seconds per scenario |
| Live run | `run --profile NAME` | How well AutoCode actually does the task with real models. | model spend; requires `--i-authorize-live-model-spend` |

`run --fake --fake-solution broken/<name>` makes the scripted model deliver a
wrong solution. Because its own checks pass, AutoCode completes, and the
harness must report `FALSE_COMPLETE`. That is how the harness itself is tested.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | AutoCode reported completion and every oracle check passed. |
| `FALSE_COMPLETE` | AutoCode reported completion but the oracle found failures. The worst outcome. |
| `HONEST_BLOCKER` | AutoCode stopped (paused, waiting for a person) without claiming completion. The oracle summary shows how far the work got. |
| `ERROR` | The harness could not finish (budget used up, no progress, a CLI crash) or the oracle crashed. |
| `SKIPPED` | A required tool is missing, or the scenario does not support the requested mode. |

The driver answers AutoCode's clarifying questions with AutoCode's own proposed
default and records each answer in `result.json`. It approves the plan it is
shown and accepts requested human reviews. It never writes AutoCode state and
does not resume paused runs: a pause is reported as `HONEST_BLOCKER`.

## Catalog

| Scenario | Category | What it exercises |
| --- | --- | --- |
| `bugfix-iso-weeks` | bugfix | Root-causing a reported symptom in a different module; hidden tests cover every day from 2000 to 2030, so a special-case fix fails. |
| `feature-timesheet-by-project` | feature | Adding an option to an existing CLI without changing existing output. |
| `greenfield-greeting-cli` | greenfield | A small CLI from an empty repository. |
| `greenfield-todo-cli` | greenfield | Durable state and failure cases that must not corrupt data. |
| `port-policy-go` | port | Porting C# to Go against golden vectors. Requires `go`. |
| `parallel-diamond` | parallel | Four milestones where two can be built in parallel. Live only for now. |

Planned next: architecture-only design tasks, Figma design → implementation,
and multi-service systems started with `docker compose` and checked end to end.

## Adding a scenario

```
catalog/<id>/
  scenario.toml     title, category, optional requires = ["go"], [fake] check = "...", [run] budgets
  brief.md          the request, exactly as a user would type it (plain text, no headings)
  seed/             the starting project, committed before the run (omit for an empty repo)
  oracle.py         def check(project, scenario) -> list[Check]
  reference/        files that, laid over the seed, make a correct solution
  broken/<name>/    plausible solutions with one real defect each
  hidden/           tests only the oracle sees; never copied into the workspace
```

Rules for oracles, so a verdict means something:

- Judge through the documented interface (CLI, HTTP, public functions), so a
  correct solution with a different internal structure still passes.
- Work on a copy (`scratch_copy`) and never modify the delivered project.
- Import only `harness.oracle` and the standard library, never AutoCode.
- Every scenario has a reference solution and at least one broken variant, and
  `check` must show the oracle telling them apart. `test_harness.py` enforces this.
- For changes to an existing Python project, `python_change_checks` gives the
  standard checks: project tests pass, hidden tests pass, existing tests kept,
  standard library only, and the delivered tests fail against the original code.

## Relation to older harnesses

`tools/live_trial.py` and `tools/live_scenarios.py` hold the scenarios this
catalog was ported from (LIVE-01, 02, 05, 06). They stay until the work in
progress on them lands; then they can be removed. LIVE-07 depends on a separate
local repository and was not ported. `test-scenarios/` is a different suite:
fault injection (crash and resume, budget exhaustion, dirty workspaces) against
a fake Codex, and has not been migrated yet.
