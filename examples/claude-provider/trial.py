"""Run a scenario on the Claude models (Haiku builds, Sonnet plans and validates, Opus reviews).

Registers a `claude-tiers` profile in memory and calls the scenario harness, so the repository's
profiles stay unchanged. Spends real model money: it needs --i-authorize-live-model-spend.

    python3 examples/claude-provider/trial.py run bugfix-trivial --profile claude-tiers \
        --i-authorize-live-model-spend --out /path/to/results --timeout-minutes 45
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scenarios"))
import run  # noqa: E402
from harness import profiles  # noqa: E402

profiles.PROFILES["claude-tiers"] = {
    "provider": "claude",
    "models": {"requirements": "claude-sonnet-5-5", "planner": "claude-sonnet-5-5",
               "reviewer": "claude-opus-5-5", "builder": "claude-haiku-4-5-20251001",
               "validator": "claude-sonnet-5-5", "resolver": "claude-opus-5-5",
               "completion": "claude-opus-5-5"},
    "effort": {role: "medium" for role in profiles.ROLES},
    # A trial's guard rails: a stage limit, and a cap on reported tokens (cache reads included).
    "extra": ["--max-stage-seconds", "1200", "--max-reported-tokens", "6000000"],
}
sys.exit(run.main(sys.argv[1:]))
