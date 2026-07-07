# Topic 5: ULTRAPLAN / Coordinator Mode — 多代理并行工作流

## TLDR

ULTRAPLAN 是一个**远程多代理规划系统**，在云端 Claude 实例上运行长达 **30 分钟**的 Opus 级规划会话，通过 `ExitPlanModeScanner` 状态机追踪浏览器端的人工审批流程，支持"远程执行"和"传送回本地"两种路径。Coordinator Mode 是本地多代理编排器——协调者生成 worker 代理并行干活，通过 XML `<task-notification>` 接收异步汇报，支持 tmux/iTerm2/进程内三种执行后端。两者加上 TeamCreate/SendMessage 工具链，构成了完整的 "一个 Claude 指挥多个 Claude" agent swarm 架构。

---

## 1. ULTRAPLAN——30 分钟远程规划

定义在 `src/commands/ultraplan.tsx`。

### 核心常量

```typescript
const ULTRAPLAN_TIMEOUT_MS = 30 * 60 * 1000  // 30 分钟

function getUltraplanModel(): string {
  return getFeatureValue_CACHED_MAY_BE_STALE(
    'tengu_ultraplan_model',
    ALL_MODEL_CONFIGS.opus46.firstParty  // 默认 Opus 4.6
  )
}
```

30 分钟 + Opus 级模型——这不是快速原型，是认真的长时间规划。

### 提示词构建

```typescript
// prompt.txt 通过 Bun bundler 内联为字符串
const _rawPrompt = require('../utils/ultraplan/prompt.txt')
const DEFAULT_INSTRUCTIONS: string = (typeof _rawPrompt === 'string'
  ? _rawPrompt : _rawPrompt.default).trimEnd()

// Ant 用户可通过环境变量覆盖提示词（开发调试用）
const ULTRAPLAN_INSTRUCTIONS: string =
  "external" === 'ant' && process.env.ULTRAPLAN_PROMPT_FILE
    ? readFileSync(process.env.ULTRAPLAN_PROMPT_FILE, 'utf8').trimEnd()
    : DEFAULT_INSTRUCTIONS

export function buildUltraplanPrompt(blurb: string, seedPlan?: string): string {
  const parts: string[] = []
  if (seedPlan) parts.push('Here is a draft plan to refine:', '', seedPlan, '')
  parts.push(ULTRAPLAN_INSTRUCTIONS)
  if (blurb) parts.push('', blurb)
  return parts.join('\n')
}
```

支持 seed plan——可以把草稿计划交给远程 Claude 精炼。

### 执行流程

```
用户输入 /ultraplan "重构认证系统"
    ↓
teleportToRemote() → 建立 CCR 远程会话
    ↓
注册 RemoteAgentTask（type: 'ultraplan'）
    ↓
startDetachedPoll() → 每 3 秒轮询 CCR 事件流
    ↓
ExitPlanModeScanner 解析事件：
  - running → 远程 Claude 正在探索代码库
  - needs_input → 浏览器 PlanModal 等待用户操作
  - plan_ready → 用户正在审批计划
    ↓
用户在浏览器审批/拒绝/修改计划
    ↓
approved → 两种路径：
  - executionTarget = 'remote' → 远程会话直接执行
  - executionTarget = 'local' → teleport 回本地终端
```

### 两种完成路径

```typescript
if (executionTarget === 'remote') {
  // 远程直接执行——更新 task 为 completed
  updateTaskState<RemoteAgentTaskState>(taskId, setAppState, t => ({
    ...t, status: 'completed', endTime: Date.now()
  }))
} else {
  // Teleport：设置 pendingChoice 让 REPL 弹出 UltraplanChoiceDialog
  setAppState(prev => ({
    ...prev,
    ultraplanPendingChoice: { plan, sessionId, taskId }
  }))
}
```

---

## 2. ExitPlanModeScanner——事件流状态机

定义在 `src/utils/ultraplan/ccrSession.ts`。这是一个**纯函数式状态机**，没有 I/O 和定时器，可用合成/录制事件做单元测试：

