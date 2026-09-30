# Background GoCode startup

GoCode 0.1.315 printed version, unmanaged mode and successful Client Service
authentication, but did not exit within 45 seconds when invoked by the local
Autocode worker. Both piped and file-backed output reproduced the timeout.

The adapter now accepts that identity snapshot only after a separate, bounded
`gocode key list --json` succeeds. Its output is discarded. Missing identity,
missing authentication, a failed verification or another timeout still stops
startup. `gocode auth whoami` is unsuitable: it can exit zero while reporting
"Not signed in".

The focused transport tests failed before this change and pass afterward.
Broader local checks are not green: the subprocess test is blocked by process
enumeration restrictions, and Codex fixture scenarios reject this environment's
authentication configuration. A live retry through the A2A HTTP gateway still
failed before saving a run; its worker traceback is needed. This change does
not establish that a live GPT request works, nor that GoCode unmanaged mode
selects the intended model credentials.
