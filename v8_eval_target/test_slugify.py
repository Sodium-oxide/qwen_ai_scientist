"""Tests for slugify(text).

Requirements:
- Lowercase ASCII letters.
- Spaces (and other whitespace) become single '-'.
- Runs of punctuation collapse into a single '-'.
- Chinese (CJK) characters are preserved as-is.
- Leading/trailing separators are stripped.
- Empty / whitespace-only / punctuation-only input returns ''.
"""

import pytest
from slugify import slugify


class TestBasicCase:
    def test_lowercases_ascii(self):
        assert slugify("Hello") == "hello"

    def test_mixed_case_words(self):
        assert slugify("Hello World") == "hello-world"

    def test_all_uppercase(self):
        assert slugify("PYTHON ROCKS") == "python-rocks"


class TestWhitespace:
    def test_single_space(self):
        assert slugify("foo bar") == "foo-bar"

    def test_multiple_spaces_collapse(self):
        assert slugify("foo    bar") == "foo-bar"

    def test_tabs_and_newlines(self):
        assert slugify("foo\t\nbar") == "foo-bar"

    def test_leading_trailing_whitespace(self):
        assert slugify("   hello world   ") == "hello-world"


class TestPunctuation:
    def test_single_punct_to_dash(self):
        assert slugify("foo,bar") == "foo-bar"

    def test_consecutive_punctuation_collapses(self):
        assert slugify("foo!!!???bar") == "foo-bar"

    def test_mixed_space_and_punct_collapses(self):
        assert slugify("foo -- bar") == "foo-bar"

    def test_leading_trailing_punct_stripped(self):
        assert slugify("!!!hello!!!") == "hello"

    def test_dots_and_slashes(self):
        assert slugify("a/b.c") == "a-b-c"


class TestChinese:
    def test_pure_chinese_preserved(self):
        assert slugify("你好世界") == "你好世界"

    def test_chinese_with_spaces(self):
        assert slugify("你好 世界") == "你好-世界"

    def test_chinese_english_mixed(self):
        assert slugify("Hello 你好 World") == "hello-你好-world"

    def test_chinese_with_punctuation(self):
        # Chinese punctuation, like the ideographic comma, is not a word char -> becomes '-'
        assert slugify("你好，世界") == "你好-世界"

    def test_chinese_consecutive_punct(self):
        assert slugify("你好！！！世界") == "你好-世界"


class TestEdges:
    def test_empty_string(self):
        assert slugify("") == ""

    def test_whitespace_only(self):
        assert slugify("     ") == ""

    def test_punctuation_only(self):
        assert slugify("!!!???") == ""

    def test_digits_preserved(self):
        assert slugify("Version 2 Release 10") == "version-2-release-10"

    def test_already_slug(self):
        assert slugify("already-a-slug") == "already-a-slug"

    def test_non_string_raises(self):
        with pytest.raises(TypeError):
            slugify(123)
