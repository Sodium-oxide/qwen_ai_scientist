---
name: pre_final_validation_hook
description: Pre-final validation enforces concrete outcomes
type: reference
---

This environment runs a pre-final validation step that can block the final response until: (a) referenced persistent tasks are in `completed` status (not just `in_progress` or `pending`), and (b) expected files exist at the exact paths mentioned in the user request. It will re-invoke the assistant with a message starting `Pre-final validation failed. You must continue instead of ending.` listing what to fix. Always drive tasks to `completed` and create files at the literal paths the user specified.
