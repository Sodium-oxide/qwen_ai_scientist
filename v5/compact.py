from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from .config import (
        EMERGENCY_KEEP_MESSAGES,
        L0_SERIALIZED_LIMIT,
        L0_SUMMARY_TOKENS,
        L1_COMPACT_TRIGGER_MESSAGES,
        L1_KEEP_HEAD,
        L1_KEEP_TAIL,
        L1_MAX_MESSAGES,
        L2_KEEP_TOOL_RESULTS,
        L3_SNIPPET_CHARS,
        L3_TOOL_RESULT_BUDGET,
        MODEL_ID,
        TOOL_RESULTS_DIR,
        TRANSCRIPTS_DIR,
    )
    from .llm import get_client
    from .log import log_event
except ImportError:
    from config import (
        EMERGENCY_KEEP_MESSAGES,
        L0_SERIALIZED_LIMIT,
        L0_SUMMARY_TOKENS,
        L1_COMPACT_TRIGGER_MESSAGES,
        L1_KEEP_HEAD,
        L1_KEEP_TAIL,
        L1_MAX_MESSAGES,
        L2_KEEP_TOOL_RESULTS,
        L3_SNIPPET_CHARS,
        L3_TOOL_RESULT_BUDGET,
        MODEL_ID,
        TOOL_RESULTS_DIR,
        TRANSCRIPTS_DIR,
    )
    from llm import get_client
    from log import log_event


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    focus: str = "",
    force_l0: bool = False,
) -> list[dict[str, Any]]:
    compacted = deepcopy(messages)
    compacted = tool_result_budget(compacted)
    compacted = snip_compact(compacted)
    compacted = micro_compact(compacted)
    compacted = normalize_messages(compacted)

    if force_l0 or serialized_len(compacted) > L0_SERIALIZED_LIMIT:
        try:
            compacted = compact_history(compacted, focus=focus)
        except Exception as exc:
            log_event("ERROR", "l0_failed", error=exc)
            compacted = local_fallback_compact(
                compacted,
                reason="manual compact failed" if force_l0 else "serialized history too large",
            )

    return normalize_messages(compacted)


def compact_in_place(
    messages: list[dict[str, Any]],
    *,
    focus: str = "",
    force_l0: bool = False,
) -> None:
    messages[:] = compact_messages(messages, focus=focus, force_l0=force_l0)


def emergency_compact(messages: list[dict[str, Any]], *, focus: str = "") -> list[dict[str, Any]]:
    log_event("COMPACT", "emergency_start", messages=len(messages), focus=focus)
    try:
        return compact_history(messages, focus=focus or "recover from prompt_too_long")
    except Exception as exc:
        log_event("ERROR", "emergency_llm_failed", error=exc)
        return local_fallback_compact(messages, reason="prompt_too_long emergency")


