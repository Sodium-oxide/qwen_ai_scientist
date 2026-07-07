---
name: tdd_workflow_preference
description: User prefers strict TDD
type: user
---

# TDD Workflow Preference

User explicitly requests TDD flow: **write tests first → implement → run tests → fix until green**.

- Announce the plan before coding.
- Use `todo_write` to track: (1) write tests, (2) implement, (3) run & fix.
- Group tests into logical test classes covering: basic cases, whitespace, punctuation, unicode/CJK, edges (empty, type errors).
