"""Command-line arguments for `autocode`: the parser and the rules for combining flags.

Kept out of autocode.py so the CLI contract reads in one place. Nothing here
imports the runner; main() passes in the few runner constants the help text needs.
"""
import argparse
import sys
from pathlib import Path


def build_parser(*, unit, units, role_models, joint_models):
    """The `autocode` parser. ``role_models`` are the Codex-only defaults per role and
    ``joint_models`` the selected provider's defaults, both shown in help text."""
    parser = argparse.ArgumentParser(description="Independent requirements gathering, planning, plan review, build, validation and completion ownership")
    parser.add_argument("task", nargs="?", help="Idea for the requirements gatherer, planner and plan reviewer to turn into an approvable build brief")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--unit", choices=units, default=unit,
                        help="Run only this unit, stopping before the next unit; default runs Autopilot")
    parser.add_argument("--run-dir", type=Path, help="Existing run directory to resume")
    parser.add_argument("--in-place", action="store_true", help="Use this checkout directly; otherwise new tasks get independent worktrees from HEAD")
    parser.add_argument("--max-parallel-builders", type=int,
                        help="Orchestrator concurrency for independent milestones (new joint runs: 2; 1 dispatches serially)")
    parser.add_argument('--builder-strong-model', help='New-run Builder escalation model after one ordinary retry (default xiaomi-token-plan-sgp/mimo-v2.6-pro, high); pinned routes never escalate')
    parser.add_argument("--retry-builder", action="append", default=[], metavar="MILESTONE_ID",
                        help="Explicitly retry a stopped Builder after inspecting its retained work; requires --resume-paused")
    parser.add_argument("--figma-file", help="Figma Design URL to implement using the connected Codex plugin")
    parser.add_argument("--ui-run", type=Path, help="Accepted autocode-ui run to implement")
    parser.add_argument("--figma-review", choices=["automatic", "human"], help="Visual review policy for new Figma runs (default: automatic)")
    parser.add_argument("--engine", choices=["codex", "gocode", "opencode"],
                        help="Select Codex, GoCode, or OpenCode; resumes keep the saved engine")
    parser.add_argument("--provider", default=None,
                        help="Tool that runs each role for a new run. Default: AUTOCODE_PROVIDER, then default_provider in "
                             "~/.config/autocode/config.toml, then opencode. Other names load ~/.config/autocode/providers/<name>.toml")
    parser.add_argument("--joint-planning", action="store_true",
                        help="Separate requirements, planning, and independent review; default for new OpenCode/GoCode runs, opt-in for Codex")
    parser.add_argument("--glm-model", help="Planner model: OpenCode provider/model or native Codex GPT name")
    parser.add_argument("--glm-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override planner draft and revision reasoning effort")
    parser.add_argument("--requirements-model", help="Independent requirements-gatherer model for the saved engine")
    parser.add_argument("--requirements-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override independent requirements-gatherer reasoning effort")
    parser.add_argument("--plan-reviewer-model",
                        help="Override the independent plan-reviewer model for the saved engine")
    parser.add_argument("--plan-reviewer-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override independent plan-reviewer reasoning effort")
    parser.add_argument("--max-iterations", type=int, help="Total iteration ceiling (new-run default: 15; resumes keep saved limits)")
    parser.add_argument('--unlimited-iterations',action='store_true',help='Remove only the iteration ceiling; other safety and usage limits remain')
    for role, model in role_models.items():
        label = {"astra": "plan reviewer", "terra": "builder", "sol": "validator",
                 "completion": "completion owner"}[role]
        parser.add_argument(f"--{role}-model",
                            help=f"Override the {label} model (joint default: "
                                 f"{joint_models[role]}; "
                                 f"Codex-only default: {model}; resumes keep the saved model)")
    parser.add_argument("--astra-provider", help="Codex model_provider override for the Plan Reviewer (flag keeps the legacy Astra name)")
    parser.add_argument("--terra-provider", help="Codex model_provider override for the Builder (e.g. ZAI); default is the local Codex login")
    parser.add_argument("--sol-provider", help="Codex model_provider override for the Validator (e.g. ZAI); default is the local Codex login")
    parser.add_argument("--completion-provider", help="Codex model_provider override for the completion owner; default is the local Codex login")
    parser.add_argument("--reasoning-effort", choices=["low","medium","high","xhigh","max"])
    parser.add_argument("--astra-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for plan review only")
    parser.add_argument("--terra-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for the Builder only")
    parser.add_argument("--sol-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for the Validator only")
    parser.add_argument("--completion-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for the completion owner only")
    parser.add_argument("--pin-model-role", action="append", choices=tuple(role_models), default=[],
                        help="Keep this role's selected model and reasoning effort instead of escalating it automatically")
    parser.add_argument("--headroom", choices=["off","on"], default=None,
                        help="Off by default; on fails closed until compatibility is verified")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pause-after-stage", action="store_true")
    parser.add_argument("--chat", action=argparse.BooleanOptionalAction, default=None,
                        help="Converse with the planner/reviewer and approve the brief here (default: on in an interactive terminal)")
    parser.add_argument("--context-soft-tokens", type=int)
    parser.add_argument("--rotate-after-input-tokens", type=int, help="0 disables checkpointed session rotation")
    parser.add_argument("--legacy-iteration-ceiling", type=int)
    parser.add_argument("--max-seconds", type=int)
    parser.add_argument("--milestone-checkpoints", action="store_true",
                        help="Enable enforced Builder/Validator/review milestone checkpoints on a saved run; new runs enable them by default")
    parser.add_argument("--request-milestone-checkpoints", action="store_true",
                        help="Queue a boundary pause and milestone configuration for an active saved run; never launches or stops workers")
    parser.add_argument("--max-milestone-seconds", type=int,
                        help="Active-time budget per milestone; with --resume-paused this also resets spent time (default: 5400; 0 disables)")
    parser.add_argument("--max-milestone-replans", type=int,
                        help="Maximum changed-approach replans per milestone (saved default: 1; 0 means unbounded)")
    parser.add_argument("--max-milestone-stalled-reviews", type=int,
                        help="Reviews without progress before replanning (saved default: 3; 0 disables)")
    parser.add_argument("--max-stage-seconds", type=int,
                        help="Hard runtime limit for one provider stage (new-run default: 0/off; saved limits persist)")
    parser.add_argument("--max-idle-seconds", type=int,
                        help="Maximum provider inactivity outside a running tool (default: 300; 0 disables)")
    parser.add_argument("--max-tool-seconds", type=int,
                        help="Maximum time for a running tool or unreported descendant-tool interval (default: 1800; 0 disables)")
    parser.add_argument("--max-reported-tokens", type=int)
    parser.add_argument("--no-progress-limit", type=int, help="Pause after this many unchanged batches (new-run default: 3)")
    parser.add_argument("--max-findings-per-task", type=int,
                        help="Reject a REWORK task that bundles more than this many open findings (default: unlimited; 0 disables)")
    parser.add_argument("--resume-paused", action="store_true", help="Acknowledge a saved pause; uncertain stages still require reconciliation")
    parser.add_argument("--retry-failed-stage", action="store_true",
                        help="Authorize one fresh attempt for the recorded unchanged repeated failure after inspecting it; requires --resume-paused")
    parser.add_argument("--planning-review-call-limit", type=int, metavar="N",
                        help="At a planning-budget pause, save a finite total review-call allowance for this cycle only; no agent launched")
    parser.add_argument("--retry-report", metavar="ATTEMPT_ID",
                        help="With --resume-paused, retry an exact exhausted format-failed report as fresh independent validation")
    parser.add_argument("--accept-transport-change", action="store_true",
                        help="With --resume-paused, accept the current validated OpenCode configuration at a clean transport-change pause")
    parser.add_argument("--abandon-stage", metavar="ATTEMPT_ID",
                        help="Set aside exactly this stopped uncertain attempt, preserving edits and logs; no agent is launched")
    parser.add_argument("--show-goal", action="store_true", help="Display the exact contract revision and approval token")
    parser.add_argument("--answer", action="append", default=[], metavar="QUESTION_ID=TEXT")
    parser.add_argument("--feedback", metavar="TEXT", help="Send brief feedback to the Requirements Gatherer; never approves implementation")
    parser.add_argument("--delegate", action="append", default=[], metavar="QUESTION_ID",
                        help="Explicitly accept the proposed default and delegate this decision")
    parser.add_argument("--approve-goal", metavar="TOKEN", help="Approve exactly a previously displayed revision")
    parser.add_argument("--edit-goal", type=Path, help="Load a revised contract body JSON; invalidates approval")
    parser.add_argument("--approve-review", action="append", default=[], metavar="CRITERION_ID")
    parser.add_argument("--reconcile-review", metavar="CRITERION_ID=ANSWER_ID",
                        help="Bind an authenticated legacy acceptance to current validated evidence without a new approval")
    parser.add_argument("--accept-completion", action="store_true",
                        help="Operator-accept completion after the runner itself verifies every gate; use when the model's completion report cannot be produced")
    parser.add_argument("--review-token", help="Exact displayed contract/artifact/validation token")
    return parser


