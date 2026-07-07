from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    from .config import BACKGROUND_ENABLED, BACKGROUND_MAX_OUTPUT_CHARS, TASKS_DIR
    from .log import log_event
except ImportError:
    from config import BACKGROUND_ENABLED, BACKGROUND_MAX_OUTPUT_CHARS, TASKS_DIR
    from log import log_event


TASK_STATUSES = {"pending", "in_progress", "completed"}
SLOW_KEYWORDS = [
    "install",
    "build",
    "test",
    "deploy",
    "compile",
    "docker build",
    "pip install",
    "npm install",
    "pnpm install",
    "yarn install",
    "cargo build",
    "cargo test",
    "mvn test",
    "gradle build",
    "make",
]

background_lock = threading.Lock()
background_tasks: dict[str, dict[str, Any]] = {}
background_results: dict[str, dict[str, Any]] = {}


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str = "pending"
    owner: str | None = None
    blockedBy: list[str] = field(default_factory=list)
    createdAt: float = field(default_factory=time.time)
    updatedAt: float = field(default_factory=time.time)


def ensure_tasks_dir() -> None:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def create_task(subject: str, description: str, blockedBy: list[str] | None = None) -> str:
    ensure_tasks_dir()
    task = Task(
        id=new_task_id(),
        subject=subject,
        description=description,
        blockedBy=list(blockedBy or []),
    )
    if would_create_cycle(task.id, task.blockedBy):
        raise ValueError("Task dependencies would create a cycle.")
    save_task(task)
    log_event("TASK", "created", id=task.id, subject=subject, blocked=len(task.blockedBy))
    return render_task(task)


def list_tasks(include_completed: bool = True) -> str:
    tasks = load_tasks()
    if not include_completed:
        tasks = [task for task in tasks if task.status != "completed"]
    if not tasks:
        return "(no tasks)"
    return "\n".join(render_task_line(task) for task in sorted(tasks, key=lambda item: item.createdAt))


def get_task(task_id: str) -> str:
    return render_task(load_task(task_id))


def claim_task(task_id: str, owner: str = "main") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        raise ValueError(f"Task {task_id} is not pending.")
    if not can_start(task):
        missing = incomplete_dependencies(task)
        raise ValueError(f"Task {task_id} is blocked by: {', '.join(missing)}")
    task.status = "in_progress"
    task.owner = owner
    task.updatedAt = time.time()
    save_task(task)
    log_event("TASK", "claimed", id=task.id, owner=owner)
    return render_task(task)


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    task.status = "completed"
    task.updatedAt = time.time()
    save_task(task)
    unlocked = [candidate.id for candidate in load_tasks() if candidate.status == "pending" and can_start(candidate)]
    log_event("TASK", "completed", id=task.id, unlocked=len(unlocked))
    if unlocked:
        return f"{render_task(task)}\n\nUnlocked tasks: {', '.join(unlocked)}"
    return render_task(task)


def load_tasks() -> list[Task]:
    ensure_tasks_dir()
    tasks: list[Task] = []
    for path in sorted(TASKS_DIR.glob("task_*.json")):
        try:
            tasks.append(task_from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            log_event("WARN", "task_load_failed", path=path, error=exc)
    return tasks


def load_task(task_id: str) -> Task:
    path = task_path(task_id)
    if not path.exists():
        raise ValueError(f"Task not found: {task_id}")
    return task_from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_task(task: Task) -> None:
    ensure_tasks_dir()
    task.updatedAt = time.time()
    task_path(task.id).write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")


def can_start(task: Task) -> bool:
    return not incomplete_dependencies(task)


def incomplete_dependencies(task: Task) -> list[str]:
    tasks = {item.id: item for item in load_tasks()}
    missing: list[str] = []
    for dep_id in task.blockedBy:
        dependency = tasks.get(dep_id)
        if dependency is None or dependency.status != "completed":
            missing.append(dep_id)
    return missing


def would_create_cycle(new_id: str, dependencies: list[str]) -> bool:
    graph = {task.id: task.blockedBy for task in load_tasks()}
    graph[new_id] = dependencies
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, []):
            if visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return visit(new_id)


