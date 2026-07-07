# Topic 1: Agent 架构极致模块化

## TLDR

Claude Code 的 Agent 架构远比外界想象的复杂。它原生支持 **Coordinator Mode**（多 Agent 协调器），通过 **UDS Inbox**（Unix Domain Socket 邮箱）让同一台机器上的多个 Claude 实例互相"聊天"，还有完整的 **Context 压缩**、**Memory 持久化** 和 **Hooks 生命周期** 系统。整个架构就是一个缩小版的"Agent 操作系统"——有进程通信、有调度器、有持久化存储、有事件钩子。

---

## 1. Coordinator Mode — 多 Agent 协调器

**源码位置：** `src/coordinator/coordinatorMode.ts`（340+ 行）

**启用条件：**
- 编译时 flag `COORDINATOR_MODE` 开启
- 运行时环境变量 `CLAUDE_CODE_COORDINATOR_MODE=1`

### 工作原理

Coordinator Mode 把 Claude 变成一个"主管"，它自己不直接写代码，而是：

1. **接收用户需求** → 拆解任务
2. **派遣 Worker Agent** → 并行研究/实现/验证
3. **汇总结果** → 合成理解后再派新任务
4. **通知用户** → 最终交付

### 四阶段工作流

| 阶段 | 执行者 | 职责 |
|------|--------|------|
| Research | Workers（并行） | 调查代码库 |
| Synthesis | **Coordinator** | 理解发现、编写规格说明 |
| Implementation | Workers | 按规格实现，提交代码 |
| Verification | Workers | 测试变更 |

### 关键设计原则

源码中反复强调一条原则：**"Never delegate understanding"（永远不要把理解委托出去）**

```
永远不要写"based on your findings, fix the bug"——
这是在把思考推给 Agent。
Coordinator 必须先读 Worker 的发现，理解后，
带着具体文件路径和行号写出聚焦的规格说明。
```

### Worker 任务通知格式

Worker 完成后通过 XML 结构化消息汇报：

```xml
<task-notification>
  <task-id>{agentId}</task-id>
  <status>completed|failed|killed</status>
  <summary>{人类可读摘要}</summary>
  <result>{agent 最终文本}</result>
  <usage>
    <total_tokens>N</total_tokens>
    <tool_uses>N</tool_uses>
    <duration_ms>N</duration_ms>
  </usage>
</task-notification>
```

### Continue vs. Spawn 决策

| 场景 | 策略 |
|------|------|
| Worker 已探索过目标文件，上下文重叠度高 | `SendMessage` 继续 |
| 不同任务、需要验证别人写的代码 | 新建 Worker |

---

## 2. UDS Inbox — 跨会话消息通信

**Feature Flag：** `UDS_INBOX`
**核心文件：** `src/hooks/useInboxPoller.ts`（970 行）

### 这是什么？

UDS（Unix Domain Socket）Inbox 让同一台机器上的多个 Claude Code 实例能互相发消息。想象一下：你开了三个终端窗口，每个都跑着 Claude Code，它们可以**像 Slack 群聊一样沟通**。

### 消息路由

`SendMessage` 工具的 `to` 字段支持多种目标：

| 目标格式 | 含义 |
|----------|------|
| `teammate_name` | 同进程或 tmux 面板中的队友 |
| `"*"` | 广播给所有队友 |
| `"uds:/path/to.sock"` | 本机其他 Claude 会话 |
| `"bridge:session_..."` | 跨机器 Remote Control 对端 |

### 轮询机制

- 每 **1 秒** 轮询一次（`INBOX_POLL_INTERVAL_MS = 1000`）
- 空闲时：消息立即作为新一轮对话提交
- 忙碌时：排入 `AppState.inbox` 队列，当前轮结束后投递

### 协议消息类型

不只是文字聊天，还有结构化的管理消息：

| 消息类型 | 用途 |
|----------|------|
| `shutdown_request` / `response` | Team Lead 发起关闭，需要审批 |
| `plan_approval_response` | Leader 审批队友的 Plan Mode |
| `team_permission_update` | 广播权限规则变更 |
| `mode_set_request` | Leader 切换队友的权限模式 |
| `sandbox_permission_request/response` | 网络访问审批 |

### 安全控制

- 只有 Team Lead 能批准关闭请求
- 只有 Team Lead 能发送模式切换请求
- 消息去重：即使 `markMessagesAsRead` 失败也不会重复处理
- 畸形消息静默跳过并记录调试日志

---

## 3. Sub-Agent 系统

**核心工具：** `AgentTool`（`src/tools/AgentTool/`）

### Agent 类型

源码中定义了多种 Sub-Agent 类型：

| 类型 | 用途 |
|------|------|
| `general-purpose` | 通用研究和多步骤任务 |
| `Explore` | 快速代码库探索（quick/medium/very thorough） |
| `Plan` | 架构设计和实现规划 |
| `worker` | Coordinator 模式下的工作节点 |
| `code-reviewer` | 代码审查 |
| `code-simplifier` | 代码简化和重构 |

