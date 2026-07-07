---
name: string_utils_task_ids
description: Persistent task IDs for string_utils work
type: reference
---

- Task A (implement `v8_eval_targets/string_utils.py`): `task_1782969051382_7040`, worktree `v8/.worktrees/alice_string_utils`, owner=alice
- Task B (write tests `v8_eval_targets/test_string_utils.py`): `task_1782969070627_5537` with `blockedBy=[task_1782969051382_7040]`, worktree `v8/.worktrees/bob_string_tests`, owner=bob
- Stale duplicate (no blockedBy): `task_1782969051391_7643` — pending, superseded by `_5537`
