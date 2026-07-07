from __future__ import annotations

from typing import Any

try:
    from .config import ANTHROPIC_BASE_URL, LLM_PROVIDER, QWEN_API_BASE, QWEN_API_KEY, QWEN_MODEL_ID
except ImportError:
    from config import ANTHROPIC_BASE_URL, LLM_PROVIDER, QWEN_API_BASE, QWEN_API_KEY, QWEN_MODEL_ID


_client: Any | None = None


def get_client() -> Any:
    global _client
    if _client is None:
        if LLM_PROVIDER in {"qwen", "dashscope"}:
            try:
                from .qwen_adapter import QwenClient
            except ImportError:
                from qwen_adapter import QwenClient
            _client = QwenClient(api_key=QWEN_API_KEY or "", model=QWEN_MODEL_ID, api_base=QWEN_API_BASE or "")
            return _client

        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "The anthropic package is not installed. Run: pip install -r v4/requirements.txt"
            ) from exc

        kwargs: dict[str, str] = {}
        if ANTHROPIC_BASE_URL:
            kwargs["base_url"] = ANTHROPIC_BASE_URL
        _client = Anthropic(**kwargs)
    return _client
