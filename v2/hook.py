from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from .tools import WORKDIR, path_escapes_workspace
except ImportError:
    from tools import WORKDIR, path_escapes_workspace


HookHandler = Callable[..., str | None]
HOOKS: dict[str, list[HookHandler]] = defaultdict(list)
LOG_PATH = Path(__file__).with_name("agent.log")
LARGE_OUTPUT_CHARS = 20_000

STATS = {
    "tool_calls": 0,
    "blocked": 0,
    "large_outputs": 0,
}

DENY_PATTERNS = [
    r"\brm\s+-rf\s+[/\\~]?",
    r"\bsudo\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
    r"\bdiskpart\b",
    r"\bformat\s+[a-z]:",
    r"\bdel\s+/[sq]\b",
    r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b",
]

RISKY_PATTERNS = [
    r"\brm\b",
    r"\bdel\b",
    r"\brmdir\b",
    r"\bchmod\s+777\b",
    r"\bchown\b",
    r"\bgit\s+push\b.*\b--force\b",
]


def register_hook(event: str, handler: HookHandler) -> None:
    HOOKS[event].append(handler)


def trigger_hook(event: str, *args: Any, **kwargs: Any) -> str | None:
    """Run handlers in registration order and return the first blocking result."""
    for handler in HOOKS.get(event, []):
        result = handler(*args, **kwargs)
        if result is not None:
            return result
    return None


def block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def matches_any(command: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in patterns)


def append_log(line: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {line}\n")


def permission_hook(block: Any) -> str | None:
    name = block_attr(block, "name")
    tool_input = block_attr(block, "input", {}) or {}

    if name == "bash":
        command = str(tool_input.get("command", ""))
        if matches_any(command, DENY_PATTERNS):
            STATS["blocked"] += 1
            return f"BLOCKED: dangerous command denied: {command}"
        if matches_any(command, RISKY_PATTERNS) and not approve(
            f"Allow risky command? {command}"
        ):
            STATS["blocked"] += 1
            return f"BLOCKED: user denied command: {command}"

    if name in {"write_file", "edit_file", "read_file"}:
        path = str(tool_input.get("path", ""))
        if path and path_escapes_workspace(path):
            STATS["blocked"] += 1
            return f"BLOCKED: path escapes workspace: {path}"

    return None


def log_hook(block: Any) -> str | None:
    name = block_attr(block, "name", "<unknown>")
    tool_input = block_attr(block, "input", {}) or {}
    STATS["tool_calls"] += 1
    append_log(f"tool_start name={name} input={compact_input(tool_input)}")
    return None


def large_output_hook(block: Any, output: str) -> str | None:
    name = block_attr(block, "name", "<unknown>")
    if len(output) > LARGE_OUTPUT_CHARS:
        STATS["large_outputs"] += 1
        append_log(f"large_output name={name} chars={len(output)}")
    append_log(f"tool_end name={name} chars={len(output)}")
    return None


def context_inject_hook(user_input: str) -> str | None:
    if os.environ.get("AGENT_DISABLE_CONTEXT_INJECTION"):
        return None
    return (
        f"{user_input}\n\n"
        f"[v2 context]\n"
        f"- workspace: {WORKDIR}\n"
        f"- hook events: {', '.join(sorted(HOOKS))}\n"
    )


def summary_hook(final_text: str | None = None) -> str | None:
    append_log(
        "summary "
        f"tool_calls={STATS['tool_calls']} "
        f"blocked={STATS['blocked']} "
        f"large_outputs={STATS['large_outputs']}"
    )
    return None


def approve(question: str) -> bool:
    if os.environ.get("AGENT_AUTO_APPROVE", "").lower() in {"1", "true", "yes"}:
        return True
    answer = input(f"{question}\n[y/N] ").strip().lower()
    return answer in {"y", "yes"}


def compact_input(tool_input: dict[str, Any]) -> str:
    rendered = repr(tool_input)
    if len(rendered) <= 500:
        return rendered
    return rendered[:500] + "...[truncated]"


def register_default_hooks() -> None:
    if HOOKS:
        return
    register_hook("UserPromptSubmit", context_inject_hook)
    register_hook("PreToolUse", permission_hook)
    register_hook("PreToolUse", log_hook)
    register_hook("PostToolUse", large_output_hook)
    register_hook("Stop", summary_hook)


register_default_hooks()
