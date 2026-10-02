# Whitespace overhead in inline output schemas

OpenCode stage prompts embedded a pretty-printed JSON output schema. Compact JSON
serialization preserves every schema value while removing indentation and
separator whitespace. This change does not remove schema fields or constraints.

The regression test parses the schema from the generated prompt, checks equality
with the source schema, and verifies compact formatting and a smaller prompt
schema. It fails against the previous prompt builder and passes after the change.

An offline comparison using the actual old and new prompt builders on 319 saved
schemas removed 862,716 bytes. Every other generated prompt byte was unchanged.
Of those savings, 162,072 bytes came from the 59 scenario review prompts: 4.31%
of their complete original prompt bytes. This measures bytes, not live tokens,
billing, or model output quality.

The preceding combined local validation passed 254 affected tests and completed
54 offline scenarios: 52 PASS, one expected NOT_EXERCISED, and one live-only
SKIPPED. The full suite was not green: the installed-package check needed an
isolated package installation, and CLI and browser tests hit their time limits.
The separate validation-diagnostic change is outside this formatting fix.
