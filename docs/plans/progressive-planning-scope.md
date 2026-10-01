# Progressive Planning Scope And Verification

PR #204 published the initial implementation and the authorized scope updates
for #22 and #23. Its audited replacement separates behavior-preserving
extractions, inactive libraries, and the complete guarded runtime. Do not close
#22 or #23, delete their prerequisites, or claim the
original Project → Workstream → Milestone → Task hierarchy is delivered.
Keep #16 open: offline rule coverage is not live effectiveness.

## Recorded scope exception

The user authorized a limited exception to these issues' start conditions:
the smaller **single-run progressive-planning** version may proceed before a
real project needs it and before #16's "done" tests pass. This exception does
not establish that #16 passed, does not authorize live trials or spending, and
does not enable the original multi-run hierarchy. The original proposals below
stay valid as **deferred work**.

## Scope For #22 (Workstreams / Project Agreement)

The published scope exception remains; replacement-series verification updates
must not expand it into the deferred hierarchy:

> **Scope update (progressive-planning version, in progress).** Part of what
> this issue asks for is being delivered differently than the original
> parent/child-run sketch, in one continuing run:
>
> - **Implemented and offline-verified:** a fixed product contract with one
>   whole-product milestone; an optional Planner-proposed slice map (complete
>   capability/requirement coverage, first slice, tentative future slices) with
>   replayable machine checks; an explicit continuation delegation sealed into
>   ordinary plan approval (hash plan content first, then seal its identity
>   into the approved contract); inherited requirement coverage that a slice
>   revision can never drop; per-slice allowances that retries and splits
>   share. The existing controller activates reviewed artifacts, executes
>   serial bounded tasks, verifies cumulative checkpoints, accounts actual
>   stage usage, classifies progress and enforces full-product completion.
>   Four real-CLI fake-provider scenarios have independent oracles, reference
>   solutions and broken controls. Twenty-six real CLI tests cover restart,
>   regression repair, split/reorder, shared quotas, stale/fabricated authority,
>   approved product/permission changes and selective authenticated retirement.
>   Ordinary reapproval preserves spent usage and historical proof; removal is
>   signed as an exact named check definition visibly shown in the revised
>   plan, never inferred from a changed plan/criterion ID.
> - **Validation limits:** the coverage is offline with fake providers, not
>   live-model effectiveness. The original real-project and #16 conditions
>   remain unsatisfied; revision-specific results, earlier failures and
>   qualification gaps are recorded below rather than treated as feature success.
> - **Deferred (original proposal):** Project → Workstream → Milestone → Task
>   as separate child runs, a shared parent agreement file across lanes,
>   cross-run delegation and multi-run approval invalidation. Prerequisites
>   unchanged: a real project that needs this, and #16's done tests. The
>   hierarchy prototype is checkpointed on `feat/hierarchical-planning` for
>   reference.
> - **Start-condition exception (recorded):** the single-run version above may
>   proceed before those prerequisites. It does not satisfy or remove them.

## Scope For #23 (Interface Contracts / Walking Skeleton)

The single-run implementation retains these boundaries:

> **Scope update (progressive-planning version, in progress).** Version one
> adopts the issue's two instincts inside a single run, and defers the rest:
>
> - **Walking skeleton as the first slice:** the proposal's first slice must
>   have an observable useful result across the essential layers (the main
>   user journey), bounded writable paths, product-criterion references and
>   nonempty machine checks whose commands the runner can actually replay.
>   Investigation/setup may be tasks but cannot be reported as delivery.
> - **Integrated verification:** every slice checkpoint re-runs the entire
>   cumulative required-check set (earlier slices' retained demonstrations,
>   `fully_verify` targets and always-applicable constraints). A slice can
>   pass while a broader product criterion stays open; partial progress is
>   never product acceptance. Simulated-scale results must say what they do
>   not prove.
> - **Controlled change:** interface/technical changes proceed only after
>   independent review and fresh cumulative proof. Product changes, new
>   permissions and unresolved product decisions return to the user through
>   the ordinary goal-change/answer/approval path; spent usage, findings and
>   check obligations survive the new contract hash, and only checks whose
>   behavior an approved change explicitly removes may retire (shown in the
>   visible revised plan).
> - **Deferred (original proposal):** versioned producer/consumer interface
>   contracts as first-class shared artifacts across independent workstreams,
>   and change-request invalidation of sibling workstream approvals. Those
>   need the multi-run hierarchy deferred in #22.
>
> The final product check follows the goal's named user journeys and the full
> product-checklist proof, not the last slice's tests alone.

## Version-one gates this work must pass before either issue is touched again

1. Real CLI with fake providers and default limits: initial planning consumes
   its normal review calls, S1 verifies while a broad criterion stays OPEN,
   restart recovers, S2 completes, full proof, completion in one run; fake
   clock proves per-slice and aggregate budget accounting.