def validate(parser, args, unit):
    """Enforce cross-flag rules. Usage errors exit through ``parser.error``; rejected
    input returns the exit code to use, and valid input returns None."""
    if args.max_parallel_builders is not None and args.max_parallel_builders < 1:
        parser.error('--max-parallel-builders must be positive')
    if args.retry_builder and (not args.run_dir or not args.resume_paused):
        parser.error('--retry-builder requires --run-dir and --resume-paused')
    if args.unlimited_iterations and (args.max_iterations is not None or args.legacy_iteration_ceiling is not None):
        parser.error('--unlimited-iterations cannot be combined with an explicit iteration ceiling')
    if args.accept_transport_change and (not args.run_dir or not args.resume_paused):
        parser.error("--accept-transport-change requires --run-dir and --resume-paused")
    if args.retry_report and (not args.run_dir or not args.resume_paused):
        parser.error("--retry-report requires --run-dir and --resume-paused")
    if args.retry_failed_stage and (not args.run_dir or not args.resume_paused):
        parser.error("--retry-failed-stage requires --run-dir and --resume-paused")
    if args.planning_review_call_limit is not None and args.planning_review_call_limit < 2:
        parser.error("--planning-review-call-limit must be at least 2; unlimited is not supported")
    if unit and args.unit != unit:
        parser.error(f"This entry point runs only {unit}")
    if args.unit in ("autocode", "autoreview", "autoresolver") and not args.run_dir:
        parser.error("Build and review units require an existing --run-dir with an approved plan")
    if args.chat is None:
        args.chat = sys.stdin.isatty() and sys.stdout.isatty()
    for flag in ("max_iterations", "legacy_iteration_ceiling", "max_seconds", "max_stage_seconds", "max_idle_seconds", "max_tool_seconds", "max_reported_tokens", "no_progress_limit", "max_milestone_seconds", "max_milestone_replans", "max_milestone_stalled_reviews", "max_findings_per_task"):
        if getattr(args, flag) is not None and getattr(args, flag) < 0:
            parser.error(f"--{flag.replace('_', '-')} must be nonnegative")
    actions = [args.status, args.dry_run, args.migrate_only, args.show_goal,
               bool(args.answer or args.delegate), bool(args.approve_goal), bool(args.edit_goal),
               bool(args.approve_review), bool(args.reconcile_review),
               args.feedback is not None, args.accept_completion, args.abandon_stage is not None,
               args.request_milestone_checkpoints, args.planning_review_call_limit is not None]
    if sum(bool(a) for a in actions) > 1:
        parser.error("Choose one action per invocation; answering and approving are separate events")
    if args.retry_builder and any(actions):
        parser.error("--retry-builder is a resume action; do not combine it with another action")
    if args.review_token and not (args.approve_review or args.reconcile_review):
        parser.error("--review-token requires --approve-review or --reconcile-review")
    if args.reconcile_review and not args.review_token:
        parser.error("--reconcile-review requires --review-token")
    if not args.run_dir and any(actions[2:]):
        parser.error("User actions require an existing --run-dir")
    if args.feedback is not None and not args.feedback.strip():
        print("Input rejected: Feedback must be nonempty", file=sys.stderr)
        return 2
    return None
