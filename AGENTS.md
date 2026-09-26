# Working on AutoCode

Rules for anyone changing this repository, human or agent. They exist because
the code grew faster than its structure: most of `tools/` now sits in one
import cycle, and three naming schemes describe the same roles. The goal right
now is to make the existing workflow dependable and the code easier to change,
not to add surface area. See `RELIABILITY.md` for product priorities.

## Layout

| Path | What it is |
| --- | --- |
| `tools/` | The application, installed as the `autocode_cli` package. Flat for now. |
| `tools/test_*.py` | Unit and integration tests (`unittest`). |
| `scenarios/` | End-to-end scenario harness and catalog. Black box: it drives the CLI and never imports `tools/`. |
| `test-scenarios/` | Older fault-injection suite (crash/resume, budgets, dirty workspaces) against a fake Codex. |
| `docs/` | User documentation. |

Parked on 2026-09-26: the browser dashboard (`tools/dashboard/`) and the macOS
app (`macos-app/`). Historical audit records (`audits/`) and notes were archived
at the same time. All of it is at tag `archive/pre-restructure-2026-09-26`:
`git checkout archive/pre-restructure-2026-09-26 -- tools/dashboard` restores the
dashboard. Do not add dashboard features until it is unparked.

## Architecture rules

1. **Do not grow the big modules.** `autocode.py`, `autocode_goals.py`,
   `autocode_support.py` and `autopilot.py` have line limits recorded in
   `tools/test_architecture.py`. New behavior goes in a new module with one
   purpose. Lower the recorded limit when you shrink one.
2. **Do not join the import cycle.** 27 modules currently import each other
   through `autocode.py` (listed in `tools/test_architecture.py`). A new module
   must depend only on lower-level modules, never on `autocode`, `autopilot` or
   anything that imports them. Pass what you need as arguments instead.
   Removing a module from the cycle is progress: take it off the list.
3. **Target layering**, from the bottom: utilities (files, hashing, locking,
   schemas) → domain (contract, findings, milestones, completion gate; pure
   functions over state) → runtime (processes, providers) → controller
   (`autopilot`, owns the loop) → interfaces (CLI). Lower layers never import
   higher ones.
4. **Run state is an untyped dict with about 140 keys.** Prefer existing keys.
   If you must add one, write it in one place and document what reads it. A
   typed `RunState` is planned.
5. **Anything that works across tasks** (architecture, multi-component builds,
   integration, deployment) goes in a new layer that drives task runs through
   the CLI (start, status, answer, approve, resume). It must not import
   `autocode.py` internals.

## Names

The code still uses internal stage names. Until they are renamed, this is the
mapping (the unit column is `autopilot.unit_for`):

| In code | Role in docs | Unit |
| --- | --- | --- |
| `requirements_gather` | Requirements | AutoPlanner |
| `astra_discovery`, `glm_revise` | Planner | AutoPlanner |
| `astra_challenge`, `astra_finalize` | Plan Reviewer | AutoPlanner |
| `orchestrator` | parallel milestone scheduling (no model) | AutoCode build unit |
| `astra_plan` | next-task planning | AutoCode build unit |
| `terra` | Builder | AutoCode build unit |
| `sol` | Validator | AutoReview |
| `astra_review`, `astra_checkpoint` | Completion Owner | AutoReview |
| `astra_resolve` | AutoResolver | AutoResolver |

CLI model flags follow the code names: `--astra-model`, `--glm-model`,
`--terra-model`, `--sol-model`. Do not introduce a fourth naming scheme.

## Testing

Run everything from the repository root. Test modules use two import styles
(package-relative, and `sys.path` insertion); only `tools.<module>` names from
the root load both. Running from inside `tools/` silently skips about half the
suite as import errors.

```sh
PY=.venv/bin/python   # has psutil; the system python3 does not
$PY -m unittest tools.test_architecture                        # seconds
$PY -m unittest tools.test_goals tools.test_autocode           # the modules you touched
$PY scenarios/run.py run --fake                                # every scenario end to end, ~3 min
$PY -m unittest scenarios/test_harness.py                      # harness and catalog, ~2 min
$PY tools/run_suite.py                                         # the suite gate CI runs; about 40 minutes
```

`tools/run_suite.py` runs the same discovery as `unittest discover -s tools -t .`
minus the modules listed, with reasons, in `tools/suite_exclusions.json`. Most of
the full suite's time is spent waiting on subprocesses and timeouts, not
computing. Before committing a change to `tools/`, run the tests for the modules
you touched, `test_architecture`, and the fake scenario runs.

Use `scenarios/` for new end-to-end coverage: add a catalog entry with an
oracle, a reference solution and a broken variant (see `scenarios/README.md`).
Live-model runs need `--i-authorize-live-model-spend` and are never part of a
routine test run.

## Hygiene

- Do not commit run output, logs, `.patch` files or evidence bundles. Scenario
  results go to `.scenario-runs/` (ignored); AutoCode's own state goes to
  `.autocode/` (ignored).
- Write findings worth keeping as a short Markdown note in `docs/bugs/` or in
  the relevant doc, not as a new top-level report file.
- Other AutoCode runs, including self-builds, may be running from this
  checkout. Never edit or delete `.autocode/` contents you did not create.
