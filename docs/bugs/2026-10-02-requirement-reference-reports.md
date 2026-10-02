# Saved requirement references in refreshed handoffs

A live local bug-fix run repeatedly paused because the requirements model expanded
partial source quotations while refreshing its handoff. The existing provenance
guard correctly rejected these rows, despite repair prompts carrying exact copies.

Refreshed Requirements reports can now use an ID-only object for an unchanged saved
requirement. The report loader copies that entire row from the previous handoff,
preserves the raw provider report, and passes the canonical full report through
the existing source, coverage, omission and human-approval checks. Initial and new
requirements still need ID, text and source quotation. Explicit full rows retain
their supplied content; wrong quotations are never silently repaired. Unknown IDs,
duplicates and partial explicit rows are rejected. Missing requirements still need
a saved user-backed omission.

`tests.test_requirements_report` failed first for missing text, absent hydration,
and unchanged normal/repair launch schemas. It covers reference decoding, legacy
full rows, raw report preservation, initial handoffs, new requirements, malformed
references and unchanged omission guards. Local live completion remains a separate
check; passing these tests alone does not establish it.
