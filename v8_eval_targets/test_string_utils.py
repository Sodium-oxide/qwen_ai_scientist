"""Pytest suite for string_utils.

Run from inside ``v8_eval_targets``::

    python -m pytest -v test_string_utils.py
"""

import pytest

from string_utils import (
    capitalize_words,
    count_words,
    is_palindrome,
    reverse,
    truncate,
)


# ---------- reverse ----------

def test_reverse_normal():
    assert reverse("abc") == "cba"


def test_reverse_empty():
    assert reverse("") == ""


def test_reverse_single_char():
    assert reverse("z") == "z"


def test_reverse_with_spaces():
    assert reverse("ab cd") == "dc ba"


def test_reverse_type_error():
    with pytest.raises(TypeError):
        reverse(123)  # type: ignore[arg-type]


# ---------- is_palindrome ----------

def test_is_palindrome_sentence_with_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama") is True


def test_is_palindrome_plain_false():
    assert is_palindrome("hello world") is False


def test_is_palindrome_empty_is_true():
    assert is_palindrome("") is True


def test_is_palindrome_type_error():
    with pytest.raises(TypeError):
        is_palindrome(None)  # type: ignore[arg-type]


# ---------- count_words ----------

def test_count_words_normal():
    assert count_words("hello world") == 2


def test_count_words_multiple_whitespace():
    assert count_words("  hello\t\nworld  ") == 2


def test_count_words_empty():
    assert count_words("") == 0


def test_count_words_whitespace_only():
    assert count_words("   \t\n ") == 0


def test_count_words_type_error():
    with pytest.raises(TypeError):
        count_words(["hi"])  # type: ignore[arg-type]


# ---------- capitalize_words ----------

def test_capitalize_words_sentence():
    assert capitalize_words("hello world") == "Hello World"


def test_capitalize_words_apostrophe():
    assert capitalize_words("don't stop") == "Don't Stop"


def test_capitalize_words_empty():
    assert capitalize_words("") == ""


def test_capitalize_words_whitespace_only():
    assert capitalize_words("   ") == ""


def test_capitalize_words_type_error():
    with pytest.raises(TypeError):
        capitalize_words(42)  # type: ignore[arg-type]


# ---------- truncate ----------

def test_truncate_no_change():
    assert truncate("hello", 10) == "hello"


def test_truncate_exact_length():
    assert truncate("hello", 5) == "hello"


def test_truncate_shortens_with_default_suffix():
    assert truncate("hello world", 8) == "hello..."


def test_truncate_custom_suffix():
    assert truncate("hello world", 7, suffix="~") == "hello ~"


def test_truncate_max_length_equals_suffix():
    assert truncate("abcdef", 3) == "..."


def test_truncate_max_length_smaller_than_suffix():
    assert truncate("abcdef", 2) == ".."


def test_truncate_max_length_zero():
    assert truncate("abc", 0) == ""


def test_truncate_negative_max_length():
    with pytest.raises(ValueError):
        truncate("abc", -1)


def test_truncate_type_error_on_text():
    with pytest.raises(TypeError):
        truncate(123, 5)  # type: ignore[arg-type]


def test_truncate_type_error_on_max_length():
    with pytest.raises(TypeError):
        truncate("abc", "5")  # type: ignore[arg-type]
