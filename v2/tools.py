from __future__ import annotations

import glob as glob_module
import os
import subprocess
from pathlib import Path
from typing import Callable


WORKDIR = Path(os.environ.get("AGENT_WORKDIR", os.getcwd())).resolve()
MAX_OUTPUT_CHARS = 50_000
BASH_TIMEOUT_SECONDS = 120


def get_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The anthropic package is not installed. Run: pip install -r v2/requirements.txt"
        ) from exc
    return Anthropic()


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


TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                }
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


TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "bash": bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob,
}
