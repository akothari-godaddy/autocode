# Dashboard persistent conversation verification

The dashboard implementation was completed directly after the operator stopped the two retained Dashboard and Planner runs. This work does not change the separate hierarchy run or claim completion of either retained run through edited state.

## Design and historical review

The accepted Figma file is `kcSv6tbrqK3pzMFl2BRSUk`. Lifecycle page `288:2` provides desktop draft `296:286`, mobile draft `296:579`, and reviewed plan `302:850`. The original shell frames `13:106`–`13:126` remain the reference for workspace, operational summaries, and secondary views.

The historical visual baseline is the retained `flash-visual-review.json` from `audits/dashboard-figma-correction-2026-09-24/final-validation-20260925/flash-review-package/`, SHA-256 `5c4f168b8cf01b472cbe92d2eb866f15523e25b218f55e148cab9293b689d774`. Its ten findings are handled as follows:

| Finding | Disposition and verification |
| --- | --- |
| 1. Saved configuration overlaps clipped header/action fragments | The task menu has a scrim and a viewport-bounded scrollable panel. M3 dialog/focus/browser checks exercise open and closed states. |
| 2. Mobile planning badge is squeezed beside the title | Mobile title and status use separate rows. Responsive screenshots assert no header clipping. |
| 3. Mobile filters resemble plain text | Existing master filter controls are retained. The original-shell matrix checks visible labeled controls and their touch targets. |
| 4. Theme inconsistencies obscure comparisons | Every new lifecycle screenshot explicitly selects light or dark. Draft and ready-plan captures include both themes. User theme preference remains saved. |
| 5. Toasts expose long absolute paths across mobile content | Path text is single-line ellipsized; notifications have a bounded, scrollable height. M3 workspace-action checks cover notifications. Exact deletion paths remain available in the deliberate preview dialog. |
| 6. Detached completion metadata is duplicated | Persistent chat summarizes completion once, with saved evidence disclosures. The dedicated operational completion record remains available in Now and keeps its authoritative saved timestamp. |
| 7. Default composition lacks persistent chat and live draft rail | Chat is the default task view. Desktop shows a quiet draft/contract rail; mobile uses a compact open-question summary and full-plan disclosure. Reviewed, approved, and mutable draft states have distinct labels. |
| 8. Mobile secondary tabs end abruptly | Tabs remain horizontally scrollable with an edge fade; keyboard navigation moves focus without selecting silently. |
| 9. Catalogue screenshots are taken midway down the page | Matrix captures reset every owned page/scroller before comparison; intentional scrolled captures are separate. |
| 10. Checkpoint class names are rendered as text | The master DOM construction fix is retained. Session-checkpoint tests verify real elements and restored reply drafting. |

## Behavior that must remain true

- One conversation retains user messages, model attribution, plan drafts, review findings, code changes, evidence, and lifecycle receipts.
- New product information invalidates the old review/approval/start boundary immediately. Ordinary Send does not approve or build.
- Approve & build records approval first, waits for that exact action to finish, rechecks the current revision, then sends a separate build command. Failure, uncertainty, changed revision, and navigation cancellation cannot cause a delayed build.
- Delivery that might already have reached a provider is not replayed automatically. A confirmed pre-dispatch failure can expose a supported retry.
- Confirmed missing temporary workspaces move to reversible history only from fresh backend metadata. Files are not deleted by that cleanup; returning workspaces reappear.
- Permanent deletion requires a server preview and exact named confirmation. The UI shows all selected paths; worktree/branch options depend on exclusive ownership. Partial or uncertain deletion retries preserve the original preview identity.

## Verification

The frontend gates are `test_continuous_chat_ui.js`, `test_persistent_chat_browser_ui.js`, the existing lightweight dashboard UI tests, `test_m3_lifecycle_browser_ui.js`, and `test_shell_a11y_ui.js`. Browser tests use disposable fixtures and their own uniquely named sessions. They do not call models or start an AutoCode run.

The persistent-chat browser gate covers desktop 1440×1024, tablet 1024×768, mobile 390×844, narrow 320px, light/dark themes, exact approval controls, open-question visibility, full-plan focus restoration, real preview/confirmation/deletion requests against disposable files, and 200% text. At enlarged text the transcript keeps at least 120px and the composer stays reachable through the scrollable panel. The shell gate covers the original 21-state matrix, drawer/focus/contrast, keyboard replies, separate approval/start receipts, uncertain pause reconciliation, recovery, and unavailable-model handling.

An independent owned browser sentinel remained open with its original title/content after the lifecycle suite, confirming that the suite did not close unrelated sessions. Test screenshots and source-hash manifests remain under ignored `.autocode/evidence/`; they are not source artifacts and must not be committed. Layout-only draft fixtures are explicitly labeled and are not evidence of provider execution or autonomous task completion.

### Recorded browser results

- Persistent conversation gate: passed, `.autocode/evidence/persistent-chat/run-67329/manifest.json`, SHA-256 `f4bbeeedf3503b268ba57387f74b18a08c55b5f21b3cbbf3eee4e7943e841ed0`. Its 24 screenshots match the recorded JS/CSS/HTML source hashes and include the final mobile draft and 200% text repairs.
- Original shell and operational flow gate: passed, `.autocode/evidence/m2-scenario-matrix/run-89557/m2-scenario-matrix-manifest.json`.
- Model, stale-state and dialog lifecycle gate: passed, `.autocode/evidence/m3-lifecycles/run-25394/m3-lifecycle-manifest.json`.
- All 21 lightweight dashboard Node checks passed, including the first-build toolbar payload, exact approval sequencing and preserved legacy Resume behavior.

Independent visual review found three additional issues during implementation: the mobile draft hid its question, the reviewed plan still used a draft label, and 200% text squeezed the transcript/composer and clipped desktop navigation. The final captures include their repairs. The further 200% mobile header finding is also resolved: project identity remains readable on its own row when the controls wrap, and navigation overlays follow the actual header height. The enlarged transcript, approval controls and composer are usable without horizontal page overflow. Full drafts display the actual producing Planner model and reasoning level when saved attribution is present; legacy records do not invent missing attribution.
