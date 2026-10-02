# Planning report repair lost clarification context

A local browser-to-Autocode bug-fix trial stopped when the planner omitted open
questions and represented saved human feedback as `machine_resolutions`. The
existing clarification checks correctly rejected those reports. Report repair
received the rejected draft and its error, but not the current requirements
handoff, matching investigation request, saved answers, or feedback provenance.

The generic repair prompt also called the original planning draft an immutable
execution-history baseline. A repair that removed invalid resolutions could
restore them on its next attempt while fixing a different coverage error.

Planning repairs now receive that clarification context and distinguish a
historical planning draft from recorded execution. The prompt requires empty
machine resolutions without a matching investigation request, records human
choices through their saved sources, and explains exact checklist coverage.
Builder execution-history protection, clarification validation, contract
protection and human approval gates remain unchanged.

Focused regressions failed first for the missing payload and ambiguous baseline
instruction, then passed with the change. Broader CLI and fake-scenario checks
must run in a normal Terminal under an isolated contributor configuration;
the tool sandbox cannot enumerate provider processes. A live assisted trial is
separate evidence and must not be reported complete until its public status
actually says `done=true`.
