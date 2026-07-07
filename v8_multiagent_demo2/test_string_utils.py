"""Pytest suite for :func:`v8_multiagent_demo2.string_utils.normalize_words`."""

from __future__ import annotations

import pytest

from v8_multiagent_demo2.string_utils import normalize_words


class TestCaseFolding:
    def test_uppercase_becomes_lowercase(self) -> None:
        assert normalize_words("HELLO") == "hello"

    def test_mixed_case_becomes_lowercase(self) -> None:
        assert normalize_words("HeLLo WoRLD") == "hello world"

    def test_already_lowercase_untouched(self) -> None:
        assert normalize_words("hello world") == "hello world"


class TestWhitespaceCollapsing:
    def test_multiple_spaces_between_words(self) -> None:
        assert normalize_words("foo    bar") == "foo bar"

    def test_leading_and_trailing_spaces_stripped(self) -> None:
        assert normalize_words("   hello   world   ") == "hello world"

    def test_mixed_runs_of_spaces(self) -> None:
        assert normalize_words("a  b   c    d") == "a b c d"


class TestTabsAndNewlines:
    def test_tabs_treated_as_whitespace(self) -> None:
        assert normalize_words("foo\tbar") == "foo bar"

    def test_newlines_treated_as_whitespace(self) -> None:
        assert normalize_words("foo\nbar") == "foo bar"

    def test_mixed_tab_newline_space_runs(self) -> None:
        assert normalize_words("  Foo\t\tBAR\n\n baz \tQUX ") == "foo bar baz qux"

    def test_carriage_return_and_formfeed(self) -> None:
        assert normalize_words("a\r\nb\fc") == "a b c"


class TestEmptyAndWhitespaceOnly:
    def test_empty_string(self) -> None:
        assert normalize_words("") == ""

    def test_only_spaces(self) -> None:
        assert normalize_words("     ") == ""

    def test_only_tabs_and_newlines(self) -> None:
        assert normalize_words("\t\n \r\n\t") == ""


class TestTypeGuard:
    @pytest.mark.parametrize("bad", [None, 42, ["hello"], b"bytes"])
    def test_non_string_raises_type_error(self, bad: object) -> None:
        with pytest.raises(TypeError):
            normalize_words(bad)  # type: ignore[arg-type]
