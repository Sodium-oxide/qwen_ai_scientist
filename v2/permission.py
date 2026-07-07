from __future__ import annotations

from typing import Any

try:
    from .hook import permission_hook
except ImportError:
    from hook import permission_hook


def check_permission(block: Any) -> None:
    """Legacy v1-compatible entrypoint.

    v2 routes permission checks through the PreToolUse hook. This wrapper exists
    for scripts that still import check_permission directly.
    """
    blocked = permission_hook(block)
    if blocked is not None:
        raise PermissionError(blocked)
