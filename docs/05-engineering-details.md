# Topic 5: 工程实现细节全暴露

## TLDR

Claude Code 的完整工程内幕被源码暴露无遗：**System Prompt 如何构建**（优先级层叠 + 缓存 + 注入点）、**沙箱如何隔离**（路径约定 + 文件系统规则 + 网络策略）、**权限系统如何运作**（分类器 + Hook + 用户审批三层决策）、**Tool Use 完整流水线**（40+ 工具的注册/权限/执行全链路）、**Query 引擎如何调度**（流式响应 + 工具调用循环 + 重试 + Token 计数）。之前社区抓包逆向猜测的内容，现在都有官方对照版。

---

## 1. System Prompt 构建

**核心文件：** `src/utils/systemPrompt.ts`, `src/constants/systemPromptSections.ts`, `src/context.ts`

### 优先级层叠

System Prompt 不是一个静态字符串，而是通过 `buildEffectiveSystemPrompt()` 按优先级层叠组装：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1（最高） | Override System Prompt | 完全替换所有其他 prompt（如 Loop 模式） |
| 2 | Coordinator System Prompt | Coordinator 模式激活时使用 |
| 3 | Agent System Prompt | 来自 `mainThreadAgentDefinition` |
| 4 | Custom System Prompt | `--system-prompt` CLI 参数 |
| 5（最低） | Default System Prompt | 标准 Claude Code 提示词 |
| 追加 | AppendSystemPrompt | 始终追加（除 Override 模式外） |

**特殊规则：** 在 Proactive/KAIROS 模式下，Agent Prompt 是**追加**到 Default 后面，而不是替换。

### 上下文注入

| 函数 | 内容 | 缓存策略 |
|------|------|----------|
| `getSystemContext()` | Git 状态、缓存中断器 | 会话期间缓存 |
| `getUserContext()` | CLAUDE.md 文件、当前日期 | 动态发现 |

**Git 状态限制：** 截断到 2000 字符，长行附带截断警告。

### 缓存与注入

- System Prompt 各部分使用 memoization 缓存
- `/clear` 或 `/compact` 时清除缓存
- `setSystemPromptInjection()` 提供 ant-only 的临时调试注入点
- `DANGEROUS_uncachedSystemPromptSection()` 标记不走缓存的部分

### CLAUDE.md 发现

系统会自动向上遍历目录树，收集所有 `CLAUDE.md` 文件。禁用扫描可用 `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`。

---

## 2. 沙箱系统

**核心文件：** `src/utils/sandbox/sandbox-adapter.ts`

### 架构

沙箱包装了 `@anthropic-ai/sandbox-runtime`，添加了 Claude CLI 特定的集成：

```
Claude Code CLI
    ↓
sandbox-adapter.ts（适配层）
    ↓
@anthropic-ai/sandbox-runtime（底层沙箱运行时）
```

### 路径约定

沙箱配置中的路径遵循特殊规则：

| 前缀 | 含义 | 示例 |
|------|------|------|
| `//path` | 文件系统绝对路径 | `//Users/foo` → `/Users/foo` |
| `/path` | 相对于 settings 文件目录 | `/src` → `{settings_dir}/src` |
| `~/path` | Home 目录展开 | `~/projects` → `/Users/foo/projects` |
| `./path` 或 `path` | 相对路径（透传） | `./dist` |

### 文件系统控制

通过 `sandbox.filesystem.*` 配置：

| 配置项 | 作用 |
|--------|------|
| `allowWrite` | 允许写入的路径列表 |
| `denyWrite` | 禁止写入的路径列表 |
| `allowRead` | 允许读取的路径列表 |
| `denyRead` | 禁止读取的路径列表 |

### 网络策略

- `allowManagedDomainsOnly` — 仅允许管理域名列表中的网络访问
- 沙箱权限请求/响应通过 UDS Inbox 系统处理（`sandbox_permission_request/response`）

---

## 3. 权限系统

**核心文件：** `src/hooks/toolPermission/PermissionContext.ts`（161+ 行）

### 三层决策机制

