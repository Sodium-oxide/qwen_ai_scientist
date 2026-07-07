from __future__ import annotations

import argparse
from typing import Any

try:
    from .config import MAX_TOKENS, MODEL_ID
    from .hook import trigger_hook
    from .llm import get_client
    from .skill import build_system
    from .tools import TOOL_HANDLERS, TOOLS
except ImportError:
    from config import MAX_TOKENS, MODEL_ID
    from hook import trigger_hook
    from llm import get_client
    from skill import build_system
    from tools import TOOL_HANDLERS, TOOLS


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


def run_tool(block: Any) -> dict[str, Any]:
    name = block_attr(block, "name")
    tool_input = block_attr(block, "input", {}) or {}
    tool_use_id = block_attr(block, "id")

    blocked = trigger_hook("PreToolUse", block)
    if blocked is not None:
        return tool_result(tool_use_id, blocked, is_error=True)

    try:
        handler = TOOL_HANDLERS[name]
        output = handler(**tool_input)
    except Exception as exc:
        output = f"ERROR: {exc}"
        trigger_hook("PostToolUse", block, output)
        return tool_result(tool_use_id, output, is_error=True)

    trigger_hook("PostToolUse", block, output)
    return tool_result(tool_use_id, output)


def run_agent(user_input: str) -> str:
    client = get_client()
    injected = trigger_hook("UserPromptSubmit", user_input)
    prompt = injected if injected is not None else user_input
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    while True:
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=build_system(user_input),
            messages=messages,
            tools=TOOLS,
        )

        if response.stop_reason == "end_turn":
            final_text = response_text(response.content)
            stop_injection = trigger_hook("Stop", final_text)
            if stop_injection is not None:
                messages.append({"role": "user", "content": stop_injection})
                continue
            print(final_text)
            return final_text

        tool_blocks = [
            block for block in response.content if block_attr(block, "type") == "tool_use"
        ]
        if not tool_blocks:
            final_text = response_text(response.content)
            trigger_hook("Stop", final_text)
            print(final_text)
            return final_text

        messages.append(
            {
                "role": "assistant",
                "content": [block_to_dict(block) for block in response.content],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [run_tool(block) for block in tool_blocks],
            }
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v3 Modular + Sub-agent loop.")
    parser.add_argument("prompt", nargs="*", help="Task prompt. If omitted, read one line.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    user_input = " ".join(args.prompt).strip()
    if not user_input:
        user_input = input("User> ").strip()
    if not user_input:
        raise SystemExit("Empty prompt.")
    run_agent(user_input)


if __name__ == "__main__":
    main()
