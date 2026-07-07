---
name: v8_multiagent_protocol
description: v8 multi-agent MCP tools, worktree layout, and validator behavior
type: project
---

The v8 multiagent system uses MCP tools: `create_task`, `create_worktree`, `spawn_teammate`, `request_plan`/`review_plan`, `claim_task`, `complete_task`, `send_message`, `check_inbox`, `list_tasks`, `request_shutdown`.

- Worktrees live under `v8/.worktrees/<name>/` relative to workspace root.
- Tasks support `blockedBy` dependencies; completing a blocker unlocks (and may auto-claim) dependents.
- On `complete_task`, deliverables from a task's worktree are auto-delivered to dependent tasks' worktrees, but NOT mirrored to workspace root.
- Spawned teammates (alice/bob) operate via chat/messages only; the Lead performs actual file edits and command execution on their behalf. Teammates auto-exit after task completion; `request_shutdown` on an already-exited teammate returns "not running". Sessions have IDs like `alice_<timestamp>_<n>`.

## Pre-final validator file checks
The validator looks for expected task output files at:
1. Workspace root directly (e.g. `./string_utils.py`)
2. The module directory at workspace root (`v8_multiagent_demo/`)
3. Each relevant worktree's module directory (`v8/.worktrees/task-*/v8_multiagent_demo/`)

Must manually mirror final artifacts to root in addition to worktrees before the final summary.
