"""String utility functions.

Pure helpers for common string operations:
- reverse(text): reverse a string
- is_palindrome(text): check if text is a palindrome, ignoring case and
  non-alphanumeric characters
- count_words(text): count whitespace-separated tokens

Every function raises TypeError when given a non-str argument.
"""

from __future__ import annotations


def _require_str(value: object, name: str) -> None:
    """Raise TypeError if value is not exactly a str."""
    if not isinstance(value, str):
        raise TypeError(
            f"{name} must be str, got {type(value).__name__}"
        )


def reverse(text: str) -> str:
    """Return the reverse of ``text``.

    Raises:
        TypeError: if ``text`` is not a str.
    """
    _require_str(text, "text")
    return text[::-1]


def is_palindrome(text: str) -> bool:
    """Return True if ``text`` reads the same forwards and backwards.

    Comparison ignores case and any non-alphanumeric characters.
    An empty (or purely non-alphanumeric) string is considered a palindrome.

    Raises:
        TypeError: if ``text`` is not a str.
    """
    _require_str(text, "text")
    normalized = [ch.lower() for ch in text if ch.isalnum()]
    return normalized == normalized[::-1]


def count_words(text: str) -> int:
    """Return the number of whitespace-separated words in ``text``.

    Uses ``str.split()`` with no arguments, so any run of whitespace acts
    as a single separator and leading/trailing whitespace is ignored.

    Raises:
        TypeError: if ``text`` is not a str.
    """
    _require_str(text, "text")
    return len(text.split())
