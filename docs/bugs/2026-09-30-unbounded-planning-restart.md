# A deferred goal approval restarts planning with no bound

Found 2026-09-30 while building adaptive planning (docs/adaptive-planning.md).

When AutoResolver defers a `goal_approval` request
(`autocode_resolver_human._decision`, for example "Final planning evidence must
match the current source before approval"), `autocode_run_records` restarts
planning with `planning.start(state)`. `start` archives the planning record and
creates a new one with `astra_calls: 0`, so the plan-review allowance
(`review_call_limit`, default 2) starts over. If the next final review is
deferred for the same reason, planning restarts again. Nothing counts restarts,
so a condition that keeps deferring approval keeps spending review calls with no
limit and no pause for a person.

Reproduced with the scripted provider before the adaptive gate was fixed. The
approval gate accepted only `astra_finalize` as final evidence, so every early
approval was deferred. One run made 559 plan reviews in ten minutes (contract
revision 561, `state.json` 9 MB) before it was stopped by hand. On master the
same loop needs a deferral that repeats: the source revision moving between the
final review and approval, or a final token that never matches.

Suggested fix: count restarts caused by a deferred approval (for example,
`planning_history` entries whose restart reason is a deferral) and pause with
`PAUSED_PLANNING_BUDGET`, or ask AutoResolver, after the second one, instead of
starting a third cycle.
