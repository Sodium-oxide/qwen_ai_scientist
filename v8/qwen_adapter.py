from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class QwenResponse:
    content: list[dict[str, Any]]
    stop_reason: str | None = None


class QwenMessages:
    def __init__(self, api_key: str, default_model: str, api_base: str = "") -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.api_base = api_base

    def create(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        system: str = "",
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> QwenResponse:
        try:
            import dashscope
            from dashscope import Generation
        except ImportError as exc:
            raise RuntimeError("The dashscope package is not installed. Run: pip install dashscope") from exc

        if self.api_base:
            dashscope.base_http_api_url = self.api_base
        qwen_messages = to_qwen_messages(system, messages, tools or [])
        kwargs: dict[str, Any] = {
            "model": self.effective_model(model),
            "messages": qwen_messages,
            "result_format": "message",
            "api_key": self.api_key,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = Generation.call(**kwargs)
        ensure_success(response)
        text = extract_qwen_text(response)
        return QwenResponse(content=parse_qwen_content(text, tools or []))

    def effective_model(self, requested: str | None) -> str:
        value = str(requested or "").strip()
        if not value:
            return self.default_model
        if value.startswith("claude") or value.startswith("anthropic."):
            return self.default_model
        return value


class QwenClient:
    def __init__(self, api_key: str, model: str = "qwen-plus", api_base: str = "") -> None:
        if not api_key:
            raise RuntimeError("Qwen API key is not set. Set QWEN_API_KEY or DASHSCOPE_API_KEY.")
        self.messages = QwenMessages(api_key=api_key, default_model=model, api_base=api_base)


def to_qwen_messages(system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    rendered: list[dict[str, str]] = []
    full_system = system
    if tools:
        full_system += "\n\n" + tool_protocol_prompt(tools)
    if full_system.strip():
        rendered.append({"role": "system", "content": full_system.strip()})

    for message in messages:
        role = str(message.get("role", "user"))
        if role not in {"system", "user", "assistant"}:
            role = "user"
        rendered.append({"role": role, "content": render_content(message.get("content", ""))})
    return rendered


def tool_protocol_prompt(tools: list[dict[str, Any]]) -> str:
    catalog = [
        {
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema", {}),
        }
        for tool in tools
    ]
    return (
        "You have access to tools. When you need tools, respond with JSON only, "
        "with no markdown and no surrounding explanation:\n"
        "{\"tool_uses\":[{\"name\":\"tool_name\",\"input\":{}}]}\n"
        "You may include multiple tool calls in tool_uses. Use exact tool names and "
        "JSON inputs matching the schemas. If you are done, answer normally in text.\n\n"
        f"Available tools:\n{json.dumps(catalog, ensure_ascii=False)}"
    )


def render_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            chunks.append(str(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            chunks.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            chunks.append(
                "[assistant requested tool]\n"
                + json.dumps(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    },
                    ensure_ascii=False,
                )
            )
        elif block_type == "tool_result":
            chunks.append(
                "[tool result]\n"
                + json.dumps(
                    {
                        "tool_use_id": block.get("tool_use_id"),
                        "is_error": bool(block.get("is_error", False)),
                        "content": block.get("content", ""),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            chunks.append(json.dumps(block, ensure_ascii=False))
    return "\n\n".join(part for part in chunks if part)


def extract_qwen_text(response: Any) -> str:
    try:
        return str(response.output.choices[0].message.content or "")
    except Exception:
        pass
    if isinstance(response, dict):
        try:
            return str(response["output"]["choices"][0]["message"]["content"] or "")
        except Exception:
            pass
    return str(response)


def ensure_success(response: Any) -> None:
    status_code = response_value(response, "status_code")
    if status_code in {None, "", 200, "200"}:
        return
    code = response_value(response, "code")
    message = response_value(response, "message")
    request_id = response_value(response, "request_id")
    raise RuntimeError(
        "DashScope call failed: "
        f"status_code={status_code}, code={code or '(none)'}, "
        f"message={message or '(none)'}, request_id={request_id or '(none)'}"
    )


def response_value(response: Any, name: str) -> Any:
    if isinstance(response, dict):
        return response.get(name)
    return getattr(response, name, None)


def parse_qwen_content(text: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_names = {str(tool.get("name", "")) for tool in tools}
    payload = parse_json_object(text)
    if payload:
        tool_uses = normalize_tool_uses(payload)
        blocks: list[dict[str, Any]] = []
        for index, item in enumerate(tool_uses):
            name = str(item.get("name", "")).strip()
            if not name or (tool_names and name not in tool_names):
                continue
            tool_input = item.get("input", {})
            if not isinstance(tool_input, dict):
                tool_input = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(item.get("id") or f"toolu_qwen_{int(time.time() * 1000)}_{index}"),
                    "name": name,
                    "input": tool_input,
                }
            )
        if blocks:
            return blocks
    return [{"type": "text", "text": text}]


def normalize_tool_uses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("tool_uses") or payload.get("tools") or payload.get("tool_calls")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if "name" in payload and "input" in payload:
        return [payload]
    if "tool" in payload:
        return [{"name": payload.get("tool"), "input": payload.get("input", {})}]
    return []


def parse_json_object(text: str) -> dict[str, Any]:
    candidates = json_candidates(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def json_candidates(text: str) -> list[str]:
    stripped = text.strip()
    candidates: list[str] = []
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            candidates.append("\n".join(lines[1:-1]).strip())

    balanced = first_balanced_json_object(stripped)
    if balanced:
        candidates.append(balanced)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end >= start:
        candidates.append(stripped[start : end + 1])

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return deduped


def first_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""
