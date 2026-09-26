"""Model profiles for live runs.

A profile is part of the evidence: two results are comparable only when their
profiles match, or when the profile is the variable under test.
"""
from __future__ import annotations

ROLES = ("requirements", "planner", "reviewer", "builder", "validator", "resolver", "completion")

PROFILES = {
    # Runner defaults: no model flags, so AutoCode's DEFAULT_ROLE_MODELS apply.
    "default": {"provider": "opencode", "passthrough": True},
    "glm53": {
        "provider": "kilocode",
        "models": {role: "zai-coding-plan/glm-5.3" for role in ROLES},
        "effort": {"planner": "max", "reviewer": "high", "completion": "high",
                   "builder": "low", "requirements": "low", "resolver": "high"},
    },
    # Verifier differs from producer: MiMo checks GLM work and GLM checks MiMo work.
    "glm53-mimo": {
        "provider": "opencode",
        "models": {
            "requirements": "zai-coding-plan/glm-5.3", "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "builder": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "validator": "zai-coding-plan/glm-5.3", "completion": "zai-coding-plan/glm-5.3",
            "resolver": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
        },
        "effort": {"requirements": "medium", "planner": "high", "reviewer": "high", "builder": "medium",
                   "validator": "high", "completion": "medium", "resolver": "high"},
    },
}

# AutoCode's CLI still names these flags after the internal stage names (see docs/models.md).
MODEL_FLAGS = {
    "requirements": "--requirements-model", "planner": "--glm-model", "reviewer": "--plan-reviewer-model",
    "builder": "--terra-model", "validator": "--sol-model", "resolver": "--astra-model",
    "completion": "--completion-model",
}
EFFORT_FLAGS = {
    "requirements": "--requirements-reasoning-effort", "planner": "--glm-reasoning-effort",
    "reviewer": "--plan-reviewer-reasoning-effort", "builder": "--reasoning-effort",
    "validator": "--sol-reasoning-effort", "resolver": "--astra-reasoning-effort",
    "completion": "--completion-reasoning-effort",
}


def resolve(name: str) -> dict:
    if name not in PROFILES:
        raise ValueError(f"unknown profile {name!r}; choose one of: {', '.join(sorted(PROFILES))}")
    return PROFILES[name]


def flags(profile: dict) -> list[str]:
    result = ["--provider", profile["provider"], "--joint-planning"]
    if profile.get("passthrough"):
        return result
    for role in ROLES:
        result += [MODEL_FLAGS[role], profile["models"][role]]
        if profile.get("effort", {}).get(role):
            result += [EFFORT_FLAGS[role], profile["effort"][role]]
    return result
