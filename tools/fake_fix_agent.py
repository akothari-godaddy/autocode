#!/usr/bin/env python3
"""Offline stand-in for ``codex exec`` in autocode fix tests; never contacts a model.

AUTOCODE_FAKE_FIX_SCRIPT names a JSON file: {"calls": [step, ...]}. Each call
consumes the next step, in order:

    {"role": "builder" | "reviewer",
     "write": {"path": "content" | null},     # files to write (null deletes)
     "report": {...} | "garbage" | null,      # JSON report; "garbage" writes invalid JSON
     "commit": false,                         # commit the written files (a disobedient Builder)
     "exit": 0,                               # process exit code
     "sleep": 0}                              # seconds to wait before exiting

Every invocation is appended to <script>.log as {"argv", "prompt", "step"} so a
test can assert what the runner sent (resumed sessions, feedback, read-only).
"""
import json
import sys
import time
import uuid
from pathlib import Path
import os

argv = sys.argv[1:]
if argv[:2] == ["login", "status"]:
    print("Logged in (offline fixture)")
    raise SystemExit(0)
script_path = Path(os.environ["AUTOCODE_FAKE_FIX_SCRIPT"])
script = json.loads(script_path.read_text())
counter = script_path.with_suffix(".count")
index = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(index + 1))
prompt = sys.stdin.read()
steps = script["calls"]
step = steps[index] if index < len(steps) else {"role": "any", "report": None, "exit": 9}
with script_path.with_suffix(".log").open("a") as log:
    log.write(json.dumps({"argv": argv, "prompt": prompt, "step": index}) + "\n")

sandbox = argv[argv.index("--sandbox") + 1] if "--sandbox" in argv else ""
role = "reviewer" if sandbox == "read-only" else "builder"
if step.get("role") not in (role, "any"):
    print(json.dumps({"type": "error", "message": f"fixture expected {step.get('role')}, got {role}"}))
    raise SystemExit(8)
session = argv[argv.index("resume") + 1] if "resume" in argv else str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
workspace = Path(argv[argv.index("-C") + 1]) if "-C" in argv else Path.cwd()
for relative, content in (step.get("write") or {}).items():
    target = workspace / relative
    if content is None:
        target.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
if step.get("commit"):
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "-c", "user.name=agent", "-c", "user.email=agent@example.test", "commit", "-qm",
                    "agent commit"], cwd=workspace, check=True)
time.sleep(step.get("sleep", 0))
output = Path(argv[argv.index("-o") + 1])
report = step.get("report")
if report == "garbage":
    output.write_text("this is not json")
elif report is not None:
    output.write_text(json.dumps(report))
print(json.dumps({"type": "turn.completed",
                  "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 200,
                            "reasoning_output_tokens": 50}}), flush=True)
raise SystemExit(step.get("exit", 0))
