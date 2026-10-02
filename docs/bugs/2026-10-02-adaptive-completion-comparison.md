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
