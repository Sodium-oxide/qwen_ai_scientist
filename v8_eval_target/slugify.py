"""slugify(text) -> str

Rules:
- Lowercase ASCII letters.
- Whitespace and any non-word characters become '-'.
- Consecutive separators collapse into a single '-'.
- Chinese / CJK characters are preserved.
- Leading/trailing '-' are stripped.
- Non-str input raises TypeError.
"""

import re
import unicodedata


# CJK Unified Ideographs (basic + common extensions).
_CJK_RANGES = (
    (0x3400, 0x4DBF),    # CJK Ext A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x20000, 0x2A6DF),  # CJK Ext B
    (0x2A700, 0x2EBEF),  # CJK Ext C-F
    (0x30000, 0x3134F),  # CJK Ext G
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def _is_keep(ch: str) -> bool:
    """Characters kept verbatim (after lowercasing): ascii alnum or CJK."""
    if ch.isascii() and ch.isalnum():
        return True
    return _is_cjk(ch)


def slugify(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError(f"slugify expects str, got {type(text).__name__}")

    # Normalize so composed characters behave predictably.
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()

    out_chars = []
    for ch in text:
        if _is_keep(ch):
            out_chars.append(ch)
        else:
            out_chars.append("-")

    result = "".join(out_chars)
    # Collapse runs of '-' and strip.
    result = re.sub(r"-+", "-", result).strip("-")
    return result
