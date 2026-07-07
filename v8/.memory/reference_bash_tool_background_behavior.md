---
name: bash_tool_background_behavior
description: bash tool sometimes backgrounds commands
type: reference
---

# Bash Tool Background Task Behavior

The `bash` tool sometimes returns immediately with `[Background task bg_XXX started]` instead of blocking for output. The actual result arrives later as a `<task_notification>` message with `<status>completed</status>` and `<output>` containing stdout+exit_code.

- Do NOT re-run the same command when this happens — wait for the task_notification.
- Re-running just spawns another background task and wastes turns.
- The notification includes full stdout/stderr and exit_code, same as a synchronous run.
