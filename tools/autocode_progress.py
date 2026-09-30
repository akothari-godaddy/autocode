"""Concise INFO-level CLI progress without prompts, commands or raw tool output."""
import re
from pathlib import PurePath

STAGES = {
    "requirements_gather": ("Requirements", "reading the repository and clarifying the requested outcome"),
    "recognize_workflow": ("Job recognizer", "choosing the workflow for this task"),
    "investigate_bug": ("Investigator", "reproducing the bug and checking its cause"),
    "investigate_stuck": ("Investigator", "checking why the run stopped making progress"),
    "astra_discovery": ("Planner", "preparing an implementation plan"),
    "glm_revise": ("Planner", "revising the plan in response to review"),
    "astra_challenge": ("Plan Reviewer", "checking the plan for missing requirements and risks"),
    "astra_finalize": ("Plan Reviewer", "finalizing the plan for approval"),
    "astra_plan": ("Planner", "choosing the next bounded task"),
    "terra": ("Builder", "implementing the task and running checks"),
    "sol": ("Validator", "checking the changes and their test evidence"),
    "astra_review": ("Completion Owner", "checking whether the requested work is complete"),
    "astra_checkpoint": ("Completion Owner", "reviewing the completed milestone"),
    "astra_resolve": ("Resolver", "working through unresolved review findings"),
}


def info(message):
    print(f"INFO: {message}", flush=True)


def tool_description(tool, inputs):
    """Use an allowlisted description, never provider prose or command contents."""
    inputs = inputs if isinstance(inputs, dict) else {}
    path = inputs.get('filePath') or inputs.get('path') or inputs.get('file_path')
    name = PurePath(path).name if isinstance(path, str) else ''
    safe_name = name if re.fullmatch(r'[\w.-]{1,80}', name) and not name.startswith('.') else ''
    suffix = f' {safe_name}' if safe_name else ' repository files'
    if tool == 'read':
        return 'reading' + suffix
    if tool in ('grep', 'glob'):
        return 'searching' + suffix
    if tool in ('edit', 'write', 'apply_patch', 'file_change'):
        return 'updating' + suffix
    if tool in ('bash', 'shell', 'command_execution'):
        command = inputs.get('command', '')
        if isinstance(command, str):
            if re.search(r'\b(pytest|unittest|vitest)\b|\bnpm\b.*\btest\b', command):
                return 'running the tests'
            if re.search(r'\b(ruff|mypy|tsc|typecheck)\b', command):
                return 'checking formatting or types'
            if re.search(r'\bgit\s+diff\b', command):
                return 'reviewing the code changes'
            if re.search(r'\bnpm\b.*\bbuild\b', command):
                return 'building the project'
        return 'running a repository command'
    return 'using a repository tool'


class StageProgress:
    def __init__(self, stage):
        self.label, self.work = STAGES.get(stage, ("Agent", "working on the current task"))
        self.completed = 0
        self.running_tool = False
        self.last_action = None

    def started(self):
        info(f"{self.label} started: {self.work}.")

    def activity(self, snapshot):
        completed = snapshot.get("completed_tool_count", 0)
        if completed > self.completed:
            action = snapshot.get('completed_action')
            if action:
                info(f"{self.label} finished {action}.")
            else:
                info(f"{self.label} completed {completed - self.completed} repository action(s); continuing the task.")
            self.completed = completed
        running = snapshot.get("activity") == "running_tool"
        action = snapshot.get('detail')
        if running and (not self.running_tool or action != self.last_action):
            info(f"{self.label} is {action if action else 'running a repository tool or check'}.")
            self.last_action = action
        self.running_tool = running

    def finished(self, exit_code, timed_out=False, interrupted=False):
        if interrupted:
            info(f"{self.label} was interrupted; saved work will be inspected before continuing.")
        elif timed_out:
            info(f"{self.label} stopped at its execution deadline; partial work and logs were retained.")
        elif exit_code == 0:
            info(f"{self.label} finished its response; checking the report before advancing.")
        else:
            info(f"{self.label} stopped without a successful response; inspecting the failure.")
