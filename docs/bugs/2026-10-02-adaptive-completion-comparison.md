# Adaptive planning through completion

Adaptive planning remains opt-in. The September 30 comparison stopped at plan
approval and cannot establish cost or correctness for a completed build.

## Progressive approval regression

The October 2 full-catalog scripted comparison found three adaptive failures:
`progressive-learning-journey`, `progressive-cumulative-regression`, and
`progressive-split-learning-journey`. Fixed planning passed all three. With no
blocking challenge, adaptive planning showed the draft for approval after three
model stages. Approval refused it: initial progressive delegation requires an
accepted revision and final independent review, not just a challenge.

The fix retains revision and final review whenever a progressive candidate is
present. It preserves the existing approval witness, exact-contract and
delegation checks. Ordinary plans retain early approval. After the fix both
variants pass 52/54 catalog cases; each has one `NOT_EXERCISED` Resolver case
and one skipped hybrid live-Investigator case, with no missing attempts.
Scripted model stages are 446 fixed versus 333 adaptive; this is plumbing
evidence, not a token, dollar or model-quality result.

An added to-do negative control reproduces the historical missing-brackets
failure. Its own three delivered tests pass, but the independent brief oracle
fails exactly the three output checks (7/10). The complete adaptive fake run is
correctly classified `FALSE_COMPLETE`, not a success in the comparison.

## Reproducible comparison

`scenarios/run.py build-compare` repeats both variants through completion, uses
the original catalog briefs and independent oracles, alternates arm order, and
retains every failure, timeout and missing attempt. `--prepare` saves the exact
protocol without calling a model; `--rebuild` reconstructs reports without
rerunning attempts. See [the commands and limits](../../scenarios/README.md#fixed-versus-adaptive-through-completion).

API estimates use the frozen October 2 standard text rate card, separate fresh
input/cache reads/cache writes/output, include reasoning and failed calls,
deduplicate replayed finishes, and apply the long-context surcharge per request.
Unknown rates and unfinished usage stay unpriced. Cost per pass includes failed
attempt spend. It is API repricing, not subscription pricing or an invoice.

Offline evidence is ignored under
`.scenario-runs/adaptive-build-comparison-20261002/{offline,offline-fixed}/`.
The approved live campaign schedules four original catalog cases, two variants
and two repetitions (16 attempts), with identical models and caps. Its production
source is frozen separately with a file-hash manifest. Live results must be
reported before recommending a default change.

## Declared negative probes blocked correct completion

The second adaptive greeting attempt delivered code that passed all 12 original
oracle checks. Its approved plan explicitly required six direct CLI probes with
exit codes `0/2/2/0/0/0`. The Validator's two cited commands independently checked
the regression tests and exact CLI bytes and exited zero. The runner nevertheless
added the plan's raw commands as checks expected to exit zero. Correct usage
errors returning 2 rejected three reports and led to a paused investigation.

The fix turns an explicit terminal exit-code declaration into shell assertions
for the corresponding quoted commands. All probes still run; a wrong zero exit
for a required usage error fails. Mismatched status counts, ambiguous quoted
snippets and statuses outside 0–255 are refused. Natural-language checks outside
this narrow syntax remain with the Validator. Reported checks in a PASS still
must exit zero, and ordinary test commands retain that requirement.

Re-executing the original stopped run's report against its unchanged delivered
files reproduces the rejection with the frozen runner. The patched runner
accepts all nine distinct reported and plan-derived checks. Source-file hashes
match before and after both replays; neither makes model calls. Regression tests
also reject a CLI returning zero for usage errors and a Validator falsely
claiming zero. Complete scripted greeting builds with the problematic plan now
finish in both planning modes, without report repair.

The recorded attempt cost $1.1489 at the frozen API rates, including $0.3950
(34.4%) for subsequent Validator repair and Investigator calls. This identifies
the cost of the observed failure path; it is not a measured live saving after
the fix. The approved 16-attempt campaign continues against source
`7c598602fc4746d58cf2e8a9b89df4e560f45cfa`, which predates this replay fix.

The patched full catalog retains the same 52 PASS, one `NOT_EXERCISED` and one
`SKIPPED` per mode. Eight complete-build regression tests also pass, covering
negative probes, broken deliveries, literal output and progressive approval.
The 2,588-test full unit/integration gate exposed a preexisting startup fixture
using the old underscore report name, plus installed-package checks whose shared
interpreter lacked `autocode_cli`. The configuration module's 12 checks pass with
an isolated local installation. The nine startup checks pass after locating the
retained report independently of its stage slug and synchronizing the fake
provider's failure with its recorded process registration. This fixes a test
race without relaxing the runtime's uncertain-execution or retry limits.

## Live interpretation constraints

The driver answers clarification questions with the model's proposed default
and records those answers. These can change the target away from the original
brief oracle. In the first fixed to-do attempt, it answered the brackets question
with "Notation: print the bare status word only". The approved plan and Builder
then used bare `open` and `done`, while the original oracle requires `[open]` and
`[done]`. Its 7/10 oracle score is not evidence that the Builder violated its
approved plan, nor a valid format-quality comparison against an arm with no such
answer. The attempt also stopped before completion at the active-time cap, which
is a separate outcome. Future quality comparisons need consistent clarification
answers that preserve the intended catalog behavior; the current attempts and
their original scores remain retained.

An interrupted model request without a final usage event leaves that attempt's
total API cost unknown. Known finished-request spend remains included as a lower
bound, including in failed attempts. An incomplete dollar total cannot establish
an overall saving.