2. Negative authority: ordinary/legacy approval, stale or tampered plan/review/
   source, unknown delegation and undetailed future slices cannot authorize
   dispatch.
3. Partial-proof safety and cumulative regression/rework: a local slice PASS
   never marks the product; S2 breaking S1's required behavior runs A+B, fails,
   repairs and re-proves; a fabricated PASS is rejected, never accepted as
   FAIL.
4. Revision/recovery: split/reorder without losing coverage, findings, checks
   or budgets; exactly-once recovery around activation and checkpoints.
5. Findings/stagnation, final-proof safety, approval/app compatibility,
   state/architecture compatibility (`progressive` is the only new top-level
   state record; `slice_id` the only new mutable task field), allowance
   accounting, and the product-change mid-run path.

## Local Verification Notes

The 2026-10-01 repair gates below use the verified worktree-local package and
`.venv/bin/python`, not the main checkout's installed package. The published
feature is `6e7840c1`, based on `3a54face`. Remote master was separately verified
at `fbb82e07cdb46468d5c7db974e7f18c3ed8281dc`; these are not tests of a reconciled
replacement branch. Earlier complete/green claims are superseded by this record.

- The full progressive real-CLI suite passed 26 tests in 787.994 seconds. It
  retains the original 16 tests and adds mixed outcomes, subsequent task
  admission, goal-change answers, human acceptance, adaptive review, proof
  refresh and explicit allowance recovery. This run precedes the final
  command-normalization and initial-approval follow-ups below.
- A later full 26-test run finished in 438.238 seconds with 25 passes and one
  stale error-message expectation: unauthorized future work was rejected
  before Builder dispatch. The expectation was corrected to the shared target
  policy diagnostic; that complete negative case then passed in 11.390 seconds.
  The corrected uninterrupted full CLI rerun passed all 26 tests in 435.250
  seconds. It precedes only the final saved-v2 session-provenance follow-up.
- The frozen source, including the shared provenance helper, subsequently
  passed all 26 CLI tests in 465.616 seconds.
- Actual CLI/runtime interrupted-recovery and accounting coverage passed seven
  tests in 131.477 seconds, including artifact publication/owner-commit faults,
  actual session identity, default local/aggregate exhaustion, explicit
  increases, report repair, refunds and exactly-once provider-token accounting.
- `tools/run_suite.py --changed` passed 1,579 tests in 102 modules in 173
  seconds before the final admission and saved-v2 review follow-ups. It is not
  a full-suite result or proof that subsequent edits pass.
- A subsequent `tools/run_suite.py` run passed 2,535 tests in 173 modules in
  764 seconds, including browser-backed dashboard catalogue coverage and the
  three additional saved-v2 recovery repairs. This precedes the remaining
  repaired-Planner/reviewer approval integration.
- After registering the standalone CLI suite in CI, `--changed` selected all
  modules and passed 2,538 tests in 173 modules in 766 seconds, including the
  repaired-Planner/reviewer approval continuation. The final session checks
  below were verified separately after this broad run.
- The final source-checkpoint gate, `tools/run_suite.py --changed 3a54face`,
  passed all 2,540 tests in 173 modules in 882 seconds after all provenance
  fixes and the shared helper. The explicit base avoids conflating this
  source-branch result with moving master or the replacement branches.
- The scenario harness passed 71 tests. The complete fake catalog finished
  without failures; `feature-refund-window` remains `NOT_EXERCISED` because its
  Resolver stage was not reached, and `stuck-planner-citation` was skipped
  because it requires a live Investigator. All four progressive catalog
  seed/reference/broken-control sets behaved as expected.
- The dashboard Python gate passed 256 tests. All 22 CI-selected nonbrowser
  Node scripts passed, including shipped approval rendering, ordinary
  token/Resolver envelopes, stale rejection and separate approve/build actions.
  Three browser-dependent scripts were excluded from that Node invocation,
  matching CI; this does not establish browser accessibility success.
- Independent review found and reproduced an option-value normalization
  bypass. The repaired helper preserves option-bearing argument vectors and
  normalizes only simple positional unittest targets. Initial approval now
  uses the same policy, restricted to the reviewed first slice. The focused
  admission/runtime/plan-flow/architecture gate passed 69 tests after that fix.
- Saved-v2 independent review additionally found repaired-report witness,
  artifact/delta pairing and pending Resolver-control recovery gaps. Those
  repairs passed 76 focused tests. Continuing from a repaired Planner through
  ordinary renewed approval exposed another literal-stage witness lookup,
  which is now repaired and covered by actual CLI approval tests. Final review
  additionally found missing original-session/expected-session checks at
  ceiling recovery. The final repaired snapshot passed 119 focused tests in
  112.522 seconds, including normal/detail recovery, renewed approval,
  negative provenance/control cases, ordinary compatibility and architecture.
  Independent actual-CLI probes closed both findings, preserving the complete
  reconciled saved state and ledger on rejection. Their shared stdlib-only
  provenance helper then passed 121 focused tests in 231.915 seconds, including
  explicitly sessionless provider positives. Independent factoring review
  found no defect and passed six targeted cases. The complete fake catalog was
  repeated without failures on the frozen source, with the same qualifications.

