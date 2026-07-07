---
name: mock_teammates_behavior
description: Spawned teammates are chat-only
type: project
---

`spawn_teammate` teammates (alice, bob, etc.) in this workspace are effectively chat/echo loops — they do not actually edit files or run commands. They tend to echo prompts back as plans and hit a work-turn limit reporting things like "file written" without any real change on disk. The lead must do the actual file edits and verification, then close out the persistent tasks. Still drive them through the protocol (request_plan → review_plan → claim_task → complete_task → request_shutdown) because pre-final validation checks that persistent tasks reach `completed` status.