```
工具调用请求
    ↓
① Hook 审批 → 允许/拒绝 → 结束
    ↓（无 Hook 或 Hook 未决定）
② Bash 分类器 → 安全命令自动通过 → 结束
    ↓（非 Bash 或无法判断）
③ 用户交互审批 → 允许/拒绝（可选持久化）
```

### 权限模式

| 模式 | 行为 |
|------|------|
| `default` | 标准模式，需要用户审批 |
| `plan` | Plan 模式，限制写操作 |
| `bypassPermissions` | 绕过所有权限检查 |
| `auto` | 自动模式，最大限度减少交互 |

### 审批来源

| 来源 | 触发方式 |
|------|----------|
| `hook` | 生命周期钩子自动审批（可持久化规则） |
| `user` | 用户手动确认（临时或永久） |
| `classifier` | Bash 分类器自动审批安全命令 |

### 拒绝来源

| 来源 | 含义 |
|------|------|
| `hook` | 钩子主动拒绝 |
| `user_abort` | 用户中断操作 |
| `user_reject` | 用户明确拒绝（可附带反馈） |

### Permission Context（冻结对象）

```typescript
const ctx = Object.freeze({
  // 工具元数据
  toolName, toolInput, toolContext,

  // 决策助手
  buildAllow(),
  buildDeny(),
  cancelAndAbort(),

  // Hook 执行
  runHooks(),        // 带建议的 Hook 执行

  // 分类器
  tryClassifier(),   // 仅 Bash 工具，feature-gated

  // 持久化
  persistPermissions(),  // 更新 AppState

  // 用户交互
  handleUserAllow(),
  handleHookAllow(),

  // 队列操作
  pushToQueue(),
  removeFromQueue(),
  updateQueueItem(),
})
```

### 权限队列

- 基于 React 状态的权限审批队列
- `PermissionQueueOps` 泛型队列接口（解耦 React）
- `createPermissionQueueOps()` React 状态适配器
- 映射到 `setToolUseConfirmQueue` React 状态

### 分析遥测

- `logPermissionDecision()` — 记录接受/拒绝，包含来源和耗时
- `sanitizeToolNameForAnalytics()` — 安全的工具名遥测

---

## 4. Tool Use 完整体系

**核心文件：** `src/Tool.ts`（30K 行）, `src/tools.ts`（18K 行）

### 40+ 工具注册表

所有工具在 `src/tools.ts` 中注册，使用条件导入和 feature flag 控制可见性：

#### 文件操作

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `FileReadTool` | 读取文件（支持图片、PDF、Notebook） | 低 |
| `FileWriteTool` | 创建/覆写文件 | 高 |
| `FileEditTool` | 部分文件修改（字符串替换） | 高 |
| `GlobTool` | 文件模式匹配搜索 | 低 |
| `GrepTool` | ripgrep 内容搜索 | 低 |

#### 执行环境

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `BashTool` | Shell 命令执行 | 需审批 |
| `PowerShellTool` | PowerShell（feature-gated） | 需审批 |
| `REPLTool` | 交互式 REPL（ant-only） | 需审批 |

#### 外部交互

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `WebFetchTool` | URL 内容获取 | 中 |
| `WebSearchTool` | Web 搜索 | 中 |
| `MCPTool` | MCP 服务器工具调用 | 可变 |
| `LSPTool` | Language Server Protocol | 低 |

#### Agent 协调

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `AgentTool` | 子 Agent 生成 | 中 |
| `SendMessageTool` | 跨 Agent 消息 | 中 |
| `TeamCreateTool` | 创建 Agent 团队 | 中 |
| `TeamDeleteTool` | 解散团队 | 中 |
| `TaskCreateTool` | 创建任务 | 低 |
| `TaskUpdateTool` | 更新任务 | 低 |
| `TaskStopTool` | 停止任务 | 中 |

#### 模式切换

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `EnterPlanModeTool` | 进入 Plan 模式 | 低 |
| `ExitPlanModeTool` | 退出 Plan 模式 | 低 |
| `EnterWorktreeTool` | 进入 Git Worktree 隔离 | 中 |
| `ExitWorktreeTool` | 退出 Worktree | 中 |

