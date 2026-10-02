# Revision repairs need the actual review

A real local bug-fix plan review requested four missing preservation tests under
concern C1. The Planner returned a contract without the required wrapper. Its
report repairs omitted responses, then invented `plan_review_concern_1` with no
evidence. The runner correctly rejected both, but the repair prompt did not supply
the saved current review whose IDs and requests it required.

Revision and finalization repairs now receive the current planning exchange:
discovery and review, plus the accepted revision for finalization. Prior planning
cycles are excluded. The repair instruction requires substantive responses or
decisions for the actual saved IDs and evidence, leaving coverage, protected
contract, review and approval guards unchanged.

Context, instruction and real prompt-construction regressions failed first for
the absent planning exchange and then passed. A live task's eventual completion
must still be checked independently.
