"""
DeepSeek API 适配器
==================
把 DeepSeek 的 OpenAI 兼容接口包装成与 QwenClient 相同的 .messages.create() 接口，
让 reviewer.py / science_core.py 不用改调用方式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class DeepSeekResponse:
    content: list[dict[str, Any]]  # 与 QwenResponse.content 格式一致


class DeepSeekMessages:
    """提供与 QwenMessages 相同签名的 .create() 方法。"""

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    def create(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        system: str = "",
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> DeepSeekResponse:
        # 1. 把 Anthropic/Qwen 风格的 messages 转成 OpenAI 格式
        openai_messages: list[dict[str, str]] = []
        if system.strip():
            openai_messages.append({"role": "system", "content": system.strip()})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # 展平 tool_use / tool_result 块为纯文本
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(str(block.get("text", "")))
                        elif block.get("type") == "tool_use":
                            parts.append(f"[tool_use: {block.get('name', '')}]")
                        elif block.get("type") == "tool_result":
                            parts.append(f"[tool_result: {str(block.get('content', ''))[:500]}]")
                        else:
                            parts.append(str(block))
                    else:
                        parts.append(str(block))
                content = "\n".join(parts)
            openai_messages.append({"role": role, "content": str(content)})

        # 2. 调用 DeepSeek（OpenAI 兼容 API）
        effective_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": openai_messages,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = self._client.chat.completions.create(**kwargs)

        # 3. 提取文本并包装成统一格式
        text = response.choices[0].message.content or ""
        return DeepSeekResponse(content=[{"type": "text", "text": text}])


class DeepSeekClient:
    """与 QwenClient 接口一致：client.messages.create(...)"""

    def __init__(self, api_key: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com/v1") -> None:
        if not api_key:
            raise RuntimeError("DeepSeek API key is not set. Set DEEPSEEK_API_KEY.")
        from openai import OpenAI
        self._openai = OpenAI(api_key=api_key, base_url=base_url)
        self.messages = DeepSeekMessages(self._openai, model)


def create_deepseek_client() -> DeepSeekClient:
    """从环境变量创建 DeepSeek 客户端。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or ""
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    return DeepSeekClient(api_key=api_key, model=model, base_url=base_url)
