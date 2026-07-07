from __future__ import annotations

from typing import Any

try:
    from .config import ANTHROPIC_BASE_URL
except ImportError:
    from config import ANTHROPIC_BASE_URL


_client: Any | None = None


def get_client() -> Any:
    global _client
    if _client is None:
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
