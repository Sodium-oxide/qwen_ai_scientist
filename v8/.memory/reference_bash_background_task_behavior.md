---
name: bash_background_task_behavior
description: bash tool runs commands as background tasks
type: reference
---

# Bash Tool Background Task Behavior

The `bash` tool often returns immediately with `[Background task bg_XXX started]` instead of blocking. The actual result arrives later as a `<task_notification>` with `<status>completed</status>` and `<output>` containing stdout+stderr+exit_code.

- Do NOT re-run the same command assuming it failed silently; wait for the notification.
- Re-issuing just spawns another background task and wastes turns.
- If a duplicate was issued by mistake, both results will arrive; deduplicate in the response.
