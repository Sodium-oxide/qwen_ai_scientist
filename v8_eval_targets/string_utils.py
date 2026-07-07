"""Small string utilities used by the v8 eval sandbox.

All functions are pure and type-annotated. Non-``str`` inputs raise
``TypeError``. ``truncate`` additionally raises ``ValueError`` when
``max_length`` is negative.
"""

from __future__ import annotations


def _require_str(value: object, name: str) -> str:
    """Return ``value`` if it is a ``str``, else raise ``TypeError``."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")
    return value


def reverse(text: str) -> str:
    """Return ``text`` reversed character by character.

    >>> reverse("abc")
    'cba'
    """
    _require_str(text, "text")
    return text[::-1]


def is_palindrome(text: str) -> bool:
    """Return True if ``text`` is a palindrome.

    Comparison ignores case and any character that is not alphanumeric.

    >>> is_palindrome("A man, a plan, a canal: Panama")
    True
    """
    _require_str(text, "text")
    cleaned = [ch.lower() for ch in text if ch.isalnum()]
    return cleaned == cleaned[::-1]


def count_words(text: str) -> int:
    """Return the number of whitespace-separated words in ``text``.

    Any run of whitespace acts as a single separator. An empty or
    whitespace-only string has zero words.

    >>> count_words("  hello   world ")
    2
    """
    _require_str(text, "text")
    return len(text.split())


def capitalize_words(text: str) -> str:
    """Return ``text`` with the first letter of each word uppercased.

    Only the first character of each whitespace-separated token is
    changed; the rest of the token is lowercased so that already-shouted
    words are normalized.

    >>> capitalize_words("don't stop believing")
    "Don't Stop Believing"
    """
    _require_str(text, "text")
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split())


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Return ``text`` shortened to at most ``max_length`` characters.

    When truncation occurs, ``suffix`` is appended so the final length
    equals ``max_length``. If ``max_length`` is shorter than
    ``len(suffix)``, the suffix itself is truncated instead of raising.

    Raises:
        TypeError: if ``text`` or ``suffix`` is not ``str``, or
            ``max_length`` is not ``int``.
        ValueError: if ``max_length`` is negative.
    """
    _require_str(text, "text")
    _require_str(suffix, "suffix")
    if isinstance(max_length, bool) or not isinstance(max_length, int):
        raise TypeError(f"max_length must be int, got {type(max_length).__name__}")
    if max_length < 0:
        raise ValueError("max_length must be >= 0")

    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[: max_length - len(suffix)] + suffix
