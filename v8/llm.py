from __future__ import annotations

import os
from typing import Any

try:
    from .config import LLM_PROVIDER, QWEN_API_BASE, QWEN_API_KEY, QWEN_MODEL_ID
except ImportError:
    from config import LLM_PROVIDER, QWEN_API_BASE, QWEN_API_KEY, QWEN_MODEL_ID


_client: Any | None = None


def get_client() -> Any:
    global _client
    if _client is not None:
        return _client

    # Qwen / DashScope
    if LLM_PROVIDER in {"qwen", "dashscope"}:
        try:
            from .qwen_adapter import QwenClient
        except ImportError:
            from qwen_adapter import QwenClient
        _client = QwenClient(
            api_key=QWEN_API_KEY or "",
            model=QWEN_MODEL_ID or "qwen-plus",
            api_base=QWEN_API_BASE or "",
        )
        return _client

    # DeepSeek（默认）
    try:
        from .deepseek_adapter import create_deepseek_client
    except ImportError:
        from deepseek_adapter import create_deepseek_client
    _client = create_deepseek_client()
    return _client
