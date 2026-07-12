from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

# TokenPlan 的默认 base URL（OpenAI 兼容协议，直连阿里云 MaaS）
TOKENPLAN_DEFAULT_BASE = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def _is_tokenplan_key(api_key: str) -> bool:
    """TokenPlan 的 key 以 sk-sp-D 开头，走 OpenAI 兼容协议。"""
    return bool(api_key and api_key.startswith("sk-sp-D"))


DASHSCOPE_INPUT_LIMIT = 30_720
DASHSCOPE_SAFE_INPUT_UNITS = 25_000
DASHSCOPE_FULL_TOOL_CONTEXT_UNITS = 6_000

RESEARCH_CONTEXT_MARKERS = (
    "create_research_project",
    "boxue",
    "zhizhi",
    "tanxi",
    "socrates",
    "mingli",
    "yanzhen",
    "duzhi",
    "bianlun",
    "research brief",
    "scientific hypothesis",
    "文献检索",
    "科研闭环",
    "研究目标",
)

RESEARCH_WORKFLOW_TOOL_NAMES = (
    "create_research_project",
    "decompose_research_objective",
    "set_research_brief",
    "list_research_projects",
    "get_research_project",
    "list_literature_providers",
    "run_boxue_research_round",
    "run_zhizhi_subhypothesis_analysis",
    "run_tanxi_gap_exploration",
    "run_socrates_mechanism_enrichment",
    "run_mingli_hypothesis_evolution",
    "run_yanzhen_mechanism_verification",
    "run_socratic_hypothesis_debate",
    "build_knowledge_map",
    "list_papergraph_records",
    "export_research_plan",
)

GENERAL_BOOTSTRAP_TOOL_NAMES = (
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "todo_write",
    "load_skill",
    "compact",
    "create_task",
    "list_tasks",
    "get_task",
)


@dataclass
class QwenResponse:
    content: list[dict[str, Any]]
    stop_reason: str | None = None
    requires_tool_json_retry: bool = False


