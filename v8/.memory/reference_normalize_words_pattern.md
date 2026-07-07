---
name: normalize_words_pattern
description: Preferred implementation for whitespace normalization
type: reference
---

Idiomatic `normalize_words(text)` implementation: `" ".join(text.split()).lower()`. `str.split()` with no args collapses every whitespace run (spaces/tabs/newlines/CR/formfeed) and strips leading/trailing whitespace automatically. Guard non-str inputs with `TypeError`.