def should_run_background(block: Any) -> bool:
    if not BACKGROUND_ENABLED:
        return False
    name = block_attr(block, "name", "")
    normalized_name = str(name).replace("-", "_").replace(" ", "_").lower()
    tool_input = block_attr(block, "input", {}) or {}
    if bool(tool_input.get("run_in_background")) and normalized_name in {"bash", "spawn_subagent", "spawnsubagent"}:
        return True
    if normalized_name == "bash":
        command = str(tool_input.get("command", "")).lower()
        return any(keyword in command for keyword in SLOW_KEYWORDS)
    if normalized_name in {"spawn_subagent", "spawnsubagent", "agent"}:
        description = str(tool_input.get("description", "")).lower()
        return any(keyword in description for keyword in ("analyze", "investigate", "refactor", "review"))
    return False


def start_background_task(
    block: Any,
    handler: Callable[..., str],
    tool_input: dict[str, Any],
) -> str:
    task_id = new_background_id()
    name = str(block_attr(block, "name", "<unknown>"))
    summary = summarize_invocation(name, tool_input)
    with background_lock:
        background_tasks[task_id] = {
            "id": task_id,
            "name": name,
            "summary": summary,
            "status": "running",
            "startedAt": time.time(),
        }

    thread = threading.Thread(
        target=run_background_task,
        args=(task_id, handler, dict(tool_input)),
        daemon=True,
    )
    thread.start()
    log_event("BACKGROUND", "started", id=task_id, name=name, summary=summary)
    return f"[Background task {task_id} started] {summary}"


def run_background_task(task_id: str, handler: Callable[..., str], tool_input: dict[str, Any]) -> None:
    try:
        output = handler(**strip_control_args(tool_input))
        status = "completed"
        is_error = False
    except Exception as exc:
        output = f"ERROR: {exc}"
        status = "failed"
        is_error = True
    output = truncate(output, BACKGROUND_MAX_OUTPUT_CHARS)
    with background_lock:
        task = background_tasks.get(task_id, {})
        task["status"] = status
        task["completedAt"] = time.time()
        background_tasks[task_id] = task
        background_results[task_id] = {
            "id": task_id,
            "status": status,
            "summary": task.get("summary", ""),
            "output": output,
            "is_error": is_error,
        }
    log_event("BACKGROUND", status, id=task_id, chars=len(output))


def collect_background_notifications() -> list[str]:
    with background_lock:
        results = list(background_results.values())
        background_results.clear()
    return [render_background_notification(result) for result in results]


def render_background_notification(result: dict[str, Any]) -> str:
    return (
        "<task_notification>\n"
        f"  <task_id>{result['id']}</task_id>\n"
        f"  <status>{result['status']}</status>\n"
        f"  <summary>{escape_xml(str(result.get('summary', '')))}</summary>\n"
        f"  <output>{escape_xml(str(result.get('output', '')))}</output>\n"
        "</task_notification>"
    )


def strip_control_args(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in tool_input.items() if key != "run_in_background"}


def task_path(task_id: str) -> Path:
    ensure_tasks_dir()
    return TASKS_DIR / f"{task_id}.json"


def task_from_dict(data: dict[str, Any]) -> Task:
    status = data.get("status", "pending")
    if status not in TASK_STATUSES:
        status = "pending"
    return Task(
        id=str(data["id"]),
        subject=str(data.get("subject", "")),
        description=str(data.get("description", "")),
        status=status,
        owner=data.get("owner"),
        blockedBy=list(data.get("blockedBy", [])),
        createdAt=float(data.get("createdAt", time.time())),
        updatedAt=float(data.get("updatedAt", time.time())),
    )


def render_task(task: Task) -> str:
    return json.dumps(asdict(task), ensure_ascii=False, indent=2)


def render_task_line(task: Task) -> str:
    blocked = f" blockedBy={','.join(task.blockedBy)}" if task.blockedBy else ""
    owner = f" owner={task.owner}" if task.owner else ""
    return f"{task.id} [{task.status}]{owner}{blocked} - {task.subject}"


def new_task_id() -> str:
    return f"task_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"


def new_background_id() -> str:
    return f"bg_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"


def summarize_invocation(name: str, tool_input: dict[str, Any]) -> str:
    if "command" in tool_input:
        return str(tool_input["command"])[:300]
    if "description" in tool_input:
        return str(tool_input["description"])[:300]
    return name


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[background output truncated to {limit} chars]"


def escape_xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)
