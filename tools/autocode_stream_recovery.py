"""Evidence and opt-in route recovery for silent OpenCode model turns.

Only stopped, nonterminal attempts admitted by the runner reach recover().
Existing recovery counters remain authoritative; this module grants no budget.
"""
import json
import re
import time
from pathlib import Path
try:
    from .autocode_progress import info
except ImportError:
    from autocode_progress import info


def configure(settings, args):
    """Persist explicit ROLE=provider/model,effort fallback routes."""
    routes = dict(settings.get("stream_hang_fallbacks", {}))
    for value in getattr(args, "stream_hang_fallback", None) or []:
        try:
            role, target = value.split("=", 1)
            model, effort = target.rsplit(",", 1)
        except ValueError:
            raise ValueError("--stream-hang-fallback expects ROLE=provider/model,effort") from None
        if role not in settings["roles"] or effort not in ("low", "medium", "high"):
            raise ValueError("Stream fallback requires an existing role and low/medium/high effort")
        if not re.fullmatch(r"[\w.-]+/[\w.:/-]+", model):
            raise ValueError("Stream fallback requires a provider/model identifier")
        route = settings["roles"][role]
        if route.get("engine", settings.get("engine")) != "opencode":
            raise ValueError("Stream fallback currently supports OpenCode roles only")
        routes[role] = {"model": model, "reasoning_effort": effort}
    if routes:
        settings["stream_hang_fallbacks"] = routes
    return settings


def diagnose(record, events):
    """Describe observed stream silence, not an unproven backend root cause."""
    if not record.get("timed_out") or record.get("timeout_kind") != "idle":
        return None
    activity = record.get("activity") or {}
    if activity.get("active_tool_count") or activity.get("process_fallback"):
        return None
    starts, completed, last, refs, session = set(), set(), None, [], None
    for event in events:
        part = event.get("part") or {}
        kind = event.get("type")
        if kind not in ("step_start", "step_finish", "tool_use", "text", "error"):
            continue
        last = {"type": kind, "id": part.get("id"), "timestamp": event.get("timestamp")}
        session = event.get("sessionID") or session
        message = part.get("messageID")
        if kind == "step_start" and message:
            starts.add(message)
        elif kind == "step_finish" and message:
            completed.add(message)
        elif kind == "tool_use":
            state = part.get("state") or {}
            if state.get("status") == "completed" and part.get("tool") in ("read", "grep", "glob"):
                inputs = state.get("input") or {}
                path = inputs.get("filePath") or inputs.get("path")
                if isinstance(path, str) and path not in refs and len(refs) < 12:
                    refs.append(path[:512])
    if not last or last["type"] != "step_start" or not starts - completed:
        return None
    return {"kind": "provider_stream_silence", "last_event": last, "session_id": session,
            "model": (record.get("launch_route") or {}).get("model", record.get("model")), "source_refs": refs,
            "idle_limit_seconds": record.get("idle_timeout_seconds"),
            "note": "Unmatched model turn start followed by idle expiry; backend cause unknown"}


def diagnose_file(record):
    """Read raw events: normalized events intentionally omit model turn starts."""
    def rows():
        try:
            with Path(record["events"]).open() as stream:
                for _ in range(50000):
                    line = stream.readline(2 * 1024 * 1024)
                    if not line:
                        break
                    if not line.endswith("\n"):
                        while line and not line.endswith("\n"):
                            line = stream.readline(2 * 1024 * 1024)
                        continue
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            yield row
                    except ValueError:
                        continue
        except (OSError, KeyError):
            return
    return diagnose(record, rows())


def _family(model):
    if model.startswith("zai-coding-plan/"):
        return "glm"
    if model.startswith(("xiaomi-token-plan-sgp/", "mimo-")):
        return "mimo"
    return model


def _independent(roles):
    for producer, verifier in (("terra", "sol"), ("terra", "completion"), ("glm", "plan_reviewer")):
        a, b = (roles.get(producer) or {}).get("model"), (roles.get(verifier) or {}).get("model")
        if a and b and (a == b or (_family(a) in ("glm", "mimo") and _family(a) == _family(b))):
            return False
    return True


def recover(state, record, recovery, *, sleep=time.sleep):
    """Carry bounded navigation evidence; switch once after two same-route stalls."""
    evidence = record.get("stream_silence")
    if not evidence:
        return
    info("Provider stopped reporting progress during a model turn; retaining completed discovery paths for recovery.")
    recovery["stream_silence"] = evidence
    recovery["instruction"] += (
        " Observed provider stream silence, not a tool failure. Completed read/search paths are "
        "navigation hints in stream_silence.source_refs; verify them rather than trusting a partial report."
    )
    role = record.get("route_role", record["role"])
    settings = state.get("settings") or {}
    target = settings.get("stream_hang_fallbacks", {}).get(role)
    roles = settings.get("roles", {})
    route = roles.get(role)
    if not target or not route or route.get("engine", settings.get("engine")) != "opencode":
        return
    if route.get("model") != evidence.get("model"):
        return
    if any(r.get("stream_fallback", {}).get("role") == role
           for r in state.get("automatic_timeout_recoveries", [])):
        return
    prior = next((r for r in reversed(state.get("automatic_timeout_recoveries", []))
                  if r.get("role") == record["role"]), None)
    if not prior or prior.get("stream_silence", {}).get("model") != evidence.get("model"):
        return
    candidate = {**route, **target}
    if candidate == route:
        return
    if not _independent({**roles, role: candidate}):
        recovery["stream_fallback_blocked"] = "Configured fallback would violate verifier independence"
        info("The configured fallback conflicts with independent review; keeping the current route.")
        return
    recovery["stream_fallback"] = {"role": role, "from": dict(route), "to": dict(candidate)}
    roles[role] = candidate
    info(f"Switching the stalled role to the approved fallback {target['model']} at {target['reasoning_effort']} reasoning.")
    state.get("sessions", {}).pop(role, None)
    state.setdefault("session_rotations", []).append({"role": role, "at": recovery["at"],
        "reason": "Explicit stream-hang fallback after repeated idle silence"})
    recovery["retry_delay_seconds"] = 2
    sleep(2)
