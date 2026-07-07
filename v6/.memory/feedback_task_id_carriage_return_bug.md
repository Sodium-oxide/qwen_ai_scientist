---
name: task_id_carriage_return_bug
description: Bug where blockedBy task IDs got \r appended
type: feedback
---

When creating tasks with `blockedBy`, task IDs can accidentally include `\r` (carriage return) characters, likely from Windows line-ending residue. The task system accepts them as-is but the dependency IDs become invalid/mismatched. Always sanitize task IDs before passing to `blockedBy`.
