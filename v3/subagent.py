from __future__ import annotations

from typing import Any

try:
    from .config import MODEL_ID, SUB_MAX_TOKENS, SUB_MAX_TURNS
    from .hook import trigger_hook
    from .llm import get_client
    from .skill import build_system
    from .tools import BASIC_TOOLS, TOOL_HANDLERS
except ImportError:
    from config import MODEL_ID, SUB_MAX_TOKENS, SUB_MAX_TURNS
    from hook import trigger_hook
    from llm import get_client
    from skill import build_system
    from tools import BASIC_TOOLS, TOOL_HANDLERS


SUB_TOOL_NAMES = {"bash", "read_file", "write_file", "edit_file", "glob"}


def spawn_subagent(description: str) -> str:
    client = get_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": description}]
    last_response_content: list[Any] = []

    for _turn in range(SUB_MAX_TURNS):
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=SUB_MAX_TOKENS,
            system=build_system(description, subagent=True),
            messages=messages,
            tools=sub_tools(),
        )
        last_response_content = list(response.content)

        if response.stop_reason == "end_turn":
            return extract_summary(messages, response.content)

        tool_blocks = [
            block for block in response.content if block_attr(block, "type") == "tool_use"
        ]
        if not tool_blocks:
            return extract_summary(messages, response.content)

        messages.append(
            {
                "role": "assistant",
                "content": [block_to_dict(block) for block in response.content],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [run_sub_tool(block) for block in tool_blocks],
            }
        )

    summary = extract_summary(messages, last_response_content)
    return (
        f"[Sub-agent reached SUB_MAX_TURNS={SUB_MAX_TURNS}.]\n"
        f"{summary}"
    )


def sub_tools() -> list[dict[str, Any]]:
    return [tool for tool in BASIC_TOOLS if tool["name"] in SUB_TOOL_NAMES]


def run_sub_tool(block: Any) -> dict[str, Any]:
    name = block_attr(block, "name")
    tool_input = block_attr(block, "input", {}) or {}
    tool_use_id = block_attr(block, "id")

    if name not in SUB_TOOL_NAMES:
        return tool_result(tool_use_id, f"BLOCKED: sub-agent cannot use tool {name}", True)

    blocked = trigger_hook("PreToolUse", block)
    if blocked is not None:
        return tool_result(tool_use_id, blocked, True)

    try:
        output = TOOL_HANDLERS[name](**tool_input)
    except Exception as exc:
        output = f"ERROR: {exc}"
        trigger_hook("PostToolUse", block, output)
        return tool_result(tool_use_id, output, True)

    trigger_hook("PostToolUse", block, output)
    return tool_result(tool_use_id, output)


def extract_summary(messages: list[dict[str, Any]], latest_content: list[Any]) -> str:
    latest_text = response_text(latest_content)
    if latest_text:
        return latest_text

    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        text = response_text(message_content(message))
        if text:
            return text

    return "[Sub-agent did not produce a text response]"


def message_content(message: dict[str, Any]) -> list[Any]:
    content = message.get("content", [])
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if hasattr(block, "dict"):
        return block.dict(exclude_none=True)
    raise TypeError(f"Unsupported response block: {type(block)!r}")


def block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def response_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if block_attr(block, "type") == "text":
            parts.append(block_attr(block, "text", ""))
    return "\n".join(part for part in parts if part)


def tool_result(tool_use_id: str, output: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": output,
    }
    if is_error:
        result["is_error"] = True
    return result