```typescript
/**
 * Pure stateful classifier for the CCR event stream.
 *
 * Precedence (approved > terminated > rejected > pending > unchanged):
 * pollRemoteSessionEvents paginates up to 50 pages per call, so one ingest
 * can span seconds of session activity. A batch may contain both an approved
 * tool_result AND a subsequent {type:'result'} (user approved, then remote
 * crashed). The approved plan is real and in threadstore — don't drop it.
 */
export class ExitPlanModeScanner {
  private exitPlanCalls: string[] = []         // ExitPlanMode tool_use IDs
  private results = new Map<string, ToolResultBlockParam>()  // tool_result 按 ID
  private rejectedIds = new Set<string>()      // 被拒绝的 tool_use IDs
  private terminated: { subtype: string } | null = null
  everSeenPending = false

  get rejectCount(): number { return this.rejectedIds.size }

  get hasPendingPlan(): boolean {
    const id = this.exitPlanCalls.findLast(c => !this.rejectedIds.has(c))
    return id !== undefined && !this.results.has(id)
  }

  ingest(newEvents: SDKMessage[]): ScanResult {
    for (const m of newEvents) {
      if (m.type === 'assistant') {
        // 收集 ExitPlanModeV2 tool_use 调用
        for (const block of m.message.content) {
          if (block.type === 'tool_use' && block.name === EXIT_PLAN_MODE_V2_TOOL_NAME) {
            this.exitPlanCalls.push(block.id)
          }
        }
      } else if (m.type === 'user') {
        // 收集 tool_result（用户的审批/拒绝响应）
        for (const block of m.message.content) {
          if (block.type === 'tool_result') {
            this.results.set(block.tool_use_id, block)
          }
        }
      } else if (m.type === 'result' && m.subtype !== 'success') {
        // 非 success 的 result = 会话终止
        this.terminated = { subtype: m.subtype }
      }
    }

    // 从最新的 ExitPlanMode 调用开始反向扫描
    for (let i = this.exitPlanCalls.length - 1; i >= 0; i--) {
      const id = this.exitPlanCalls[i]!
      if (this.rejectedIds.has(id)) continue
      const tr = this.results.get(id)
      if (!tr) return { kind: 'pending' }      // 等待浏览器审批
      if (tr.is_error === true) {
        const teleportPlan = extractTeleportPlan(tr.content)
        if (teleportPlan) return { kind: 'teleport', plan: teleportPlan }
        return { kind: 'rejected', id }         // 用户拒绝
      }
      return { kind: 'approved', plan: extractApprovedPlan(tr.content) }
    }
    // ...
  }
}
```

### Teleport 哨兵

```typescript
export const ULTRAPLAN_TELEPORT_SENTINEL = '__ULTRAPLAN_TELEPORT_LOCAL__'
// 从 deny 的 tool_result 内容中提取
```

用户在浏览器点"本地执行"时，拒绝消息的 content 里包含这个哨兵标记 + 计划文本。

### 计划文本提取

```typescript
// approved: "## Approved Plan:" 或 "## Approved Plan (edited by user):"
function extractApprovedPlan(content): string { ... }

// teleport: SENTINEL + 换行 + 计划文本
function extractTeleportPlan(content): string | null { ... }
```

---

## 3. Coordinator Mode——本地多代理编排

定义在 `src/coordinator/coordinatorMode.ts`。

### 系统提示词（约 260 行）

```typescript
export function getCoordinatorSystemPrompt(): string {
  return `You are Claude Code, an AI assistant that orchestrates software
engineering tasks across multiple workers.

## 1. Your Role

You are a **coordinator**. Your job is to:
- Help the user achieve their goal
- Direct workers to research, implement and verify code changes
- Synthesize results and communicate with the user
- Answer questions directly when possible — don't delegate work that you can
  handle without tools

Every message you send is to the user. Worker results and system notifications
are internal signals, not conversation partners — never thank or acknowledge
them. Summarize new information for the user as it arrives.

## 2. Your Tools

- **Agent** — Spawn a new worker
- **SendMessage** — Continue an existing worker
- **TaskStop** — Stop a running worker

When calling Agent:
- Do not use one worker to check on another
- Do not use workers to trivially report file contents or run commands
- Do not set the model parameter
- Continue workers whose work is complete via SendMessage to take advantage of
  their loaded context
- After launching agents, briefly tell the user what you launched and end your
  response. Never fabricate or predict agent results.

### Agent Results

Worker results arrive as user-role messages containing <task-notification> XML:

