# The three RELIABILITY.md live cases (first run, GLM/OpenAI profile)

Run 2026-10-01 from a clean worktree of master `68e89aa4` (after #196 and #198), profile
`glm53-openai` (producers GLM-5.3 on the Z.AI coding plan; Plan Reviewer/Builder/Resolver GPT-6
on OpenCode's ChatGPT login), provider `kilocode`, `--joint-planning`. Evidence directories are
under `.scenario-runs/` in the run worktree (not committed).

| Case | Scenario | Verdict | Oracle | Runner | Model time | CLI calls / answers |
| --- | --- | --- | --- | --- | --- | --- |
| Small new application | greenfield-todo-cli | HONEST_BLOCKER | 10/10 | WAITING_FOR_USER | 2205 s | 7 / 4 |
| Feature in an existing project | feature-timesheet-by-project | PASS | 6/6 | TASK_COMPLETE | 2362 s | 5 / 1 |
| Bug fix | bugfix-iso-weeks | ERROR (time budget) | 5/5 | PAUSED_INTERRUPTED | 3319 s | 4 / 1 |

No FALSE_COMPLETE in any case: all three deliverables were correct in the workspace when the
harness stopped. Judged by RELIABILITY.md's definition (agreed criteria **and** the complete
user flow), one case passed and two did not: the feature case completed; the greenfield case
never reached its completion gate (validator permission blocker); the bugfix case was
interrupted by the time budget mid-rework.

## What happened, per case

- **greenfield-todo-cli** built the correct CLI (oracle 10/10), answered two clarification
  questions and a plan approval, then the validator stage was stopped three times by OpenCode's
  `external_directory` permission (the validator referenced paths outside the workspace, e.g.
  `/tmp` evidence paths). AutoResolver exhausted the automatic-recovery budget and paused with
  the cause named and `--resume-paused --grant-recovery N` offered. Honest stop, operational
  cause, not model quality. Fix candidate: keep validator evidence paths workspace-contained
  (the stop message already states the rule), or run the validator through a provider whose
  sandbox allows the evidence directory.
- **feature-timesheet-by-project** completed end to end: plan approved, built, validated,
  completion gate satisfied, TASK_COMPLETE; oracle 6/6 with two report repairs along the way
  (planner and validator reports, both recovered automatically).
- **bugfix-iso-weeks** investigated, planned, built and validated the fix correctly (oracle 5/5
  at timeout), but after the first completion review AutoResolver scheduled a second builder
  task (rework loop), and the scenario's 60-minute harness budget expired during it. The fix
  itself was already correct in the workspace. The run needed roughly one more cycle; budget,
  not correctness, ended it.

## Interventions

- Two clarification answers and one plan approval per run were driven by the scenario driver
  (product decision boundaries, working as designed).
- The first launch attempt used the harness's committed `glm53` profile (all roles GLM-5.3) and
  paused at `PAUSED_CROSS_MODEL` on every scenario: the cross-model check compares model
  families, so no Z.AI-plan-only arrangement can satisfy it (GLM-5.3 vs GLM-5.2 is still family
  `glm`). That profile works only for 3-step routing checks and should be fixed or marked as
  such in `scenarios/harness/profiles.py`.

## Follow-ups worth doing

1. Validator evidence paths must stay inside the workspace on OpenCode (greenfield blocker).
2. `scenarios/harness/profiles.py`: `glm53` cannot pass a full run's cross-model check; either
   split families across providers or document it as routing-only.
3. bugfix-class runs can need more than 60 minutes with this profile; raise the scenario budget
   or trim the rework loop before the next sweep.
