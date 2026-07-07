from __future__ import annotations

import glob as glob_module
import subprocess
from pathlib import Path
from typing import Callable

try:
    from .config import BASH_TIMEOUT_SECONDS, MAX_OUTPUT_CHARS, WORKDIR
except ImportError:
    from config import BASH_TIMEOUT_SECONDS, MAX_OUTPUT_CHARS, WORKDIR


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated to {limit} characters]"


def safe_path(path: str) -> Path:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else WORKDIR / raw
    resolved = candidate.resolve()
    if not resolved.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def path_escapes_workspace(path: str) -> bool:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else WORKDIR / raw
    return not candidate.resolve().is_relative_to(WORKDIR)


def relative(path: Path) -> str:
    return str(path.relative_to(WORKDIR)).replace("\\", "/")


def bash(command: str) -> str:
    completed = subprocess.run(
        command,
        cwd=WORKDIR,
        shell=True,
        text=True,
        capture_output=True,
        timeout=BASH_TIMEOUT_SECONDS,
    )
    output = []
    if completed.stdout:
        output.append(completed.stdout)
    if completed.stderr:
        output.append(completed.stderr)
    if not output:
        output.append("(no output)")
    output.append(f"\n[exit_code={completed.returncode}]")
    return truncate("".join(output))


def read_file(path: str, limit: int | None = None) -> str:
    target = safe_path(path)
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    if limit is not None and limit >= 0 and len(lines) > limit:
        visible = lines[:limit]
        visible.append(f"\n...[truncated after {limit} lines]\n")
        lines = visible

    return "".join(f"{index + 1:>4} | {line}" for index, line in enumerate(lines))


def write_file(path: str, content: str) -> str:
    target = safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {relative(target)}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    target = safe_path(path)
    content = target.read_text(encoding="utf-8", errors="replace")
    if old_text not in content:
        raise ValueError("old_text was not found.")
    updated = content.replace(old_text, new_text, 1)
    target.write_text(updated, encoding="utf-8")
    return f"Replaced one occurrence in {relative(target)}"


def glob(pattern: str, limit: int = 200) -> str:
    matches: list[str] = []
    search_pattern = str(WORKDIR / pattern)
    for match in glob_module.glob(search_pattern, recursive=True):
        path = Path(match).resolve()
        if path.is_relative_to(WORKDIR):
            matches.append(relative(path))

    matches = sorted(set(matches))[:limit]
    if not matches:
        return "(no matches)"
    return "\n".join(matches)


def spawn_subagent(description: str) -> str:
    try:
        from .subagent import spawn_subagent as run_subagent
    except ImportError:
        from subagent import spawn_subagent as run_subagent

    return run_subagent(description)


def compact(focus: str = "") -> str:
    if focus:
        return f"Compaction requested. Focus: {focus}"
    return "Compaction requested."


def create_task(subject: str, description: str, blockedBy: list[str] | None = None) -> str:
    try:
        from .task_system import create_task as task_create
    except ImportError:
        from task_system import create_task as task_create
    return task_create(subject, description, blockedBy)


def list_tasks(include_completed: bool = True) -> str:
    try:
        from .task_system import list_tasks as task_list
    except ImportError:
        from task_system import list_tasks as task_list
    return task_list(include_completed)


def get_task(task_id: str) -> str:
    try:
        from .task_system import get_task as task_get
    except ImportError:
        from task_system import get_task as task_get
    return task_get(task_id)


def claim_task(task_id: str, owner: str = "main") -> str:
    try:
        from .task_system import claim_task as task_claim
    except ImportError:
        from task_system import claim_task as task_claim
    return task_claim(task_id, owner)


def complete_task(task_id: str) -> str:
    try:
        from .task_system import complete_task as task_complete
    except ImportError:
        from task_system import complete_task as task_complete
    return task_complete(task_id)


def spawn_teammate(name: str, task: str = "") -> str:
    try:
        from .agent_teams import spawn_teammate as team_spawn
    except ImportError:
        from agent_teams import spawn_teammate as team_spawn
    return team_spawn(name, task)


def send_message(to: str, content: str, type: str = "message") -> str:
    try:
        from .agent_teams import LEAD, send_message as team_send
    except ImportError:
        from agent_teams import LEAD, send_message as team_send
    return team_send(LEAD, to, content, type=type)


def check_inbox(agent: str = "lead") -> str:
    try:
        from .agent_teams import check_inbox as team_check
    except ImportError:
        from agent_teams import check_inbox as team_check
    return team_check(agent)


