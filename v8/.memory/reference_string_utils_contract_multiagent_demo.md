---
name: string_utils_contract_multiagent_demo
description: Reference implementation contract for v8_multiagent_demo/string_utils.py
type: reference
---

Contract for `v8_multiagent_demo/string_utils.py` used in v8 demo tasks:

```python
def _require_str(value, name):
    if not isinstance(value, str):
        raise TypeError(...)

def reverse(text):
    _require_str(text, 'text')
    return text[::-1]

def is_palindrome(text):
    # case-insensitive, strips non-alphanumeric
    _require_str(text, 'text')
    normalized = [ch.lower() for ch in text if ch.isalnum()]
    return normalized == normalized[::-1]

def count_words(text):
    _require_str(text, 'text')
    return len(text.split())
```

All three raise `TypeError` on non-`str` input. `is_palindrome` treatment: `"race,car"` → `"racecar"` → True; `"race a car"` → `"raceacar"` → False. Test suite covers 62 cases across `TestReverse`, `TestIsPalindrome`, `TestCountWords`.
