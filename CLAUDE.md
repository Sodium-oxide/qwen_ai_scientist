# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A read-only source snapshot of Anthropic's Claude Code CLI, extracted from a publicly exposed source map in the npm package (March 2026). This is a research/analysis archive — there is no build system, no package.json, no tests, and no runnable application. The `src/` directory contains ~1,900 TypeScript files (~512K lines).

## Tech Stack

- **Runtime**: Bun (uses `bun:bundle` feature flags for dead code elimination)
- **Language**: TypeScript (strict)
- **Terminal UI**: React + Ink (terminal rendering framework)
- **CLI Parsing**: Commander.js (`@commander-js/extra-typings`)
- **Schema Validation**: Zod v4
- **Protocols**: MCP SDK (Model Context Protocol), LSP
- **API**: Anthropic SDK
- **Telemetry**: OpenTelemetry + gRPC
- **Feature Flags**: GrowthBook

## Architecture

### Core Engine

- `src/main.tsx` — Entrypoint. Commander.js CLI parser + React/Ink renderer init. Uses parallel prefetch side-effects (MDM settings, keychain reads, GrowthBook) before heavy imports.
- `src/QueryEngine.ts` (~46K lines) — Core LLM API interaction. Handles streaming, tool-call loops, thinking mode, retry logic, token counting.
- `src/Tool.ts` (~29K lines) — Base types/interfaces for all tools: input schemas, permission models, progress state.
- `src/tools.ts` — Tool registry. Conditionally loads tools based on `bun:bundle` feature flags and `USER_TYPE` env var.
- `src/commands.ts` (~25K lines) — Command registry for all slash commands. Same conditional loading pattern.
- `src/context.ts` — System/user context collection (git status, CLAUDE.md content, memory files).

### Key Subsystems

- **Tools** (`src/tools/`) — Each tool is a self-contained module (BashTool, FileReadTool, FileEditTool, GlobTool, GrepTool, AgentTool, etc.). ~40 tools total.
- **Commands** (`src/commands/`) — Slash commands (`/commit`, `/review`, `/compact`, `/doctor`, etc.). ~50+ commands.
- **Services** (`src/services/`) — External integrations: `api/` (Anthropic API client), `mcp/` (MCP server connections), `oauth/`, `lsp/`, `analytics/` (GrowthBook), `compact/` (context compression), `plugins/`.
- **Bridge** (`src/bridge/`) — Bidirectional IDE communication layer (VS Code, JetBrains). JWT auth, session management, REPL bridging.
- **Permission System** (`src/hooks/toolPermission/`) — Per-tool permission checking. Modes: `default`, `plan`, `bypassPermissions`, `auto`.
- **Components** (`src/components/`) — ~140 Ink UI components. Main app in `App.tsx`.
- **State** (`src/state/`) — `AppState.tsx` is the central state container.
- **Coordinator** (`src/coordinator/`) — Multi-agent orchestration for agent swarms.
- **Skills** (`src/skills/`) — Reusable workflow definitions executed via SkillTool.
- **Plugins** (`src/plugins/`) — Plugin loader for built-in and third-party plugins.
- **Memory** (`src/memdir/`) — Persistent memory directory system.

### Design Patterns

- **Feature flags via `bun:bundle`**: `feature('PROACTIVE')`, `feature('KAIROS')`, `feature('BRIDGE_MODE')`, `feature('DAEMON')`, `feature('VOICE_MODE')`, `feature('AGENT_TRIGGERS')`, `feature('COORDINATOR_MODE')`, `feature('MONITOR_TOOL')`. Inactive code is stripped at build time.
- **Lazy loading**: Heavy modules (OpenTelemetry, gRPC, analytics) use dynamic `import()` / `require()` to defer loading.
- **Circular dependency breaking**: Several modules use lazy `require()` wrapped in getter functions (e.g., `getTeamCreateTool()`) to break import cycles.
- **Ant-only code**: Internal Anthropic tools gated on `process.env.USER_TYPE === 'ant'` (REPLTool, SuggestBackgroundPRTool, agents-platform command).

## Working With This Codebase

Since there is no build system or tests, typical tasks are:
- **Reading and understanding code** — use Grep/Glob to navigate the large file count
- **Architecture analysis** — trace flows from `main.tsx` through `QueryEngine.ts` to tool execution
- **Searching for patterns** — many files are very large (QueryEngine.ts ~46K lines, Tool.ts ~29K lines), so use line-range reads