#### 其他

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `ToolSearchTool` | 延迟工具发现 | 低 |
| `AskUserQuestionTool` | 向用户提问 | 低 |
| `SyntheticOutputTool` | 结构化输出生成 | 低 |
| `CronCreateTool` | 定时触发器创建 | 中 |
| `RemoteTriggerTool` | 远程触发 | 中 |
| `SleepTool` | 主动模式等待 | 低 |
| `NotebookEditTool` | Jupyter Notebook 编辑 | 高 |

### 工具接口

每个工具实现包含：

```typescript
interface Tool {
  // 元数据
  name: string
  description: string
  inputSchema: ZodSchema        // Zod v4 输入校验

  // 权限
  permissionModel: PermissionModel
  isReadOnly: boolean

  // 执行
  execute(input: Input, context: ToolContext): Promise<ToolResult>

  // 进度
  progressState?: ProgressState
}
```

### 循环依赖处理

工具之间的循环引用通过惰性 getter 打破：

```typescript
// 避免 AgentTool → tools.ts → AgentTool 循环
function getTeamCreateTool() {
  return require('./tools/TeamCreateTool').default
}
```

---

## 5. Query 引擎

**核心文件：** `src/QueryEngine.ts`（47K 行）, `src/query.ts`（1800+ 行）

### 查询参数

```typescript
type QueryParams = {
  messages: Message[]
  systemPrompt: SystemPrompt
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  canUseTool: CanUseToolFn
  toolUseContext: ToolUseContext
  fallbackModel?: string
  querySource: QuerySource
  maxOutputTokensOverride?: number
  maxTurns?: number
  skipCacheWrite?: boolean
  taskBudget?: { total: number }
  deps?: QueryDeps
}
```

### 流式处理流水线

```
API 请求
    ↓
流式响应接收（SSE/WebSocket）
    ↓
Token 计数 & 成本追踪
    ↓
工具调用检测
    ↓
  ┌─────────────────┐
  │ 工具调用循环      │
  │  ↓               │
  │ 权限检查          │
  │  ↓               │
  │ 工具执行          │
  │  ↓               │
  │ 结果注入到消息流   │
  │  ↓               │
  │ 继续流式接收      │←──┐
  │  ↓               │   │
  │ 检测到新工具调用？ │───┘
  └─────────────────┘
    ↓
响应完成
    ↓
Thinking 模式处理
    ↓
Token 统计汇总
```

### 重试与恢复

- **Max Output Tokens 恢复：** `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`，截断输出时自动重试
- `isWithheldMaxOutputTokens()` — 保留错误直到确认无法恢复
- 回退模型：`fallbackModel` 参数支持主模型失败时切换

### 查询配置快照

```typescript
type QueryConfig = {
  sessionId: string
  streamingToolExecution: boolean    // 工具流式执行
  emitToolUseSummaries: boolean      // 工具使用摘要
  isAnt: boolean                     // Ant 环境标记
  fastModeEnabled: boolean           // 快速模式
}
```

配置在查询开始时快照，查询期间不变。

---

## 6. MCP 集成

**源码位置：** `src/services/mcp/`

### 传输层支持

| 传输方式 | 实现 |
|----------|------|
| Stdio | `StdioClientTransport` — 子进程通信 |
| SSE | `SSEClientTransport` — Server-Sent Events |
| StreamableHTTP | `StreamableHTTPClientTransport` — HTTP 流 |
| WebSocket | `WebSocketTransport` — 自定义实现 |

### 核心工具

| 工具 | 功能 |
|------|------|
| `MCPTool` | 通用 MCP 工具包装器 |
| `ListMcpResourcesTool` | 资源枚举 |
| `ReadMcpResourceTool` | 资源读取 |
| `McpAuthTool` | OAuth 认证流程 |

### 内容处理

- `mcpContentNeedsTruncation()` — 大小估算检查
- `truncateMcpContentIfNeeded()` — 智能截断 + 回退存储
- 大输出持久化为二进制 blob

### Feature Gates

- `MCP_SKILLS` — MCP 技能支持
- `MCP_RICH_OUTPUT` — 富文本输出
- `CHICAGO_MCP` — 含义不明的 MCP 变体
- 频道级服务器路由（多租户场景）

