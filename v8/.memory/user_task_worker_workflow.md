---
name: task_worker_workflow
description: Preferred multi-agent task/worktree delegation pattern
type: user
---

User's preferred pattern for multi-agent work:

1. `create_task` (subject + description) per unit of work, using `blockedBy` for dependencies.
2. `create_worktree(name, task_id)` per task to isolate work under `v8/.worktrees/task-*/`.
3. `spawn_teammate` per owner (e.g. `worker_a`, alice, bob).
4. Drive owners through the protocol: `request_plan` → `review_plan` (approve) → owner `claim_task` → do the work → `send_message` summary to lead → `complete_task` → `request_shutdown`.
5. `complete_task` auto-delivers producer's outputs to dependent tasks' worktrees and may auto-claim dependents. Run verification (pytest, etc.) in the consumer worktree.
6. Mirror final files to workspace root for the pre-final validator.
7. Lead monitors via `check_inbox` / `list_tasks` and summarizes at the end (task IDs, worktrees, delivered files, test results).

User expects workers to operate autonomously without step-by-step direction.
