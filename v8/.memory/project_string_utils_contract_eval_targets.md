---
name: string_utils_contract_eval_targets
description: Contract for v8_eval_targets/string_utils.py
type: project
---

`v8_eval_targets/string_utils.py` exposes 5 pure functions with type annotations + docstrings:
- `reverse(text: str) -> str`
- `is_palindrome(text: str) -> bool` — ignores case and non-alphanumeric chars
- `count_words(text: str) -> int` — splits on whitespace
- `capitalize_words(text: str) -> str`
- `truncate(text: str, max_length: int, suffix: str = "...") -> str`

Error rules:
- Non-`str` input → `TypeError`
- `truncate` with `max_length < 0` → `ValueError`
- When `max_length <= len(suffix)`, `truncate` returns `suffix[:max_length]` rather than raising.

Style: matches `calc.py`/`slugify.py` (no side effects, no external deps). Tests live in `v8_eval_targets/test_string_utils.py`, ≥12 cases covering all functions + TypeError + ValueError, run with `python -m pytest -v` from inside `v8_eval_targets/`.
