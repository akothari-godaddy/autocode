# Continuous requirements and Planner boundary

A conversation has two independent outputs: the Requirements Gatherer replies
to the human, and the Planner produces a structured draft for the exact human
turn and requirements revision. A Gatherer reply does not make a plan fresh.
The repository-aware Plan Reviewer must still review the final contract before
the human can approve it and choose Build.

## Adapter contract

`tools/dashboard/planner_dispatch.py` is the structured Planner adapter. Its
provider receives the transcript, selected route, work directory, logical turn
ID and requirements revision. Dispatch observers retain process, provider and
result events. The production adapter uses a fresh OpenCode session with all
tools denied; malformed output, incomplete provider events, tool calls and
model-route mismatches fail without inventing a draft.

Every accepted draft passes `validate_structured_result`. The versioned schema
is documented in `docs/schemas/continuous-planner-contract.schema.json` and
shipped in `tools/autocode-schemas/continuous-planner-contract.schema.json` so
installed distributions do not need a repository checkout. The result must
match the requested turn, requirements revision and producing route, and must
report `fresh`. Validation failure preserves the previous usable draft.

`ConversationStore` in `tools/dashboard/conversation_store.py` routes existing
intake documents to their original implementation. New continuous documents
live in a separate directory and use independent Gatherer and Planner leases.
Restart recovery commits validated captured results or retries only delivery
known not to have started. An unknown provider outcome remains uncertain until
there is authoritative evidence; a missing process does not authorize replay.

## Durable transfer to a task

`autocode_conversation` defines the version-1 handoff and runner journal. A
handoff contains messages, unsent drafts, requirements history, structured plan
history, configured routes, and available Gatherer/Planner dispatch receipts.
Messages may include validated `execution` attribution (`role`, `model`, and
optional `engine`, `provider`, `reasoning_effort`) plus a list of `event_refs`.
These optional fields are absent from older handoffs, keeping their canonical
digests unchanged. Dispatch receipts retain their original states and history.

The dashboard stages a digest-bound receipt inside the selected project's
`.autocode/conversation-handoffs/` directory and supplies its path using
`--conversation-handoff`. The task accepts it under its run lock, verifies
project ownership even when execution uses a child worktree, and writes
`conversation-journal.json`. A repeated identical transfer is harmless; a
different conversation or receipt cannot replace the journal. The single
`conversation_handoff` state pointer records its ID, digest, staged path and
journal path. The dashboard reads the journal after attachment.

Transferred drafts and receipts grant no approval. Attached product changes
append conversation history and make the displayed draft stale; the runner's
existing contract-change process controls replanning, review and reapproval.

## Explicit Build boundary

Approval and Build are separate actions. After approval the dashboard sends
`--expected-goal-token` with Build. Under the run lock, the CLI requires the
same sealed approved contract, no unresolved questions, and the matching
Architect final token for joint planning. It checks again before continuing.
A successful Build request records one `build_start` user event for that token,
distinct from `goal_approval`. A stale token does not launch a provider or
record Build; retrying the same approved Build does not duplicate its event.

## Scoped model profile

The continuous profile applies only when creating a new task from a handoff.
Ordinary CLI defaults and already saved task routes retain their configuration.

| Role | Model | Reasoning |
| --- | --- | --- |
| Requirements Gatherer | OpenAI GPT-6 Sol | Low |
| Planner, Builder and Builder retry | OpenAI GPT-6 Sol | High |
| Resolver | OpenAI GPT-6 Sol | Max |
| Plan Reviewer, Architect, Validator, Completion Owner | Z.AI GLM 5.3 | High |
| Visual review | OpenAI GPT-6 Astra | High |

Explicit incompatible routes are rejected before dispatch. The continuous
profile keeps those routes during recovery: generic reasoning escalation does
not change them, while the authorized Builder retry still runs on Sol High.
The continuous
profile disables default usage/time/idle/iteration caps; explicit CLI budgets
remain authoritative. Verification and contract approval gates still apply.

Offline tests exercise the real adapter with a fake transport and the actual
CLI with isolated temporary repositories. They do not claim an installed
live-provider result or visual acceptance of the dashboard.

## Using the dashboard

Start a conversation without choosing a repository. The Requirements Gatherer
responds in chat while the independent Planner updates the draft alongside it.
On a narrow screen, the compact summary keeps the outstanding question visible;
View plan opens the full draft without losing the conversation.

Choose a project when ready for repository-aware planning and Architect review.
The same chat retains the original messages and draft history. When the exact
plan is reviewed, Approve & build first records approval, waits for its confirmed
receipt, then starts that approved revision. Build activity, validation,
review findings, recovery and completion appear in the same conversation.
The activity details retain each role's actual model and reasoning setting.

Sending a product change after approval invalidates the displayed plan gate.
The existing task contract process must plan and review the change again before
building it. A draft or ordinary chat message never grants approval.

If provider delivery is uncertain after a restart, the dashboard retains the
message, draft and evidence. Retry is available only when saved evidence proves
that delivery did not begin. Captured replies can be recovered without making
another provider call.

## History and permanent deletion

Archive keeps files and is reversible. A temporary workspace confirmed missing
by fresh filesystem and worker checks moves into history and returns if it
reappears. Unavailable or uncertain status does not qualify for automatic cleanup.

Permanent deletion is a separate explicit action. Preview names the selected
run and exact paths. Removing a dedicated task worktree or branch is optional
and available only after exclusive ownership is verified. Confirmation is bound
to the inspected contents and refuses running or uncertain tasks, symbolic
links, changed paths and shared workspaces. If a step fails, the receipt shows
what remains and permits retry of that same confirmed deletion. Files recreated
after a completed deletion require a new preview.
