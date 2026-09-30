"""Deterministic coverage of explicit recovery policy and raw stream evidence."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import autocode_stream_recovery as recovery

MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
GLM = "zai-coding-plan/glm-5.3"
SOL = "openai/gpt-6-sol"


class StreamRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state = {"settings": {"engine": "opencode", "roles": {
            "glm": {"model": MIMO, "model_pinned": True, "reasoning_effort": "high"},
            "plan_reviewer": {"model": GLM}}, "limits": {"automatic_retries": 3}},
            "sessions": {"glm": "old"}, "automatic_timeout_recoveries": []}
        self.record = {"role": "glm", "stage": "glm_revise", "timed_out": True,
                       "timeout_kind": "idle", "launch_route": {"model": MIMO},
                       "idle_timeout_seconds": 300, "activity": {"active_tool_count": 0}}
        self.events = [
            {"type": "tool_use", "sessionID": "s", "part": {"tool": "read",
             "state": {"status": "completed", "input": {"filePath": "src.py"}}}},
            {"type": "step_start", "timestamp": 100, "sessionID": "s",
             "part": {"id": "p", "messageID": "m"}}]

    def configure(self, value):
        return recovery.configure(self.state["settings"],
                                  SimpleNamespace(stream_hang_fallback=[value]))

    def recover(self):
        self.record["stream_silence"] = recovery.diagnose(self.record, self.events)
        context = {"at": "now", "instruction": "Preserve evidence."}
        recovery.recover(self.state, self.record, context, sleep=lambda _: None)
        return context

    def test_raw_silence_evidence_is_durable_and_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "events.jsonl"
            path.write_text("\n".join(map(json.dumps, self.events)) + "\n")
            self.record["events"] = str(path)
            evidence = recovery.diagnose_file(self.record)
        self.assertEqual("provider_stream_silence", evidence["kind"])
        self.assertEqual(MIMO, evidence["model"])
        self.assertEqual("s", evidence["session_id"])
        self.assertEqual(["src.py"], evidence["source_refs"])
        self.assertEqual("step_start", evidence["last_event"]["type"])

    def test_completed_turn_tool_timeouts_and_live_tools_do_not_classify(self):
        end = {"type": "step_finish", "part": {"messageID": "m", "reason": "stop"}}
        self.assertIsNone(recovery.diagnose(self.record, self.events + [end]))
        for update in ({"timeout_kind": "tool"}, {"timed_out": False},
                       {"activity": {"active_tool_count": 1}},
                       {"activity": {"process_fallback": True}}):
            self.assertIsNone(recovery.diagnose({**self.record, **update}, self.events))

    def test_no_implicit_fallback_or_budget_increase(self):
        before = copy.deepcopy(self.state)
        context = self.recover()
        self.assertIn("navigation hints", context["instruction"])
        self.assertEqual(before, self.state)

    def test_explicit_fallback_switches_pinned_role_once_after_repeat(self):
        self.configure(f"glm={SOL},medium")
        first = self.recover()
        self.assertNotIn("stream_fallback", first)
        self.state["automatic_timeout_recoveries"].append({**first, "role": "glm"})
        limits = copy.deepcopy(self.state["settings"]["limits"])
        second = self.recover()
        self.assertEqual(SOL, self.state["settings"]["roles"]["glm"]["model"])
        self.assertEqual("medium", self.state["settings"]["roles"]["glm"]["reasoning_effort"])
        self.assertTrue(self.state["settings"]["roles"]["glm"]["model_pinned"])
        self.assertNotIn("glm", self.state["sessions"])
        self.assertEqual(limits, self.state["settings"]["limits"])
        self.state["automatic_timeout_recoveries"].append({**second, "role": "glm"})
        self.configure(f"glm={MIMO},high")
        self.record["launch_route"]["model"] = SOL
        self.assertNotIn("stream_fallback", self.recover())

    def test_fallback_cannot_break_verifier_independence(self):
        self.configure(f"glm={GLM},high")
        first = self.recover()
        self.state["automatic_timeout_recoveries"].append({**first, "role": "glm"})
        second = self.recover()
        self.assertIn("stream_fallback_blocked", second)
        self.assertEqual(MIMO, self.state["settings"]["roles"]["glm"]["model"])

    def test_configuration_rejects_bad_roles_engines_and_excessive_effort(self):
        for value in ("invalid", f"missing={SOL},medium", f"glm={SOL},xhigh",
                      "glm=bare,medium"):
            with self.assertRaises(ValueError):
                self.configure(value)
        self.state["settings"]["roles"]["glm"]["engine"] = "codex"
        with self.assertRaises(ValueError):
            self.configure(f"glm={SOL},medium")
