---
name: v8_task_worker_workflow
description: Preferred multi-agent task delegation pattern
type: user
---

User prefers autonomous worker pattern for task delegation:

1. Create persistent tasks via `create_task` (subject + description)
2. Spawn workers via `spawn_teammate` (e.g. `worker_a`) with instructions to:
   - Scan task board (`list_tasks`)
   - Self-claim pending unblocked tasks (`claim_task`, owner=worker name)
   - Complete tasks (`complete_task`)
   - Optionally isolate work via `create_worktree(name, task_id)`
   - Report summary back to lead via `message/result`
3. Lead monitors progress via `check_inbox`

User expects workers to operate autonomously without step-by-step direction.