### Team 管理

- `TeamCreateTool`：创建 Agent 团队
- `TeamDeleteTool`：解散团队
- `TaskCreateTool` / `TaskUpdateTool` / `TaskStopTool`：任务生命周期管理

### 隔离机制

- `EnterWorktreeTool` / `ExitWorktreeTool`：通过 Git Worktree 实现文件系统级隔离
- 每个 Agent 可以在独立的 worktree 中工作，互不干扰

---

## 4. Context 压缩（Compact）

**源码位置：** `src/services/compact/`（compact.ts 1800+ 行）

### 压缩策略

| 类型 | 场景 |
|------|------|
| Full Compact | 整个对话压缩为摘要 |
| Partial Compact | 只压缩早期消息，保留最近的 |
| Micro Compact | 轻量级压缩（`microCompact.ts`） |
| Session Memory Compact | 会话记忆专用压缩 |
| Auto Compact | 自动触发（`autoCompact.ts`，窗口大小由 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 控制） |

### 压缩后的 9 大必需部分

Compact 的输出有严格的结构要求，分析用 `<analysis>` 标签包裹（事后剥离），摘要必须包含：

1. **Primary Request and Intent** — 用户到底要什么
2. **Key Technical Concepts** — 涉及的技术概念
3. **Files and Code Sections** — 完整代码片段（不能省略！）
4. **Errors and Fixes** — 遇到的错误和修复
5. **Problem Solving** — 解题过程
6. **All User Messages** — **所有**用户消息（关键，用于理解反馈）
7. **Pending Tasks** — 待办任务
8. **Current Work** — 当前工作状态
9. **Optional Next Step** — 下一步建议（含直接引用）

---

## 5. Memory 持久化

**源码位置：** `src/memdir/`

### 记忆类型

| 类型 | 描述 | 共享级别 |
|------|------|----------|
| `user` | 用户角色、偏好、知识水平 | 始终私有 |
| `feedback` | 用户对工作方式的纠正和确认 | 默认私有 |
| `project` | 项目上下文、进行中的工作 | 私有或团队 |
| `reference` | 外部系统的指针（如 Linear 项目、Grafana 面板） | 通常团队共享 |

### 存储结构

- 路径：`~/.claude/projects/<slug>/memory/`
- 索引文件：`MEMORY.md`（限制 200 行或 25,000 字节，先触发者为准）
- 每条记忆一个独立 `.md` 文件，带 YAML frontmatter
- 自动记忆：由 `isAutoMemoryEnabled` 特性门控制

### 团队记忆同步

- `src/services/teamMemorySync/` — 团队级别的记忆同步
- 通过 `TEAMMEM` feature flag 控制
- 支持团队共享的编码规范、项目上下文

---

## 6. Hooks 生命周期系统

**不只是 React Hooks！** Claude Code 有一套完整的**用户可配置的生命周期钩子系统**。

### 事件钩子类型

| 钩子 | 触发时机 |
|------|----------|
| `PreToolUse` | 任何工具执行**之前** |
| `PostToolUse` | 任何工具执行**之后** |
| `PreWrite` | 文件写入**之前** |
| `PostWrite` | 文件写入**之后** |
| `PreCommit` | Git 提交**之前** |
| `PostCommit` | Git 提交**之后** |

### 配置方式

钩子在 `settings.json` 中配置：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "event": "bash",
        "command": "echo 'Running bash command: ${command}'"
      }
    ],
    "PostWrite": [
      {
        "event": "*",
        "command": "ruff check ${filepath} --fix"
      }
    ]
  }
}
```

### 配置文件层级

| 文件 | 作用域 |
|------|--------|
| `.claude/settings.json` | 团队共享（提交到 Git） |
| `.claude/settings.local.json` | 个人（不提交） |
| `~/.claude/settings.json` | 全局用户配置 |

### 安全控制

- 管理策略可以通过 `disableAllHooks` 完全禁用用户钩子
- 钩子可以读取和修改工具输入、文件 diff、commit message
- Hook 的审批结果可以持久化为永久权限规则

---

## 架构总结

```
┌─────────────────────────────────────────────────┐
│                   用户终端                        │
├─────────────────────────────────────────────────┤
│            Coordinator (主管 Agent)               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ Worker 1 │ │ Worker 2 │ │ Worker 3 │        │
│  │(Research)│ │(Implement)│ │(Verify) │        │
│  └────┬─────┘ └────┬─────┘ └────┬────┘        │
│       │             │            │              │
│       └─────────────┼────────────┘              │
│                     │                           │
│              UDS Inbox (消息总线)                 │
├─────────────────────────────────────────────────┤
│  Context 压缩  │  Memory 持久化  │  Hooks 钩子   │
├─────────────────────────────────────────────────┤
│  Tools │ Commands │ Skills │ Plugins │ MCP      │
└─────────────────────────────────────────────────┘
```

这套架构已经不是一个简单的 CLI 工具——它是一个**完整的 Agent 操作系统**，具备进程管理、进程间通信、持久化存储、事件驱动和插件体系。