The reconciled runtime ratchets are 1,525 lines for `autocode.py`, 1,155 for
`autocode_goals.py`, 635 for `autocode_support.py` and 983 for `autopilot.py`.
They preserve master's smaller support cap and include its human-review
handoff behavior. The source branch's 659-line support cap was not copied
onto master. No new import cycle or cap increase is permitted.

Historical UI-12/UI-13 failures measured the mobile top bar at 57 px rather
than 56 px at 759 px width on baseline `ae7ad162`. A later standalone browser
invocation failed the completed-state hero assertion instead. Neither failure
reproduced in controlled, source-identity-verified full shell-matrix reruns on
pristine `3a54face`, pristine `fbb82e07`, and the dirty feature checkout; the
complete Python suite also passed its browser-backed tests. No cause is
asserted for the earlier failure. Production dashboard assets remain unchanged.
Prior load-sensitive local `test_gocode` failures likewise have no proven
cause; the latest changed and complete gates passed that module.

No live-model effectiveness, original multi-run hierarchy or #16 completion is
claimed. Runtime repairs are preserved at source commit `a160a1a3` for reconciliation,
not yet published as the complete runtime replacement. The extraction-only
foundation was separately reconciled onto pinned master `fbb82e07`, reviewed,
committed as `613b3edb`, and published as PR #210. It passed 1,087 changed-gate
tests, 267 focused tests, 73 independently executed review tests, the complete
fake catalog with the same two qualifications above, and 72 harness tests.
It has no progressive production exposure, and both macOS/Linux CI passed.
Master subsequently advanced to recorded `f0ae34de4f8aa08247da25665280550e1983748d`.
The clean foundation forward merge `697d8377` preserves its draft-example recovery and
oracle fixes; it passed 1,129 changed-gate tests, 54 targeted tests, 72 harness
tests and the complete fake catalog (49 PASS, one NOT_EXERCISED, one SKIPPED).
The inert libraries were committed as `519ab28f` and published as PR #212,
depending on #210. They passed 147 policy/architecture/verification tests after
advancing to the updated foundation, 50 ordinary compatibility tests, independent
review with 147 tests, and the complete fake catalog with the same qualifications.
Neither prerequisite exposes progressive proposals or execution.

The atomic runtime is reconciled in `feat/progressive-runtime` on that
exact policy dependency. It preserves the report-identity schema alongside the
role-schema extraction, master human-review routing and acceptance policy,
retired token-budget behavior, draft-example recovery, and updated scenario
oracles. Pure ceiling-transition tests remain in the policy test module rather
than being duplicated in runtime-recovery coverage. #204 is not superseded
until the tested complete runtime replacement exists. Reconciled runtime gate
results are recorded separately from the source-checkpoint results above.

## Reconciled Runtime Gates

These results apply to the final runtime delta over `519ab28f`, including
recorded master `f0ae34de`, not the older source branch:

- Independent reconciliation review found no merge-specific defect. It
  executed 20 focused tests and seven replay/version/nonmutation probes.
- Focused production reconciliation passed 129 tests, including ordinary
  artifact review, report identity, draft corrections, progressive proof and
  recovered-session boundaries.
- `tools/run_suite.py --changed 519ab28f` selected the complete suite because
  CI now explicitly runs the standalone progressive CLI tests. The final run
  passed 2,568 tests in 175 modules, ten at a time, in 533 seconds. This includes
  interrupted recovery/accounting and browser-backed dashboard catalogue tests.
- The full progressive CLI suite passed all 26 tests in 591.443 seconds.
- The complete fake catalog finished with 53 PASS, one NOT_EXERCISED refund
  case and one SKIPPED live-Investigator case. All four progressive
  seed/reference/broken-control sets passed their independent oracle checks.
- The scenario harness passed 72 tests in 226.866 seconds.
- The frozen dashboard Python suite passed 256 tests; all 22 CI-selected
  nonbrowser Node scripts passed. Browser-backed catalogue tests passed in the
  full Python gate; the separate Node glob retains its three browser exclusions.
- Architecture, whitespace/conflict checks and the installed runtime package
  identity check passed. Capped modules match the ratchets recorded above.

An earlier reconciled full run had one assignment-scenario error after its
fixture provider stalled at a short timeout. All 23 module tests then passed
in isolation, and the unchanged complete rerun passed. No timeout, assertion
or production guard was weakened, and the cause is not claimed as proven.

Saved-v2 coverage drives genuine public-stage acceptance, persisted artifacts,
actual CLI allowance actions and ordinary approval. It is not a complete
fake-provider v2 CLI journey. Offline coverage does not establish live-model
effectiveness or deliver the deferred multi-run hierarchy.