def tool_result_budget(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not is_tool_result(block):
                continue
            text = str(block.get("content", ""))
            if len(text) <= L3_TOOL_RESULT_BUDGET:
                continue
            if "[Full tool result saved at:" in text:
                continue
            path = save_tool_result(block, text)
            block["content"] = summarize_large_result(text, path)
            changed += 1
    if changed:
        log_event("COMPACT", "l3_tool_result_budget", changed=changed)
    return messages


def snip_compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= L1_COMPACT_TRIGGER_MESSAGES:
        return messages

    head_count = min(L1_KEEP_HEAD, max(1, L1_MAX_MESSAGES - 1))
    tail_count = min(L1_KEEP_TAIL, max(0, L1_MAX_MESSAGES - head_count - 1))
    head = messages[:head_count]
    tail = messages[-tail_count:] if tail_count else []
    omitted = len(messages) - len(head) - len(tail)
    log_event(
        "COMPACT",
        "l1_snip",
        before=len(messages),
        omitted=omitted,
        target=L1_MAX_MESSAGES,
        trigger=L1_COMPACT_TRIGGER_MESSAGES,
    )
    return [
        *head,
        {
            "role": "user",
            "content": f"[snip_compact omitted {omitted} middle messages]",
        },
        *tail,
    ]


def micro_compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = 0
    changed = 0
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not is_tool_result(block):
                continue
            seen += 1
            if seen <= L2_KEEP_TOOL_RESULTS:
                continue
            text = str(block.get("content", ""))
            if text.startswith("[tool result compressed"):
                continue
            block["content"] = "[tool result compressed by micro_compact]"
            changed += 1
    if changed:
        log_event("COMPACT", "l2_micro", changed=changed, kept=L2_KEEP_TOOL_RESULTS)
    return messages


def compact_history(messages: list[dict[str, Any]], *, focus: str = "") -> list[dict[str, Any]]:
    transcript_path = save_transcript(messages)
    summary = summarize_with_llm(messages, focus=focus)
    log_event(
        "COMPACT",
        "l0_summary",
        before=len(messages),
        transcript=transcript_path,
        chars=len(summary),
    )
    return [
        {
            "role": "user",
            "content": (
                "[Conversation compacted. Full transcript saved at: "
                f"{transcript_path}]\n\n{summary}"
            ),
        }
    ]


def local_fallback_compact(messages: list[dict[str, Any]], *, reason: str) -> list[dict[str, Any]]:
    transcript_path = save_transcript(messages)
    kept = deepcopy(messages[-EMERGENCY_KEEP_MESSAGES:])
    log_event(
        "COMPACT",
        "local_fallback",
        reason=reason,
        kept=len(kept),
        transcript=transcript_path,
    )
    return normalize_messages(
        [
            {
                "role": "user",
                "content": (
                    f"[Local fallback compaction: {reason}. Full transcript saved at: "
                    f"{transcript_path}. Earlier context was dropped.]"
                ),
            },
            *kept,
        ]
    )


def summarize_with_llm(messages: list[dict[str, Any]], *, focus: str = "") -> str:
    client = get_client()
    focus_text = f"\nFocus on: {focus}" if focus else ""
    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=L0_SUMMARY_TOKENS,
        system=(
            "Summarize an agent conversation for continuation. Include the original "
            "task, important decisions, files touched, tool results that still matter, "
            "current state, and remaining work."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "Compact this conversation without losing operational context."
                    f"{focus_text}\n\n{serialize_messages(messages)}"
                ),
            }
        ],
    )
    return response_text(response.content) or "[LLM summary was empty]"


def save_tool_result(block: dict[str, Any], text: str) -> Path:
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tool_use_id = str(block.get("tool_use_id") or f"tool_result_{time.time_ns()}")
    path = TOOL_RESULTS_DIR / f"{safe_filename(tool_use_id)}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def save_transcript(messages: list[dict[str, Any]]) -> Path:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPTS_DIR / f"transcript_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}.json"
    path.write_text(serialize_messages(messages), encoding="utf-8")
    return path


def summarize_large_result(text: str, path: Path) -> str:
    head = text[:L3_SNIPPET_CHARS]
    tail = text[-L3_SNIPPET_CHARS:] if len(text) > L3_SNIPPET_CHARS else ""
    return (
        f"[Full tool result saved at: {path}]\n"
        f"[Original length: {len(text)} characters]\n\n"
        f"--- head ---\n{head}\n"
        f"--- tail ---\n{tail}"
    )


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if (
            normalized
            and normalized[-1].get("role") == role
            and can_merge(normalized[-1].get("content", ""))
            and can_merge(content)
        ):
            normalized[-1]["content"] = merge_content(normalized[-1].get("content", ""), content)
        else:
            normalized.append({"role": role, "content": content})
    return normalized


def can_merge(content: Any) -> bool:
    if not isinstance(content, list):
        return True
    return not any(
        isinstance(block, dict)
        and block.get("type") in {"tool_use", "tool_result"}
        for block in content
    )


def merge_content(left: Any, right: Any) -> Any:
    if isinstance(left, list) or isinstance(right, list):
        return as_blocks(left) + as_blocks(right)
    return f"{left}\n\n{right}"


def as_blocks(content: Any) -> list[Any]:
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def serialized_len(messages: list[dict[str, Any]]) -> int:
    return len(serialize_messages(messages))


def serialize_messages(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, ensure_ascii=False, indent=2, default=str)


def safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in value)


def is_tool_result(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


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


def is_prompt_too_long_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "prompt_too_long",
            "context length",
            "context_length",
            "too many tokens",
            "maximum context",
        )
    )
