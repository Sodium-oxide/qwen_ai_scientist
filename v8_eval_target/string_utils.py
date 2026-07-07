"""String utility functions for v8 evaluation target.

Provides a small set of well-defined string helpers suitable for unit testing.
All functions are pure (no side effects) and validate input types.
"""

from __future__ import annotations

import re
from typing import Iterable


def reverse(text: str) -> str:
    """Return the reverse of ``text``.

    >>> reverse("abc")
    'cba'
    >>> reverse("")
    ''
    """
    if not isinstance(text, str):
        raise TypeError("reverse() expects a str")
    return text[::-1]


def is_palindrome(text: str) -> bool:
    """Return True if ``text`` is a palindrome.

    Comparison ignores case and non-alphanumeric characters.

    >>> is_palindrome("A man, a plan, a canal: Panama")
    True
    >>> is_palindrome("hello")
    False
    >>> is_palindrome("")
    True
    """
    if not isinstance(text, str):
        raise TypeError("is_palindrome() expects a str")
    cleaned = re.sub(r"[^0-9A-Za-z]", "", text).lower()
    return cleaned == cleaned[::-1]


def count_words(text: str) -> int:
    """Return the number of whitespace-separated words in ``text``.

    >>> count_words("hello world")
    2
    >>> count_words("   many   spaces   here  ")
    3
    >>> count_words("")
    0
    """
    if not isinstance(text, str):
        raise TypeError("count_words() expects a str")
    return len(text.split())


def capitalize_words(text: str) -> str:
    """Return ``text`` with the first letter of each whitespace-separated word capitalized.

    Unlike ``str.title``, this preserves apostrophes correctly (``don't`` stays ``Don't``).
    Internal whitespace runs are collapsed to a single space.

    >>> capitalize_words("hello world")
    'Hello World'
    >>> capitalize_words("don't stop believing")
    "Don't Stop Believing"
    >>> capitalize_words("")
    ''
    """
    if not isinstance(text, str):
        raise TypeError("capitalize_words() expects a str")
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split())


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate ``text`` to ``max_length`` characters, appending ``suffix`` when cut.

    If ``len(text) <= max_length`` the text is returned unchanged. Otherwise the
    result has total length exactly ``max_length`` (suffix included). If
    ``max_length`` is shorter than ``suffix``, the suffix itself is truncated.

    >>> truncate("hello world", 8)
    'hello...'
    >>> truncate("hi", 5)
    'hi'
    >>> truncate("hello", 5)
    'hello'
    >>> truncate("hello world", 4, suffix="..")
    'he..'
    """
    if not isinstance(text, str):
        raise TypeError("truncate() text must be a str")
    if not isinstance(suffix, str):
        raise TypeError("truncate() suffix must be a str")
    if not isinstance(max_length, int) or isinstance(max_length, bool):
        raise TypeError("truncate() max_length must be an int")
    if max_length < 0:
        raise ValueError("truncate() max_length must be >= 0")
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[: max_length - len(suffix)] + suffix


__all__ = [
    "reverse",
    "is_palindrome",
    "count_words",
    "capitalize_words",
    "truncate",
]
