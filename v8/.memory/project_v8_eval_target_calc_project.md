---
name: v8_eval_target_calc_project
description: Python calc project with pytest
type: project
---

# v8_eval_target Calc Project

## Location
`C:\Users\31390\Desktop\2026挑战杯\claude-code\v8_eval_target\`

## Files
- `calc.py` — Implements `add`, `subtract`, `multiply`, `divide`. `divide` raises `ZeroDivisionError` when divisor is 0.
- `test_calc.py` — 5 pytest test cases: `test_add`, `test_subtract`, `test_multiply`, `test_divide`, `test_divide_by_zero`.

## Test Results
- Command: `python -m pytest -v` (run from inside `v8_eval_target/`)
- Environment: Python 3.13.9, pytest 8.4.2, win32
- Result: **5 passed in 0.08s** (all green on first run, no fixes needed)
