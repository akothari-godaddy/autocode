#!/usr/bin/env python3
"""Run one AutoCode stage on the `claude` CLI (trial glue, not part of the repository).

argv: workspace sandbox model effort schema report. The stage prompt arrives on stdin.
Claude's stream is translated into the Codex-style events AutoCode's liveness watchdog and usage
accounting read; the schema-checked report is written to `report`.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

workspace, sandbox, model, effort, schema_path, report = sys.argv[1:7]
prompt = sys.stdin.read()
schema = json.loads(Path(schema_path).read_text())

APPEND = (
    "You are one stage of an automated engineering pipeline. Return your final report ONLY through the "
    "structured output, exactly matching the schema. If the prompt asks you to write the report to a file, do "
    "not: the structured output is the report. Work only inside the current directory. Never run git commit, "
    "git push, git checkout, git reset, git stash or git branch, and never edit anything under .git/ or "
    ".autocode/ except through the capture command the prompt names."
)
DENY_GIT = ["Bash(git commit:*)", "Bash(git push:*)", "Bash(git checkout:*)", "Bash(git reset:*)",
            "Bash(git stash:*)", "Bash(git branch:*)", "Bash(git rebase:*)", "Bash(git merge:*)"]
command = ["claude", "-p", "--no-session-persistence", "--setting-sources", "project", "--model", model,
           "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--json-schema", json.dumps(schema), "--append-system-prompt", APPEND]
if effort and "haiku" not in model:
    command += ["--effort", effort]
if sandbox == "read-only":
    command += ["--permission-mode", "default", "--allowedTools", "Read", "Grep", "Glob", "Bash",
                "--disallowedTools", "Edit", "Write", "NotebookEdit", *DENY_GIT]
else:
    command += ["--permission-mode", "acceptEdits", "--allowedTools", "Read", "Grep", "Glob", "Bash", "Edit",
                "Write", "--disallowedTools", *DENY_GIT]

env = {k: v for k, v in os.environ.items()
       if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION")}


def emit(row):
    sys.stdout.write(json.dumps(row) + "\n")
    sys.stdout.flush()


child = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
child.stdin.write(prompt)
child.stdin.close()

text, last_emit, tools, result = {}, {}, {}, None
for line in child.stdout:
    try:
        row = json.loads(line)
    except ValueError:
        continue
    kind = row.get("type")
    if kind == "stream_event":
        event = row.get("event") or {}
        if event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            key = (row.get("session_id"), event.get("index"), delta.get("type"))
            piece = delta.get("text") or delta.get("thinking") or ""
            if piece:
                text[key] = text.get(key, "") + piece
                now = time.monotonic()
                if now - last_emit.get(key, 0) >= 1.0:
                    last_emit[key] = now
                    item_type = "reasoning" if delta.get("type") == "thinking_delta" else "agent_message"
                    emit({"type": "item.updated", "item": {"id": "t%s-%s" % (key[1], abs(hash(key[0])) % 10**6),
                                                           "type": item_type, "text": text[key]}})
    elif kind == "assistant":
        for block in (row.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                name, args = block.get("name"), block.get("input") or {}
                shown = args.get("command") if name == "Bash" and isinstance(args.get("command"), str) \
                    else name + " " + json.dumps(args)[:400]
                tools[block["id"]] = shown
                emit({"type": "item.started", "item": {"id": block["id"], "type": "command_execution",
                                                       "command": shown, "status": "in_progress"}})
    elif kind == "user":
        for block in (row.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(part.get("text", "") for part in body if isinstance(part, dict))
                emit({"type": "item.completed", "item": {
                    "id": block.get("tool_use_id"), "type": "command_execution",
                    "command": tools.get(block.get("tool_use_id"), ""), "status": "completed",
                    "aggregated_output": str(body or "")[:20000],
                    "exit_code": 1 if block.get("is_error") else 0}})
    elif kind == "result":
        result = row
child.wait()

usage = (result or {}).get("usage") or {}
cached = usage.get("cache_read_input_tokens") or 0
fresh = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
totals = {"input_tokens": fresh + cached, "cached_input_tokens": cached,
          "output_tokens": usage.get("output_tokens") or 0, "reasoning_output_tokens": 0}
report_value = (result or {}).get("structured_output")
if result and not result.get("is_error") and isinstance(report_value, dict):
    Path(report).write_text(json.dumps(report_value, indent=2) + "\n")
    emit({"type": "turn.completed", "usage": totals, "cost_usd": result.get("total_cost_usd"), "model": model})
    sys.exit(0)
emit({"type": "turn.failed", "usage": totals, "cost_usd": (result or {}).get("total_cost_usd"),
      "error": {"message": ((result or {}).get("result") or "claude returned no structured report")[:500]}})
sys.exit(1)
