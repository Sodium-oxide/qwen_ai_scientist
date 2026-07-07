---
name: windows_shell_environment
description: Windows cmd shell quirks
type: reference
---

# Windows Shell Environment

Workspace runs on Windows (cmd-style shell), not bash:
- `mkdir -p` fails with "系统找不到指定的路径" — use `mkdir dir 2>nul` instead.
- `ls` is not available — use `dir`.
- Chain commands with `&` (or `&&` also works in this env).
- Output is Chinese-localized (e.g., "驱动器 C 中的卷是 OS").
- Python path: `C:\Users\31390\anaconda3\python.exe` (Python 3.13.9, pytest 8.4.2 available).