\`\`\`xml
<task-notification>
  <task-id>{agentId}</task-id>
  <status>completed|failed|killed</status>
  <summary>{human-readable status summary}</summary>
  <result>{agent's final text response}</result>
  <usage>
    <total_tokens>N</total_tokens>
    <tool_uses>N</tool_uses>
    <duration_ms>N</duration_ms>
  </usage>
</task-notification>
\`\`\``
}
```

关键规则：
- **从不感谢 worker**——worker 汇报是内部信号
- **不要用 worker 检查 worker**——它们完成时会自动通知
- **综合理解不可委托**——你必须自己理解结果再分发新任务
- **发完 Agent 就结束当前轮**——不要编造结果

---

## 4. Agent 工具参数——Swarm 的核心接口

定义在 `src/tools/AgentTool/AgentTool.tsx`：

```typescript
const fullInputSchema = lazySchema(() => {
  const multiAgentInputSchema = z.object({
    name: z.string().optional().describe(
      'Name for the spawned agent. Makes it addressable via SendMessage({to: name})'
    ),
    team_name: z.string().optional().describe(
      'Team name for spawning. Uses current team context if omitted.'
    ),
    mode: permissionModeSchema().optional().describe(
      'Permission mode for spawned teammate (e.g., "plan" to require approval).'
    ),
  })
  return baseInputSchema().merge(multiAgentInputSchema).extend({
    isolation: z.enum(['worktree', 'remote']).optional().describe(
      '"worktree" creates a temporary git worktree. ' +
      '"remote" launches in a remote CCR environment (always background).'
    ),
    cwd: z.string().optional().describe(
      'Absolute path override for working directory. ' +
      'Mutually exclusive with isolation: "worktree".'
    ),
  })
})
```

- `name`: 给代理起名，之后可用 `SendMessage({to: "researcher"})` 继续指挥
- `isolation: 'worktree'`: git worktree 隔离（所有人可用）
- `isolation: 'remote'`: CCR 远程隔离（ant only）
- `mode: 'plan'`: 要求 worker 在执行前获得 plan 审批

---

## 5. 团队管理工具

### TeamCreateTool

```typescript
// src/tools/TeamCreateTool/TeamCreateTool.ts
const teamFile: TeamFile = {
  name: finalTeamName,
  description: _description,
  createdAt: Date.now(),
  leadAgentId,                        // 格式：team-lead@{team-name}
  leadSessionId: getSessionId(),      // 用于跨会话发现
  members: [{
    agentId: leadAgentId,
    name: TEAM_LEAD_NAME,             // 固定 "team-lead"
    agentType: leadAgentType,
    model: leadModel,
    joinedAt: Date.now(),
    cwd: getCwd(),
    subscriptions: [],
  }],
}
```

限制：一个 leader 只能管理一个团队。已有团队时必须先 TeamDelete。

### TeamFile 结构

```typescript
// src/utils/swarm/teamHelpers.ts
export type TeamFile = {
  name: string
  description?: string
  leadAgentId: string
  leadSessionId?: string
  teamAllowedPaths?: TeamAllowedPath[]  // 跨成员共享的文件路径
  members: Array<{
    agentId: string
    name: string
    agentType?: string
    model?: string
    planModeRequired?: boolean
    tmuxPaneId: string
    cwd: string
    worktreePath?: string
    sessionId?: string
    backendType?: BackendType        // 'tmux' | 'iterm2' | 'in-process'
    isActive?: boolean               // false = 空闲
    mode?: PermissionMode
  }>
}
```

存储位置：`~/.claude/teams/{sanitized-team-name}/config.json`

### SendMessageTool——结构化消息

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts
const StructuredMessage = z.discriminatedUnion('type', [
  z.object({ type: z.literal('shutdown_request'), reason: z.string().optional() }),
  z.object({ type: z.literal('shutdown_response'), request_id: z.string(),
             approve: semanticBoolean(), reason: z.string().optional() }),
  z.object({ type: z.literal('plan_approval_response'), request_id: z.string(),
             approve: semanticBoolean(), feedback: z.string().optional() }),
])

const inputSchema = z.object({
  to: z.string().describe(
    'Recipient: teammate name, "*" for broadcast, ' +
    '"uds:<socket-path>" for local peer, ' +
    '"bridge:<session-id>" for Remote Control peer'
  ),
  summary: z.string().optional().describe('5-10 word preview for UI'),
  message: z.union([z.string(), StructuredMessage()]),
})
```

消息不只是文本——还有结构化的 shutdown 请求/响应和 plan 审批响应。

---

