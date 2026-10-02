# Preserve the exact Validator retry at resolver exhaustion

A real local trial exhausted two report repairs for mismatched Validator
command evidence. AutoResolver published its stop as an operational question.
After corrective information was supplied, it retained a `PAUSED_RESOLVER`
boundary. Plain Resume correctly did not authorize execution, but the existing
exact `--retry-report` action accepted only `PAUSED_REPEATED_FAILURE`, leaving
the inspected report without an effective retry action.

The public view now offers the exact rejected Validator repair identity at
either stopped boundary. Only an explicit retry for that identity may start
fresh validation, after checking unchanged source, contract, schema and pinned
artifacts. Other pause kinds, active/uncertain attempts and other report errors
remain ineligible. Prior failed attempts and exhausted repair counts are
archived, not erased; limits and plan approval are unchanged. Plain Resume
remains a hold. The CLI delegates this policy to a cycle-free module.

If the diagnostic path already archived the exhausted report repair, the public
view instead offers one explicit failed-stage retry. This requires the current
Validator's exact stalled evidence-failure identity and unchanged source. Later
investigator failures cannot substitute their identity for the Validator's.
The scoped authorization is consumed once by the repeated-failure guard.
