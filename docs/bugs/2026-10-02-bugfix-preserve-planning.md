# Bug-fix planning contradicted preservation proof

A real local A2A task for blank-name validation reached independent plan review.
Its diagnosis classified invalid names as restore cases and valid-name/argument-count
checks as preserve cases. The reviewer correctly rejected the handoff because
`BUG_DIAGNOSIS_RULE` required every diagnosis test to fail on the original code.
Preservation checks already pass there, so both obligations could not hold.

The Investigator also contained an unqualified fail-first sentence before its
correct preserve-case explanation. Both prompts now state proof requirements by
case kind: restore tests fail before and pass after; preserve tests pass before
and after and are planned with `guard:` methods. Required named-test coverage,
exact-output assertions, independent proof and human plan approval are unchanged.

Focused prompt regressions failed first for the Investigator and all four planning
stages. The bug-job, test-case proof and architecture suites then passed all 89
tests, including rejection of missing or mislabeled preserve cases and restore
tests that already pass on the original code. A broader changed-suite and fake
scenario run were attempted; contributor authentication/process constraints affect
those CLI fixtures in this tool session. Full real-model completion is still a
separate verification requirement.
