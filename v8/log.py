from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from .config import LOG_COLOR, LOG_PATH
except ImportError:
    from config import LOG_COLOR, LOG_PATH


COLORS = {
    "reset": "\033[0m",
    "gray": "\033[90m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}

CATEGORY_COLORS = {
    "WARN": "yellow",
    "ERROR": "red",
    "SUBAGENT": "magenta",
    "USER": "cyan",
    "COMPACT": "yellow",
    
    "MCP": "cyan",
    
    "TASK": "cyan",
    "CRON": "cyan",
    "TODO": "cyan",
}


def log_event(category: str, event: str, **data: Any) -> None:
    category = category.upper()
    line = format_event(category, event, **data)
    write_log_line(line)
    print(colorize(category, line))


def format_event(category: str, event: str, **data: Any) -> str:
    details = ", ".join(f"{key}={format_value(value)}" for key, value in data.items())
    if details:
        return f"[{category}] {event}: {details}"
    return f"[{category}] {event}"


def format_value(value: Any) -> str:
    text = str(value).replace("\n", "\\n")
    if len(text) > 2000:
        return text[:2000] + "...[truncated]"
    return text


def write_log_line(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {line}\n")


def colorize(category: str, line: str) -> str:
    if not LOG_COLOR:
        return line
    lowered = line.lower()
    if category == "ERROR" or "error" in lowered or "] blocked:" in lowered:
        color = "red"
    elif "warn" in lowered or category in {"WARN", "COMPACT"}:
        color = "yellow"
    else:
        color = CATEGORY_COLORS.get(category, "gray")
    return f"{COLORS[color]}{line}{COLORS['reset']}"
