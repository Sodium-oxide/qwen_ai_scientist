"""Pytest tests for string_utils.py.

Covers three helpers:
- reverse
- is_palindrome
- count_words

Each function is exercised with normal, boundary, and TypeError cases.
"""

from __future__ import annotations

import pytest

from string_utils import count_words, is_palindrome, reverse


# ---------------------------------------------------------------------------
# reverse
# ---------------------------------------------------------------------------


class TestReverse:
    """Tests for reverse(text)."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("hello", "olleh"),
            ("Python", "nohtyP"),
            ("ab cd", "dc ba"),
            ("a", "a"),
            ("racecar", "racecar"),
            ("12345", "54321"),
            ("你好世界", "界世好你"),
        ],
    )
    def test_reverse_normal(self, text: str, expected: str) -> None:
        assert reverse(text) == expected

    def test_reverse_empty_string(self) -> None:
        # Boundary: empty string reverses to empty string.
        assert reverse("") == ""

    def test_reverse_whitespace_only(self) -> None:
        # Boundary: preserves whitespace as-is.
        assert reverse("   ") == "   "

    def test_reverse_is_involution(self) -> None:
        # Reversing twice returns the original value.
        text = "The quick brown fox"
        assert reverse(reverse(text)) == text

    @pytest.mark.parametrize(
        "bad",
        [None, 123, 4.5, ["a", "b"], ("x",), {"k": "v"}, b"bytes", True],
    )
    def test_reverse_type_error(self, bad: object) -> None:
        with pytest.raises(TypeError):
            reverse(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# is_palindrome
# ---------------------------------------------------------------------------


class TestIsPalindrome:
    """Tests for is_palindrome(text)."""

    @pytest.mark.parametrize(
        "text",
        [
            "racecar",
            "level",
            "Madam",
            "A man, a plan, a canal: Panama",
            "No 'x' in Nixon",
            "Was it a car or a cat I saw?",
            "12321",
            "a",
        ],
    )
    def test_is_palindrome_true(self, text: str) -> None:
        assert is_palindrome(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "python",
            "palindrome",
            "12345",
            "ab",
            "Madame",
        ],
    )
    def test_is_palindrome_false(self, text: str) -> None:
        assert is_palindrome(text) is False

    def test_is_palindrome_empty_string(self) -> None:
        # Boundary: empty string is trivially a palindrome.
        assert is_palindrome("") is True

    def test_is_palindrome_only_punctuation(self) -> None:
        # Boundary: after stripping non-alphanumerics, nothing remains,
        # so it is considered a palindrome.
        assert is_palindrome("!!! ,,, ???") is True

    def test_is_palindrome_case_insensitive(self) -> None:
        assert is_palindrome("RaceCar") is True

    def test_is_palindrome_ignores_non_alnum(self) -> None:
        # "race a car" -> "raceacar" is not a palindrome
        assert is_palindrome("race a car") is False
        # "race,car" -> "racecar" is a palindrome
        assert is_palindrome("race,car") is True
        assert is_palindrome("r@a#c$e%c^a&r") is True

    @pytest.mark.parametrize(
        "bad",
        [None, 0, 3.14, ["r", "a", "c", "e"], ("racecar",), {"a": 1}, b"racecar"],
    )
    def test_is_palindrome_type_error(self, bad: object) -> None:
        with pytest.raises(TypeError):
            is_palindrome(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# count_words
# ---------------------------------------------------------------------------


class TestCountWords:
    """Tests for count_words(text)."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("hello world", 2),
            ("one", 1),
            ("a b c d e", 5),
            ("The quick brown fox jumps over the lazy dog", 9),
            ("hello, world!", 2),
        ],
    )
    def test_count_words_normal(self, text: str, expected: int) -> None:
        assert count_words(text) == expected

    def test_count_words_empty_string(self) -> None:
        # Boundary: empty string has zero words.
        assert count_words("") == 0

    def test_count_words_whitespace_only(self) -> None:
        # Boundary: any run of whitespace still yields zero words.
        assert count_words("   ") == 0
        assert count_words("\t\n \r") == 0

    def test_count_words_multiple_spaces_collapsed(self) -> None:
        # Runs of whitespace act as a single separator.
        assert count_words("hello    world") == 2

    def test_count_words_mixed_whitespace(self) -> None:
        assert count_words("a\tb\nc\rd e") == 5

    def test_count_words_leading_and_trailing_whitespace(self) -> None:
        assert count_words("   hello world   ") == 2

    def test_count_words_single_char(self) -> None:
        assert count_words("x") == 1

    @pytest.mark.parametrize(
        "bad",
        [None, 42, 2.5, ["a", "b"], ("a",), {"a": 1}, b"a b", False],
    )
    def test_count_words_type_error(self, bad: object) -> None:
        with pytest.raises(TypeError):
            count_words(bad)  # type: ignore[arg-type]
