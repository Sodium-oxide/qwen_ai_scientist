from __future__ import annotations

import os
import re
from typing import Any


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


def block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def check_permission(block: Any) -> None:
    name = block_attr(block, "name")
    tool_input = block_attr(block, "input", {}) or {}

    if name != "bash":
        return

    command = str(tool_input.get("command", ""))
    deny_known_dangerous(command)

    if matches_any(command, RISKY_PATTERNS):
        require_user_approval(command)


def deny_known_dangerous(command: str) -> None:
    if matches_any(command, DENY_PATTERNS):
        raise PermissionError(f"Blocked dangerous command: {command}")


def require_user_approval(command: str) -> None:
    if os.environ.get("AGENT_AUTO_APPROVE", "").lower() in {"1", "true", "yes"}:
        return

    answer = input(f"Allow risky command? {command}\n[y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise PermissionError(f"User denied command: {command}")


def matches_any(command: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in patterns)