def request_shutdown(teammate: str, reason: str = "") -> str:
    try:
        from .agent_teams import request_shutdown as team_shutdown
    except ImportError:
        from agent_teams import request_shutdown as team_shutdown
    return team_shutdown(teammate, reason)


def request_plan(teammate: str, prompt: str) -> str:
    try:
        from .agent_teams import request_plan as team_plan
    except ImportError:
        from agent_teams import request_plan as team_plan
    return team_plan(teammate, prompt)


def review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    try:
        from .agent_teams import review_plan as team_review
    except ImportError:
        from agent_teams import review_plan as team_review
    return team_review(request_id, approve, feedback)


BASIC_TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run slow commands asynchronously and notify later.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "limit": {
                    "type": "integer",
                    "description": "Optional maximum number of lines to read.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "content": {"type": "string", "description": "New file content."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace the first exact occurrence of text in a workspace file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "old_text": {"type": "string", "description": "Text to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "glob",
        "description": "Find workspace files using a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, for example '**/*.py'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return.",
                },
            },
            "required": ["pattern"],
        },
    },
]

SUBAGENT_TOOL = {
    "name": "spawn_subagent",
    "description": (
        "Delegate an open-ended coding subtask to an isolated sub-agent. "
        "Use this for analysis or investigation that may require multiple tool calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "The subtask goal and expected deliverable.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run the sub-agent asynchronously and notify later.",
            }
        },
        "required": ["description"],
    },
}

COMPACT_TOOL = {
    "name": "compact",
    "description": (
        "Request context compaction when the conversation history is getting too large. "
        "Use focus to preserve the most important topic."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "description": "Optional area that the summary should preserve.",
            }
        },
        "required": [],
    },
}

TASK_TOOLS = [
    {
        "name": "create_task",
        "description": "Create a persistent DAG task with optional dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Short task title."},
                "description": {"type": "string", "description": "Detailed task context."},
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task ids that must be completed first.",
                },
            },
            "required": ["subject", "description"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List persistent tasks and their DAG state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_completed": {
                    "type": "boolean",
                    "description": "Whether completed tasks should be included.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_task",
        "description": "Read one persistent task by id.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task id."}},
            "required": ["task_id"],
        },
    },
    {
        "name": "claim_task",
        "description": "Claim a pending task if all dependencies are completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id."},
                "owner": {"type": "string", "description": "Agent or worker name."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task completed and report newly unblocked downstream tasks.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task id."}},
            "required": ["task_id"],
        },
    },
]

TEAM_TOOLS = [
    {
        "name": "spawn_teammate",
        "description": "Start a named teammate agent in a background idle loop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Stable teammate name."},
                "task": {
                    "type": "string",
                    "description": "Optional initial task to place in the teammate inbox.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a mailbox message from Lead to another agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Target teammate or lead."},
                "content": {"type": "string", "description": "Message body."},
                "type": {
                    "type": "string",
                    "description": "Message type, usually message/result.",
                },
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "check_inbox",
        "description": "Read and clear an agent mailbox, routing Lead protocol responses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent name. Defaults to lead.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "request_shutdown",
        "description": "Ask a teammate to stop gracefully through the protocol state machine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teammate": {"type": "string", "description": "Target teammate name."},
                "reason": {"type": "string", "description": "Optional shutdown reason."},
            },
            "required": ["teammate"],
        },
    },
    {
        "name": "request_plan",
        "description": "Ask a teammate to submit a plan for Lead approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teammate": {"type": "string", "description": "Target teammate name."},
                "prompt": {"type": "string", "description": "Planning request."},
            },
            "required": ["teammate", "prompt"],
        },
    },
    {
        "name": "review_plan",
        "description": "Approve or reject a teammate plan approval request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "Protocol request id."},
                "approve": {"type": "boolean", "description": "Whether to approve the plan."},
                "feedback": {"type": "string", "description": "Optional feedback."},
            },
            "required": ["request_id", "approve"],
        },
    },
]

TOOLS = BASIC_TOOLS + [SUBAGENT_TOOL, COMPACT_TOOL] + TASK_TOOLS + TEAM_TOOLS

TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "bash": bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob,
    "spawn_subagent": spawn_subagent,
    "compact": compact,
    "create_task": create_task,
    "list_tasks": list_tasks,
    "get_task": get_task,
    "claim_task": claim_task,
    "complete_task": complete_task,
    "spawn_teammate": spawn_teammate,
    "send_message": send_message,
    "check_inbox": check_inbox,
    "request_shutdown": request_shutdown,
    "request_plan": request_plan,
    "review_plan": review_plan,
}
