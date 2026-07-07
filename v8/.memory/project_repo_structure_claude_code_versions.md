---
name: repo_structure_claude_code_versions
description: Repo layout: v1-v8 iterative Python versions
type: project
---

# Claude Code Repo Structure

**Workspace:** `C:\Users\31390\Desktop\2026挑战杯\claude-code`

Repo contains 88 Python files organized across 8 progressive versions (v1-v8), each in its own directory. Later versions add more features iteratively.

## Version progression (module additions)
- **v1** (4): `main`, `permission`, `tools` — minimal base
- **v2** (6): adds `hook`, `skill`
- **v3** (8): adds `config`, `llm`, `subagent`; drops `permission`
- **v4** (10): adds `compact`, `log`
- **v5** (12): adds `memory`, `recovery`
- **v6** (13): adds `task_system`
- **v7** (14): adds `agent_teams`
- **v8** (18): adds `cron_scheduler`, `mcp_plugin`, `todo_state`, `worktree_isolation`

## Common modules
`main.py`, `tools.py`, `config.py`, `llm.py`, `hook.py`, `skill.py`, `subagent.py`, `compact.py`, `log.py`, `memory.py`, `recovery.py`, `task_system.py`

v8 is the most feature-complete. Each version is a self-contained package with `__init__.py`. Project is a Claude Code implementation/clone (2026 挑战杯 challenge cup).