class QwenMessages:
    def __init__(self, api_key: str, default_model: str, api_base: str = "") -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.api_base = api_base
        self._use_tokenplan = _is_tokenplan_key(api_key)

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
        effective_model = self.effective_model(model)

        if self._use_tokenplan:
            return self._create_via_tokenplan(
                model=effective_model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools or [],
            )

        return self._create_via_dashscope(
            model=effective_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools or [],
        )

    def _create_via_dashscope(
        self,
        model: str,
        max_tokens: int | None,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> QwenResponse:
        try:
            import dashscope
            from dashscope import Generation
        except ImportError as exc:
            raise RuntimeError("The dashscope package is not installed. Run: pip install dashscope") from exc

        if self.api_base:
            dashscope.base_http_api_url = self.api_base
        qwen_messages = to_qwen_messages(system, messages, tools)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": qwen_messages,
            "result_format": "message",
            "api_key": self.api_key,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = Generation.call(**kwargs)
        ensure_success(response)
        text = extract_qwen_text(response)
        if is_incomplete_tool_json(text):
            return QwenResponse(content=[], requires_tool_json_retry=True)
        return QwenResponse(content=parse_qwen_content(text, tools))

    def _create_via_tokenplan(
        self,
        model: str,
        max_tokens: int | None,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> QwenResponse:
        """TokenPlan 走 OpenAI 兼容协议。"""
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "TokenPlan requires the openai package. Run: pip install openai"
            ) from exc

        base_url = self.api_base or TOKENPLAN_DEFAULT_BASE
        clean_key = self.api_key.strip()
        client = OpenAI(api_key=clean_key, base_url=base_url)

        openai_msgs: list[dict[str, Any]] = []
        if system.strip():
            openai_msgs.append({"role": "system", "content": system.strip()})
        for msg in messages:
            openai_msgs.append({
                "role": msg.get("role", "user"),
                "content": _flatten_content(msg.get("content", "")),
            })

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_msgs,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or ""

        if tools:
            return QwenResponse(content=parse_qwen_content(text, tools))
        return QwenResponse(content=[{"type": "text", "text": text}])

    def effective_model(self, requested: str | None) -> str:
        value = str(requested or "").strip()
        if not value:
            return self.default_model
        if value.startswith("claude") or value.startswith("anthropic."):
            return self.default_model
        return value


def _flatten_content(content: Any) -> str:
    """展平内部消息格式为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool_use: {block.get('name', '')}]")
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", ""))[:500])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


class QwenClient:
    def __init__(self, api_key: str, model: str = "qwen-plus", api_base: str = "") -> None:
        if not api_key:
            raise RuntimeError("Qwen API key is not set. Set QWEN_API_KEY or DASHSCOPE_API_KEY.")
        self.messages = QwenMessages(api_key=api_key, default_model=model, api_base=api_base)


def to_qwen_messages(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    compact_tool_catalog: bool = False,
) -> list[dict[str, str]]:
    rendered: list[dict[str, str]] = []
    full_system = system
    if tools:
        full_system += "\n\n" + tool_protocol_prompt(tools, compact=compact_tool_catalog)
    if full_system.strip():
        rendered.append({"role": "system", "content": full_system.strip()})

    for message in messages:
        role = str(message.get("role", "user"))
        if role not in {"system", "user", "assistant"}:
            role = "user"
        rendered.append({"role": role, "content": render_content(message.get("content", ""))})
    return rendered


def tool_protocol_prompt(tools: list[dict[str, Any]], *, compact: bool = False) -> str:
    if compact:
        catalog = compact_tool_catalog(tools)
        catalog_label = "Available tools (schema summaries)"
    else:
        catalog = [transport_tool_definition(tool) for tool in tools]
        catalog_label = "Available tools"
    return (
        "You have access to tools. When you need tools, respond with JSON only, "
        "with no markdown and no surrounding explanation:\n"
        "{\"tool_uses\":[{\"name\":\"tool_name\",\"input\":{}}]}\n"
        "You may include multiple tool calls in tool_uses. Use exact tool names and "
        "JSON inputs matching the schemas. If you call create_research_project, never include "
        "research_brief: the runtime injects the complete original user prompt automatically. "
        "If you are done, answer normally in text.\n\n"
        f"{catalog_label}:\n{json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))}"
    )


def transport_tool_description(tool: dict[str, Any]) -> str:
    description = str(tool.get("description", ""))
    if str(tool.get("name", "")) == "create_research_project":
        return description + " The runtime preserves the complete original task automatically; omit research_brief."
    return description


def transport_tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    sanitized_schema = dict(schema)
    if isinstance(properties, dict):
        sanitized_schema["properties"] = {
            str(name): spec
            for name, spec in properties.items()
            if not (str(tool.get("name", "")) == "create_research_project" and str(name) == "research_brief")
        }
    return {
        "name": tool.get("name"),
        "description": transport_tool_description(tool),
        "input_schema": sanitized_schema,
    }


def compact_tool_catalog(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for tool in tools:
        schema = tool.get("input_schema") or {}
        properties = schema.get("properties") if isinstance(schema, dict) else {}
        summarized_properties: dict[str, str] = {}
        if isinstance(properties, dict):
            for name, spec in properties.items():
                if str(tool.get("name", "")) == "create_research_project" and str(name) == "research_brief":
                    continue
                if isinstance(spec, dict):
                    summarized_properties[str(name)] = str(spec.get("type") or "value")
                else:
                    summarized_properties[str(name)] = "value"
        entry: dict[str, Any] = {
            "name": tool.get("name"),
            "description": transport_tool_description(tool),
            "parameters": summarized_properties,
        }
        required = schema.get("required") if isinstance(schema, dict) else []
        if isinstance(required, list) and required:
            entry["required"] = required
        catalog.append(entry)
    return catalog


def prepare_qwen_request(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    full_messages = to_qwen_messages(system, messages, tools)
    full_units = estimate_qwen_messages_units(full_messages)
    conversation_units = estimate_qwen_messages_units(
        [message for message in full_messages if message.get("role") != "system"]
    )
    if (
        full_units <= DASHSCOPE_SAFE_INPUT_UNITS
        and conversation_units <= DASHSCOPE_FULL_TOOL_CONTEXT_UNITS
    ):
        return full_messages, tools, {
            "estimated_input_units": full_units,
            "tool_mode": "full",
            "messages_compacted_for_transport": False,
        }

    selected_tools = select_context_tools(messages, tools)
    compact_messages = to_qwen_messages(
        system,
        messages,
        selected_tools,
        compact_tool_catalog=True,
    )
    compact_units = estimate_qwen_messages_units(compact_messages)
    if compact_units <= DASHSCOPE_SAFE_INPUT_UNITS:
        return compact_messages, selected_tools, {
            "estimated_input_units": compact_units,
            "tool_mode": "contextual_compact",
            "messages_compacted_for_transport": False,
        }

    fitted_messages = fit_messages_to_budget(
        compact_messages,
        DASHSCOPE_SAFE_INPUT_UNITS,
    )
    return fitted_messages, selected_tools, {
        "estimated_input_units": estimate_qwen_messages_units(fitted_messages),
        "tool_mode": "contextual_compact",
        "messages_compacted_for_transport": True,
    }


def select_context_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    context = "\n".join(render_content(message.get("content", "")) for message in messages).lower()
    preferred_names = RESEARCH_WORKFLOW_TOOL_NAMES if any(
        marker in context for marker in RESEARCH_CONTEXT_MARKERS
    ) else GENERAL_BOOTSTRAP_TOOL_NAMES
    by_name = {str(tool.get("name", "")): tool for tool in tools}
    selected = [by_name[name] for name in preferred_names if name in by_name]
    return selected or tools[: min(12, len(tools))]


def estimate_qwen_text_units(text: str) -> int:
    cjk_or_fullwidth = sum(1 for char in text if ord(char) >= 0x2E80)
    other = len(text) - cjk_or_fullwidth
    return cjk_or_fullwidth + (other + 3) // 4


def estimate_qwen_messages_units(messages: list[dict[str, str]]) -> int:
    return sum(estimate_qwen_text_units(str(message.get("content", ""))) + 8 for message in messages)


def fit_messages_to_budget(
    messages: list[dict[str, str]],
    budget_units: int,
) -> list[dict[str, str]]:
    if estimate_qwen_messages_units(messages) <= budget_units:
        return messages

    system_messages = [message for message in messages if message.get("role") == "system"]
    conversation_messages = [message for message in messages if message.get("role") != "system"]
    system_units = estimate_qwen_messages_units(system_messages)
    available_units = max(1_200, budget_units - system_units - 8 * len(conversation_messages))
    retained: list[dict[str, str]] = []
    total_messages = max(1, len(conversation_messages))

    for index, message in enumerate(conversation_messages):
        weight = 2 if index in {0, total_messages - 1} else 1
        allocation = max(320, available_units * weight // (total_messages + 2))
        content = str(message.get("content", ""))
        retained.append({"role": str(message.get("role", "user")), "content": clip_text_to_units(content, allocation)})

    fitted = system_messages + retained
    overflow = estimate_qwen_messages_units(fitted) - budget_units
    if overflow <= 0:
        return fitted

    for index in range(len(retained) - 1, -1, -1):
        content = retained[index]["content"]
        current_units = estimate_qwen_text_units(content)
        target_units = max(160, current_units - overflow - 16)
        retained[index]["content"] = clip_text_to_units(content, target_units)
        fitted = system_messages + retained
        overflow = estimate_qwen_messages_units(fitted) - budget_units
        if overflow <= 0:
            break

    if overflow > 0 and system_messages:
        content = system_messages[-1]["content"]
        target_units = max(400, estimate_qwen_text_units(content) - overflow - 16)
        system_messages[-1] = {
            "role": "system",
            "content": clip_text_to_units(content, target_units),
        }
        fitted = system_messages + retained
    return fitted


def clip_text_to_units(text: str, budget_units: int) -> str:
    if estimate_qwen_text_units(text) <= budget_units:
        return text

    marker = "\n...[transport preview; original message retained by runtime]...\n"
    marker_units = estimate_qwen_text_units(marker)
    usable_units = max(1, budget_units - marker_units)
    prefix = take_text_units(text, usable_units * 2 // 3)
    suffix = take_text_units(text[::-1], usable_units - estimate_qwen_text_units(prefix))[::-1]
    return prefix + marker + suffix


def take_text_units(text: str, budget_units: int) -> str:
    consumed = 0
    end = 0
    for end, char in enumerate(text, start=1):
        char_units = 1 if ord(char) >= 0x2E80 else 1 / 4
        if consumed + char_units > budget_units:
            return text[: end - 1]
        consumed += char_units
    return text


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


def is_incomplete_tool_json(text: str) -> bool:
    source = str(text or "").strip()
    if not source or '"tool_uses"' not in source:
        return False
    return not bool(parse_json_object(source))


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
