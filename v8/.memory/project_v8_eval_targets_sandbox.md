---
name: v8_eval_targets_sandbox
description: Sandbox project for eval tasks (canonical path + style + existing modules)
type: project
---

# v8_eval_targets Sandbox

## Canonical path
`v8_eval_targets/` (plural, with trailing `s`) is the canonical directory that validators check. An older `v8_eval_target/` (singular) also exists in the workspace containing legacy `calc.py`/`slugify.py` work, but new work should target the plural path.

## Existing modules (legacy singular dir)
- `calc.py` + `test_calc.py` — `add`/`subtract`/`multiply`/`divide`; `divide` raises `ZeroDivisionError` on 0. 5 pytest cases, all green.
- `slugify.py` + `test_slugify.py` — 23 tests, all passing. Lowercases ASCII, whitespace/non-word → `-` with runs collapsed, preserves CJK ranges (0x3400-0x4DBF, 0x4E00-0x9FFF, plus Ext B/C-F/G), strips leading/trailing `-`, `unicodedata.normalize('NFKC', ...)` before lowercasing, non-str → `TypeError`.

## Style conventions
Pytest, import modules by bare name (tests run from inside the eval target directory), one `test_xxx` per function, separating normal path / boundary / exception cases. Pure functions with type annotations + docstrings, no side effects, no external deps.

## Run tests
`cd v8_eval_targets && python -m pytest -v` (Python 3.13.9, pytest 8.4.2, win32).