---

## 7. 状态管理

**源码位置：** `src/state/`

### 架构

```
AppState.tsx        → 主状态接口定义
AppStateStore.ts    → 状态存储实现
store.ts            → 存储工具函数
selectors.ts        → 状态选择器
onChangeAppState.ts → 变更监听器
```

### 关键状态

- 对话消息历史
- 工具权限队列（`toolUseConfirmQueue`）
- Agent 团队状态
- Inbox 消息队列
- 动态配置（设置变更监听）
- 会话后台状态

---

## 8. Token 估算与成本追踪

**核心文件：** `src/cost-tracker.ts`（11K 行）, `src/services/tokenEstimation.ts`

### 成本追踪

- 实时追踪输入/输出 Token 消耗
- 按模型定价计算美元成本
- `/cost` 命令查看当前会话消耗
- 支持成本阈值告警（`CostThreshold` 对话框）

### Token 估算

- 在发送 API 请求前估算 Token 数
- 用于判断是否需要 Context 压缩
- 配合 `TOKEN_BUDGET` feature flag 实现预算控制

---

## 9. 启动流水线

**源码位置：** `src/main.tsx`, `src/entrypoints/`

### 启动优化

```
进程启动
    ↓（并行）
├── MDM 设置预读
├── 钥匙串预取
├── API 预连接
└── GrowthBook 初始化
    ↓
Commander.js CLI 解析
    ↓
React/Ink 渲染器初始化
    ↓
Tool 注册 & Feature Flag 评估
    ↓
System Prompt 构建
    ↓
就绪
```

### 性能分析

`src/utils/startupProfiler.ts` 和 `src/utils/queryProfiler.ts` 提供启动和查询的性能剖析：

```javascript
return ` ⚠️  VERY SLOW`     // > 阈值
return ` ⚠️  SLOW`          // > 中等阈值
return ' ⚠️  git status'    // git 状态获取慢
return ' ⚠️  tool schemas'  // 工具 Schema 加载慢
return ' ⚠️  client creation' // 客户端创建慢
```

---

## 10. 对社区逆向的验证

| 社区猜测 | 源码验证 |
|----------|----------|
| "System Prompt 很长" | 确认：多层叠加，包含上下文、CLAUDE.md、工具文档 |
| "用 ripgrep 做代码搜索" | 确认：`GrepTool` 直接调用 ripgrep |
| "有某种权限系统" | 确认：三层决策（Hook → 分类器 → 用户） |
| "支持 MCP" | 确认：完整的 4 种传输层 + 认证 + 截断处理 |
| "有上下文压缩" | 确认：5 种压缩策略 + 9 部分结构化摘要 |
| "用 React 渲染终端" | 确认：React + Ink，完整组件树 |
| "有 feature flag" | 确认：但数量远超预期（80+ vs 猜测的十几个） |
| "Tool Use 循环" | 确认：QueryEngine 中的完整工具调用循环 |
| "有沙箱" | 确认：适配层 + 路径约定 + 文件系统/网络策略 |

---

## 关键文件索引

| 文件 | 行数 | 内容 |
|------|------|------|
| `src/QueryEngine.ts` | ~47K | 核心查询引擎 |
| `src/Tool.ts` | ~30K | 工具类型定义 |
| `src/commands.ts` | ~26K | 命令注册表 |
| `src/tools.ts` | ~18K | 工具注册表 |
| `src/query.ts` | ~1.8K | 查询编排 |
| `src/context.ts` | ~6.5K | 上下文收集 |
| `src/utils/systemPrompt.ts` | - | System Prompt 构建 |
| `src/utils/sandbox/sandbox-adapter.ts` | - | 沙箱适配层 |
| `src/hooks/toolPermission/PermissionContext.ts` | ~161 | 权限决策核心 |
| `src/services/compact/compact.ts` | ~1.8K | Context 压缩 |
| `src/services/mcp/client.ts` | - | MCP 客户端 |
| `src/cost-tracker.ts` | ~11K | 成本追踪 |
