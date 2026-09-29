"""CLI status rendering, with controller services supplied by the caller."""
import json
import sys


def render(runner, state, args, workspace, run_dir):
    active = state.get("active_stage")
    worker_state = runner.processes.recorded_worker_state(active) if active else None
    active_finished = runner.stage_completed(state, active) if active else None
    stale = bool(active and state.get("status") == "RUNNING"
                 and worker_state and worker_state.get("checked")
                 and not worker_state.get("alive") and not active_finished)
    current = runner.support.snapshot(workspace) if state["status"] == "TASK_COMPLETE" else None
    completion_current = (runner.completion_gate.completion_ready(state, state.get("final_decision", {}), current)
                          if state["status"] == "TASK_COMPLETE" else None)
    if stale:
        print(f"STALE CHECKPOINT: saved status is RUNNING but the recorded "
              f"{active.get('stage')} workers are gone and no terminal report was saved. "
              "AutoResolver must reconcile the retained attempt before any further provider call.", file=sys.stderr)
    print(json.dumps({"run_dir":str(run_dir), "workspace":str(workspace), "project_workspace":state.get("project_workspace", str(workspace)), "task_branch":state.get("task_branch"), "status":state["status"], "iteration":state["iteration"],
                      "stale":stale,
                      "next_action": (f"AutoResolver must reconcile retained attempt {runner.attempt_id(active)} before any provider call"
                                      if stale else None),
                      "active_stage_workers":worker_state,
                      "active_stage_finished":active_finished,
                      "engine":state.get("settings", {}).get("engine", "codex" if args.run_dir else args.engine or runner.DEFAULT_ENGINE),
                      "next_stage":state.get("next_stage", "legacy; inspect saved finals"), "sessions":state["sessions"],
                      "phase":state.get("phase", "DISCOVERING" if not args.run_dir else "migration_required"),
                      "contract_token":runner.goals.token(state["goal_contract"]) if state.get("goal_contract") else None,
                      "current_task":state.get("current_task"), "last_decision":state.get("last_decision"),
                       "settings":state.get("settings"), "active_stage":active,
                       "reasoning_escalations":state.get("reasoning_escalations", []),
                       "attempt_id":runner.attempt_id(active) if active else None,
                       "completion_current":completion_current,
                       "milestone_checkpoint": runner.milestones.summary(state),
                       "orchestration_batch": state.get("orchestration_batch"),
                       "unit_handoffs": state.get("unit_handoffs", {}),
                       "milestone_activation_pending": (run_dir / 'milestone-checkpoints-requested.json').exists(),
                       "interventions": runner.intervention_metadata(workspace, run_dir, state),
                       "view": {**runner.run_view.view(state), "delivery": runner.dependency.export(state, current, completion_current)},
                       **runner.resolver_human.projection(state)}, indent=2))