## 6. 三种执行后端

定义在 `src/utils/swarm/backends/`：

| 后端 | 实现 | 可视化 | 适用场景 |
|------|------|--------|---------|
| **tmux** | 面板分裂 | 每个 worker 一个 pane | Linux/macOS |
| **iTerm2** | AppleScript 驱动 | 每个 worker 一个 tab | macOS |
| **InProcess** | AsyncLocalStorage 隔离 | 无（后台运行） | 默认 fallback |

### InProcess 后端详解

```typescript
// src/utils/swarm/inProcessRunner.ts
function createInProcessCanUseTool(identity, abortController): CanUseToolFn {
  return async (tool, input, toolUseContext, assistantMessage, toolUseID, forceDecision) => {
    const result = forceDecision ??
      (await hasPermissionsToUseTool(tool, input, toolUseContext, assistantMessage, toolUseID))

    if (result.behavior !== 'ask') return result  // allow/deny 直接通过

    // Bash 命令尝试分类器自动审批
    if (feature('BASH_CLASSIFIER') && tool.name === BASH_TOOL_NAME && result.pendingClassifierCheck) {
      const classifierDecision = await awaitClassifierAutoApproval(
        result.pendingClassifierCheck, abortController.signal
      )
      if (classifierDecision) return { behavior: 'allow', decisionReason: classifierDecision }
    }

    // 无法自动审批 → 通过 mailbox 请求 leader 审批
    // （bridge 不可用时的 fallback）
  }
}
```

InProcess 后端用 `AsyncLocalStorage` 实现并发隔离——多个 worker 在同一个 Node.js 进程中运行，但各自有独立的上下文。

---

## 7. XML 标签基础设施

定义在 `src/constants/xml.ts`：

```typescript
export const TASK_NOTIFICATION_TAG = 'task-notification'
export const TASK_ID_TAG = 'task-id'
export const STATUS_TAG = 'status'
export const SUMMARY_TAG = 'summary'
export const RESULT_TAG = 'result'          // 可选：agent 的最终文本响应
export const ULTRAPLAN_TAG = 'ultraplan'
export const REMOTE_REVIEW_TAG = 'remote-review'
export const REMOTE_REVIEW_PROGRESS_TAG = 'remote-review-progress'
export const TEAMMATE_MESSAGE_TAG = 'teammate-message'
export const CHANNEL_MESSAGE_TAG = 'channel-message'
export const CROSS_SESSION_MESSAGE_TAG = 'cross-session-message'
export const FORK_BOILERPLATE_TAG = 'fork-boilerplate'
export const FORK_DIRECTIVE_PREFIX = 'Your directive: '
```

整个 swarm 通信都基于 XML——不是 JSON，是 XML。这让它可以嵌入 LLM 对话的文本流中，而不需要额外的序列化层。

---

## 8. BugHunter 舰队——分布式 Bug 猎手

定义在 `src/commands/review/reviewRemote.ts`：

```typescript
const commonEnvVars = {
  BUGHUNTER_DRY_RUN: '1',
  BUGHUNTER_FLEET_SIZE: String(posInt(raw?.fleet_size, 5, 20)),      // 5-20 个代理
  BUGHUNTER_MAX_DURATION: String(posInt(raw?.max_duration_minutes, 10, 25)),
  BUGHUNTER_AGENT_TIMEOUT: String(posInt(raw?.agent_timeout_seconds, 600, 1800)),
  BUGHUNTER_TOTAL_WALLCLOCK: String(posInt(raw?.total_wallclock_minutes, 22, 27)),
}
```

这是 ULTRAPLAN 的一个变体——同时启动 5-20 个代理去扒一个 PR 里的 bug，每个代理独立运行 10-25 分钟，总共最长 27 分钟（留 3 分钟做最终汇总）。

---

## 设计哲学总结

1. **XML 而非 JSON**：代理通信嵌入 LLM 对话流，XML 天然适合文本混合
2. **审批而非自治**：ULTRAPLAN 需要人工在浏览器审批计划；Coordinator 用 `<task-notification>` 而非自主决策
3. **状态机优先**：ExitPlanModeScanner 是纯函数式状态机，可离线测试
4. **后端无关**：tmux/iTerm2/InProcess 三种后端，统一接口
5. **30 分钟不是随便定的**：配合 Opus 级模型 + CCR 云端执行，这是"认真想问题"的时间
