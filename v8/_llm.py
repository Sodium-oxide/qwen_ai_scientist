from __future__ import annotations

from datetime import date, timedelta
from typing import Any
import ast
import json
import re
import time

try:
    from .config import (
        QWEN_API_BASE,
        QWEN_API_KEY,
        QWEN_MODEL_ID,
        SCIENCE_LLM_EXTRACTOR,
    )
    from .log import log_event
except ImportError:
    from config import (
        QWEN_API_BASE,
        QWEN_API_KEY,
        QWEN_MODEL_ID,
        SCIENCE_LLM_EXTRACTOR,
    )
    from log import log_event



def parse_jsonish_dict(value: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"text": value}
    return {}

def call_llm_json(
    system: str,
    prompt: str,
    max_tokens: int = 2000,
    fallback_list_key: str = "",
) -> dict[str, Any]:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    client = get_science_llm_client()
    response = client.messages.create(
        model=None,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        tools=[],
    )
    content = getattr(response, "content", response)
    rendered = render_llm_response_text(content)
    parsed = parse_json_object_from_text(rendered, fallback_list_key=fallback_list_key)
    if not parsed:
        log_event(
            "WARN",
            "llm_json_parse_failed",
            chars=len(rendered),
            snippet=trim_text(rendered, 500),
        )
        raise ValueError("LLM did not return a JSON object")
    return parsed

def get_science_llm_client() -> Any:
    extractor = SCIENCE_LLM_EXTRACTOR.strip().lower()
    if extractor in {"qwen", "dashscope"}:
        if not QWEN_API_KEY:
            raise RuntimeError("Science LLM extractor is qwen, but QWEN_API_KEY/DASHSCOPE_API_KEY is not set.")
        try:
            from .qwen_adapter import QwenClient
        except ImportError:
            from qwen_adapter import QwenClient
        return QwenClient(api_key=QWEN_API_KEY, model=QWEN_MODEL_ID, api_base=QWEN_API_BASE or "")
    if extractor in {"off", "none", "disabled"}:
        raise RuntimeError("Science LLM extractor is disabled.")
    try:
        from .llm import get_client
    except ImportError:
        from llm import get_client
    return get_client()

def render_llm_response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or item.get("content") or ""))
            else:
                chunks.append(str(item))
        return "\n".join(chunk for chunk in chunks if chunk)
    return str(content)

def parse_json_object_from_text(text: str, fallback_list_key: str = "") -> dict[str, Any]:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            stripped = "\n".join(lines[1:-1]).strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
    candidates = [stripped]
    candidates.extend(fenced_json_blocks(stripped))
    candidates.append(first_balanced_object(stripped))
    candidates.append(first_balanced_array(stripped))
    if fallback_list_key:
        candidates.append(extract_keyed_partial_array_object(stripped, fallback_list_key))
    candidates.extend(json_repair_candidates(candidate) for candidate in list(candidates) if candidate)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, dict):
            return parsed
        if fallback_list_key and isinstance(parsed, list):
            return {fallback_list_key: parsed}
    return {}

def fenced_json_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", str(text or ""), flags=re.IGNORECASE | re.DOTALL):
        block = match.group(1).strip()
        if block:
            blocks.append(block)
    return blocks

def json_repair_candidates(text: str) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return ""
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    return candidate

def extract_keyed_partial_array_object(text: str, key: str) -> str:
    array_text = extract_keyed_partial_array(text, key)
    if not array_text:
        return ""
    return f'{{"{key}": {array_text}}}'

def extract_keyed_partial_array(text: str, key: str) -> str:
    source = str(text or "")
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', source)
    if not match:
        return ""
    start = source.find("[", match.start())
    if start < 0:
        return ""
    complete_items = extract_complete_json_objects_from_array(source[start + 1 :])
    if not complete_items:
        return ""
    return "[" + ",".join(complete_items) + "]"

def extract_complete_json_objects_from_array(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(str(text or "")):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(json_repair_candidates(candidate))
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(parsed, dict):
                    items.append(json.dumps(parsed, ensure_ascii=False))
                start = -1
            continue
        if char == "]" and depth == 0:
            break
    return items

def first_balanced_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
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

def first_balanced_array(text: str) -> str:
    start = text.find("[")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""

def normalize_llm_paper_structure(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._gap_detection import normalize_gap_signals
        from ._literature_import import normalize_doi
        from ._utils import scalar, string_list
    except ImportError:
        from _gap_detection import normalize_gap_signals
        from _literature_import import normalize_doi
        from _utils import scalar, string_list
    return {
        "title": scalar(payload.get("title")),
        "citation": scalar(payload.get("citation")),
        "authors": string_list(payload.get("authors")),
        "year": scalar(payload.get("year")),
        "venue": scalar(payload.get("venue")),
        "doi": normalize_doi(scalar(payload.get("doi"))),
        "arxiv_id": scalar(payload.get("arxiv_id") or payload.get("arxiv")),
        "abstract": scalar(payload.get("abstract")),
        "conclusion": scalar(payload.get("conclusion")),
        "strengths": string_list(payload.get("strengths")),
        "improvements": string_list(payload.get("improvements") or payload.get("limitations")),
        "method": scalar(payload.get("method")),
        "scenario": scalar(payload.get("scenario")),
        "benchmark": scalar(payload.get("benchmark")),
        "contribution": scalar(payload.get("contribution")),
        "limitation": scalar(payload.get("limitation")),
        "gap_signals": normalize_gap_signals(
            [
                item if isinstance(item, dict) else {"signal_type": "gap_signal", "text": scalar(item)}
                for item in (payload.get("gap_signals") if isinstance(payload.get("gap_signals"), list) else [])
            ]
            + [
                {"signal_type": "limitation", "text": item, "evidence_type": "author_opinion"}
                for item in string_list(payload.get("limitations"))
            ]
        ),
    }

