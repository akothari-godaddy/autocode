# Follow-up repairs from the Codex-only scenario campaign

The campaign at `b0e8d8ea` finished all 48 scenarios, with 20 passes. The
remaining outcomes mix application defects, non-converging planning and review,
and bounded operational stops. They are not 28 interchangeable runtime bugs.
PR #191 fixed oracle execution errors and an archive reference/coverage gap;
the prose verification replay fix was already on master.

## Planning loses a verified correction after clarification

The inventory run repeatedly cited `.autocode/state.json` as repository source.
The Investigator's correction reached its immediate retry, but clarification
retired the active guidance. A later planning cycle repeated the same rejected
citation, and the one-investigation-per-problem limit correctly prevented a
second investigation.

`autocode_stuck_job.with_guidance` now reads accepted planning report corrections
from the existing investigation history. They remain prompt context across
clarification, without resetting a repair allowance, an investigation limit,
approval, permissions or a spend cap. Only `stage_output` diagnoses accepted
with a retry are reusable; execution, environment and user-decision diagnoses
are excluded. A follow-up conversation turn excludes older corrections.

A public CLI fault-injection regression reproduces rejection, investigation,
clarification and re-planning. It fails against the previous runtime and
completes against the repaired runtime, with one investigation and one answer.
Pure tests cover scope boundaries and the absence of state mutation.

Planner instructions also distinguish source citations from runner context,
retain settled literal requirements and saved answers, and avoid promoting
report wording or a mistaken example into another product decision. These
instructions target the observed citation and repeated clarification failures;
they are not a guarantee that every model plan will converge.

## A draft verification correction is mistaken for a product decision

The timesheet and outbox live runs exposed a separate loop: the Plan Reviewer
correctly replaced a recursive "test the whole test suite" criterion with an
ordinary suite command, but the revision guard rejected that proof change before
the plan had ever been approved. Report repair restored the unusable proof and
planning restarted or asked another question.

The guard now permits a nonempty verification-method correction in a draft with
no approval receipt, while retaining the same criterion ID, literal behavior and
human-review requirement. Behavior, permission and protected-list changes still
need saved user authorization. Current and invalidated approvals retain their
existing proof protections. The pure revision guard moved into
`autocode_contract_revision`; the public `autocode_goals` entry remains available
and its architecture line limit shrank.

A public CLI regression fails on the original runtime and finishes on the
candidate with one plan approval, no clarification and no Investigator. Pure
tests also reject an accompanying behavior, permission or human-review change.
Saved approval events also prevent a cleared current receipt from granting the
draft exception after approval.

## Completion reports retype approved criteria and builders rename tests

The outbox repair passed all ten oracle checks, but its Completion Owner inserted
one word into an approved criterion. The existing guard correctly rejected the
report. Review generation schemas now enumerate the approved criterion IDs and
literal texts, in addition to the existing contract and task identity bindings.
The full ordered contract and independent evidence are still checked at runtime;
the schema does not authorize a reworded or weaker completion report. The helper
moved from `autocode_support` to the pure `autocode_report_schema`, lowering the
support module's line limit.

The archive repair passed its application checks but renamed two original tests
to match newly planned case IDs. Both the oracle and regression proof reject
removed test names. Planner and Builder instructions now retain those names and
assertions, adding separate case tests when needed.

## Repair the generated cache, outbox and extractor

Three new catalog scenarios retain the actual failing application source as
their seed, with the original tests. Each has a repaired reference, an unchanged
negative control with the new tests, and independent hidden contract checks:

- `bugfix-cache-numeric-deadline`: compute a valid deadline before mutating cache
  entries. Retain ordinary numeric rounding, using exact rational arithmetic
  only when conversion raises or finite float addition overflows. Cover a large
  integer TTL with a float clock and expiration at an overflowed float sum.
- `bugfix-outbox-query-limit`: saturate only the internal SQLite limit at its
  representable maximum, preserving the unbounded positive Python input contract,
  event order, durable identity and acknowledgement after successful delivery.
- `bugfix-archive-corruption-cleanup`: validate directory payloads, normalize
  decompression errors to the requested `ValueError`, and clean staged files
  before re-raising. Cover stored and compressed corruption with both absent
  and existing empty destinations.

The original application module SHA-256 identities are respectively
`9bea088551b0176b15080ee55c4c34ba10635fdcc4633b4f158828f5ef94c8c0`,
`268f0654b8e2fbfee8c492429cdcee667f8c540ae79e343b208996942397cd7e`,
and `c939e21f97cd4fb4484d4005e41dd4b4a17df3e7b1240aa7f371a3082607dd85`.
Original campaign evidence remains unchanged. Reference repairs are not
counted as successful live AutoCode completions.

Planner and Validator instructions now include numeric interactions and failure
after staging begins, including compressed file and directory payloads. This
improves the checks the models are asked to perform; it does not give AutoCode
a domain-specific archive or database implementation.

## Review an accepted tradeoff within its stated scope

The sound-design review blocked rollback because redeploying the old consumer
restores its old double-charge bug. The design explicitly accepted that
consequence. Architect instructions now require a blocking concern to identify
the binding requirement the tradeoff violates, rather than silently extending
the new version's guarantee to rollback. Missing rollback procedures, incompatible
data and violations of explicit rollback guarantees remain blocking concerns.

## Mobile header padding breaks the dashboard accessibility gate

The full suite reproduced a 57 px mobile header where the normal layout requires
56 px. A 44 px control, two 6 px padding edges and a 1 px border exceeded that
height. Reducing vertical padding to 5 px preserves the minimum hit area and
allows the header to expand for enlarged text. The existing browser accessibility
suite checks both breakpoint geometry and text resizing.

## Operational stops remain bounded

Provider inactivity and missing reported usage are not cured by weakening the
usage guard. Unknown consumption remains unknown, and a positive reported-token
cap still stops the run. Active-time and iteration caps likewise remain intact.
Live runs and deterministic fixture results are recorded separately under the
ignored `.scenario-runs/remaining-fix-validation/` tree.
