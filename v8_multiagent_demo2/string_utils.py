"""String normalization helpers for the v8 multiagent demo2 task."""

from __future__ import annotations


def normalize_words(text: str) -> str:
    """Normalize whitespace in ``text``.

    The returned string:

    * has any run of whitespace (spaces, tabs, newlines, ...) collapsed
      into a single ASCII space,
    * has no leading or trailing whitespace,
    * is lowercased.

    An empty or whitespace-only input yields an empty string.

    Args:
        text: The input string to normalize. Must be a ``str``.

    Returns:
        The normalized string.

    Raises:
        TypeError: If ``text`` is not a ``str``.
    """
    if not isinstance(text, str):
        raise TypeError(f"normalize_words expects str, got {type(text).__name__}")
    # ``str.split()`` with no arguments already handles every run of
    # whitespace, including tabs and newlines, and strips leading/trailing
    # whitespace automatically.
    return " ".join(text.split()).lower()
