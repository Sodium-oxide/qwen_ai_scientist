# Topic 10: USER_TYPE=ant — 员工专属功能全解析

## TLDR

源码中有 **95+ 处** `process.env.USER_TYPE === 'ant'` 编译时门控，覆盖系统提示词、工具注册、命令系统、权限分类器、MCP 集成、API 行为、分析遥测等**所有核心子系统**。最本质的差异不是多了几个命令，而是**系统提示词完全不同**——ant 用户的 Claude 被要求"默认不写注释""主动指出误解""测试失败就不能声称成功"，而外部用户只得到"回复简洁"。所有 ant-only 代码通过 Bun `--define` 在编译时被**物理消除**——外部二进制中不存在一个字节。

从第一性原理看，员工模式揭示了一个核心事实：**Anthropic 的工程师用 Claude Code 开发 Claude Code 本身**。这套系统不是"给员工开后门"，而是一个自举（bootstrapping）团队为自己打造的极致 dogfooding 基础设施。

---

## 一、编译时消除——不是 if/else，是根本不存在

```typescript
// Bun bundler 在构建时将 process.env.USER_TYPE 替换为字符串常量
const REPLTool = process.env.USER_TYPE === 'ant'
  ? require('./tools/REPLTool/REPLTool.js').REPLTool
  : null
// 外部构建：'external' === 'ant' → false
// 整个 require('./tools/REPLTool/...') 被 tree-shake
// REPLTool.ts 甚至不存在于最终 bundle 中
```

这不是运行时检查——外部用户的二进制文件中没有任何 ant-only 代码的字节。

---

## 二、系统提示词——最大也最深的差异

**源码位置：** `src/constants/prompts.ts`

系统提示词是 ant 和外部用户体验差异的**根源**。以下是逐条对比。

### 2.1 注释规范（ant 独有，外部不可见）

```
Default to writing no comments. Only add one when the WHY is non-obvious: a
hidden constraint, a subtle invariant, a workaround for a specific bug, behavior
that would surprise a reader. If removing the comment wouldn't confuse a future
reader, don't write it.

Don't explain WHAT the code does, since well-named identifiers already do that.
Don't reference the current task, fix, or callers ("used by X", "added for the Y
flow", "handles the case from issue #123"), since those belong in the PR
description and rot as the codebase evolves.

Don't remove existing comments unless you're removing the code they describe or
you know they're wrong. A comment that looks pointless to you may encode a
constraint or a lesson from a past bug that isn't visible in the current diff.

Before reporting a task complete, verify it actually works: run the test, execute
the script, check the output. Minimum complexity means no gold-plating, not
skipping the finish line. If you can't verify (no test exists, can't run the
code), say so explicitly rather than claiming success.
```

**外部用户：** 无此段。

**解读：** Anthropic 内部推行"好代码不需要注释"的工程哲学。最后一条"完成前必须验证"直接对抗模型的"声称完成但没验证"倾向。

### 2.2 协作判断力（ant 独有）

```
If you notice the user's request is based on a misconception, or spot a bug
adjacent to what they asked about, say so. You're a collaborator, not just an
executor — users benefit from your judgment, not just your compliance.
```

**外部用户：** 无此段。

**解读：** ant 用户的 Claude 被授权**挑战指令**——这反映了 Anthropic 内部把 Claude 视为"同事"而非"工具"的定位。

### 2.3 结果忠实性——最关键的 ant-only 指令

```
Report outcomes faithfully: if tests fail, say so with the relevant output; if
you did not run a verification step, say that rather than implying it succeeded.
Never claim "all tests pass" when output shows failures, never suppress or
simplify failing checks (tests, lints, type errors) to manufacture a green
result, and never characterize incomplete or broken work as done.

Equally, when a check did pass or a task is complete, state it plainly — do not
hedge confirmed results with unnecessary disclaimers, downgrade finished work to
"partial," or re-verify things you already checked. The goal is an accurate
report, not a defensive one.
```

**外部用户：** 无此段。

**解读：** 这段对抗模型的**两种**倾向——谎称成功（sycophancy）和过度谨慎（defensive hedging）。Anthropic 内部测量到 ant 用户的 False Claim 率（29-30%）**高于**外部（16.7%），可能因为内部任务更复杂。

### 2.4 内部 Bug 上报（ant 独有）

```
If the user reports a bug, slowness, or unexpected behavior with Claude Code
itself (as opposed to asking you to fix their own code), recommend the
appropriate slash command: /issue for model-related problems (odd outputs, wrong
tool choices, hallucinations, refusals), or /share to upload the full session
transcript for product bugs, crashes, slowness, or general issues. Only
recommend these when the user is describing a problem with Claude Code. After
/share produces a ccshare link, if you have a Slack MCP tool available, offer to
post the link to #claude-code-feedback (channel ID C07VBSHV7EV) for the user.
```

**外部用户：** 无此段。

**解读：** 直接把 Slack 频道 ID 硬编码进系统提示词。反馈闭环极短：遇到问题 → 终端内 `/share` → 自动发 Slack。

### 2.5 沟通风格——两套完全不同的指导

**Ant 用户（~400 字的详细指导）：**

```
# Communicating with the user
When sending user-facing text, you're writing for a person, not logging to a
console. Assume users can't see most tool calls or thinking — only your text
output. Before your first tool call, briefly state what you're about to do.
While working, give short updates at key moments: when you find something
load-bearing (a bug, a root cause), when changing direction, when you've made
progress without an update.

When making updates, assume the person has stepped away and lost the thread.
They don't know codenames, abbreviations, or shorthand you created along the
way, and didn't track your process. Write so they can pick back up cold: use
complete, grammatically correct sentences without unexplained jargon. Expand
technical terms. Err on the side of more explanation. Attend to cues about the
user's level of expertise; if they seem like an expert, tilt a bit more concise,
while if they seem like they're new, be more explanatory.

Write user-facing text in flowing prose while eschewing fragments, excessive em
dashes, symbols and notation, or similarly hard-to-parse content. Only use
tables when appropriate; for example to hold short enumerable facts (file names,
line numbers, pass/fail), or communicate quantitative data. Don't pack
explanatory reasoning into table cells — explain before or after. Avoid semantic
backtracking: structure each sentence so a person can read it linearly, building
up meaning without having to re-parse what came before.

What's most important is the reader understanding your output without mental
overhead or follow-ups, not how terse you are. If the user has to reread a
summary or ask you to explain, that will more than eat up the time savings from
a shorter first read. Match responses to the task: a simple question gets a
direct answer in prose, not headers and numbered sections. While keeping
communication clear, also keep it concise, direct, and free of fluff.
```

**外部用户（~100 字的精简指导）：**

```
# Output efficiency

IMPORTANT: Go straight to the point. Try the simplest approach first without
going in circles. Do not overdo it. Be extra concise.

Keep your text output brief and direct. Lead with the answer or action, not the
reasoning. Skip filler words, preamble, and unnecessary transitions. Do not
restate what the user said — just do it. When explaining, include only what is
necessary for the user to understand.

Focus text output on:
- Decisions that need the user's input
- High-level status updates at natural milestones
- Errors or blockers that change the plan

If you can say it in one sentence, don't use three.
```

**解读：** ant 版强调"为人类写作"，外部版强调"极致简洁"。Anthropic 内部员工享受的是更丰富、更有温度的沟通；外部用户要求"直奔主题"。

### 2.6 额外的 ant-only 简洁约束

**外部用户独有（ant 不可见）：**
```
Your responses should be short and concise.
```

**ant 用户独有——数值长度锚：**
```
Length limits: keep text between tool calls to ≤25 words. Keep final responses
to ≤100 words unless the task requires more detail.
```

**解读：** 看似矛盾——ant 有更详细的沟通指导，但也有精确到字数的约束。实际上不矛盾：ant 版要求"写得好"（400 字指导怎么写），同时"写得短"（≤25 字/工具调用间，≤100 字/最终回复）。外部版只要求"写得短"。

### 2.7 模型身份与 Undercover 模式

**Undercover ON（ant 在公开仓库）：**
- 移除模型描述（`You are powered by the model named...`）
- 移除 Claude 模型家族信息（`The most recent Claude model family is...`）
- 移除 Claude Code 可用性信息（`Claude Code is available as...`）
- 移除 fast mode 信息
- 隐藏知识截止日期

**Undercover OFF 或外部用户：**
- 显示完整模型身份和家族信息

### 2.8 动态模型覆盖后缀（ant 独有）

```typescript
// src/constants/prompts.ts:136-140
// GrowthBook feature: tengu_ant_model_override
const suffix = getAntModelOverrideConfig()?.defaultSystemPromptSuffix
```

ant 用户的系统提示词可以通过 GrowthBook 远程注入额外后缀——实现 A/B 测试不同的指令组合。

### 2.9 Undercover 提交指令（ant 独有）

当 Undercover 模式激活时，`/commit` 和 `/commit-push-pr` 命令注入额外指令：

```
## UNDERCOVER MODE — CRITICAL

You are operating UNDERCOVER in a PUBLIC/OPEN-SOURCE repository. Your commit
messages, PR titles, and PR bodies MUST NOT contain ANY Anthropic-internal
information. Do not blow your cover.

NEVER include in commit messages or PR descriptions:
- Internal model codenames (animal names like Capybara, Tengu, etc.)
- Unreleased model version numbers (e.g., opus-4-7, sonnet-4-8)
- Internal repo or project names (e.g., claude-cli-internal, anthropics/…)
- Internal tooling, Slack channels, or short links (e.g., go/cc, #claude-code-…)
- The phrase "Claude Code" or any mention that you are an AI
- Any hint of what model or version you are
- Co-Authored-By lines or any other attribution

Write commit messages as a human developer would — describe only what the code
change does.

GOOD: "Fix race condition in file watcher initialization"
BAD:  "Fix bug found while testing with Claude Capybara"
```

**关键：没有"关闭"开关。** 只有强制开启（`CLAUDE_CODE_UNDERCOVER=1`）和自动检测。不允许意外泄露。

---

## 三、工具系统差异

**源码位置：** `src/tools.ts`

### 3.1 专属工具（4 个）

| 工具 | 注册方式 | 功能 |
|------|----------|------|
| **REPLTool** | `tools.ts:16-19` 条件 require | 沙箱化虚拟机执行环境 |
| **SuggestBackgroundPRTool** | `tools.ts:20-24` 条件 require | 后台自动建议 PR |
| **ConfigTool** | `tools.ts:214` 三元表达式 | 运行时配置管理 |
| **TungstenTool** | `tools.ts:215` 三元表达式 | 用途不明（内部代号 Tungsten） |

**REPLTool 深度剖析：**

REPLTool 不是简单的"交互式终端"，而是一个完整的执行环境替代层：

- ant CLI 会话**默认启用**（`CLAUDE_CODE_REPL=0` 可关闭）
- SDK 入口点（sdk-ts, sdk-py, sdk-cli）**不默认启用**
- 启用后**隐藏**原生 Bash、Read、Edit、Glob、Grep、Notebook、Agent 工具
- 所有操作路由到 REPL 虚拟机上下文
- 强制批量操作（而非顺序直接工具调用）

**含义：** Anthropic 内部员工的工具执行路径和外部用户**物理上不同**。

### 3.2 Agent 嵌套限制

```typescript
// src/constants/tools.ts:41
// ALL_AGENT_DISALLOWED_TOOLS 排除列表
...(process.env.USER_TYPE === 'ant' ? [] : [AGENT_TOOL_NAME]),
```

| 能力 | ant | 外部 |
|------|-----|------|
| Agent 调用 Agent（递归嵌套） | 允许 | **禁止** |
| Agent 隔离模式 | `worktree` + `remote` | 仅 `worktree` |

### 3.3 搜索工具实现差异

```typescript
// src/tools.ts:198-201
// Ant 构建使用 bun 内嵌的快速 bfs/ugrep
// 外部构建使用标准 GlobTool/GrepTool
```

ant 构建中 GlobTool 和 GrepTool 被替换为 Bun 二进制内嵌的高速实现。

---

## 四、命令系统差异

**源码位置：** `src/commands.ts`

### 4.1 激活逻辑

```typescript
// src/commands.ts:343-345
...(process.env.USER_TYPE === 'ant' && !process.env.IS_DEMO
  ? INTERNAL_ONLY_COMMANDS
  : []),
```

**双重门控：** 需要 `ant` 身份 **且** 非 Demo 模式。Demo 模式（`IS_DEMO=1`）下即使是 ant 也无法访问内部命令。

### 4.2 完整内部命令清单（27+ 个）

#### 调试与诊断

| 命令 | 实现状态 | 功能 |
|------|----------|------|
| `/debug-tool-call` | stub | 工具调用逐步调试 |
| `/break-cache` | stub | 系统提示词缓存强制中断 |
| `/env` | stub | 环境变量全量审查 |
| `/ctx_viz` | stub | Context 组装过程可视化 |
| `/ant-trace` | stub | 内部行为追踪 |
| `/perf-issue` | stub | 性能问题自动诊断收集 |
| `/version` | 已实现 | 构建版本号和时间戳（`MACRO.VERSION`, `MACRO.BUILD_TIME`） |

#### Bug 报告与质量

| 命令 | 实现状态 | 功能 |
|------|----------|------|
| `/issue` | stub | 模型问题上报（附最近 5 个 API 请求完整负载） |
| `/share` | stub | 会话记录上传 → 生成 ccshare 链接 → 可发 Slack |
| `/good-claude` | stub | 标记好的/差的模型回答 |
| `/bughunter` | stub | Bug 猎手模式（可并行 5-20 个 Agent 搜索 bug） |
| `/backfill-sessions` | stub | 会话数据回填修复 |

#### Git & PR 增强

| 命令 | 实现状态 | 功能 |
|------|----------|------|
| `/commit` | 已实现 | 增强版 Git 提交（Undercover 集成、归属控制） |
| `/commit-push-pr` | 已实现 | 一键 commit→push→PR（含 Slack 通知、reviewer 分配） |
| `/autofix-pr` | stub | AI 驱动的 PR 自动修复 |
| `/subscribe-pr` | feature-gated | GitHub Webhook 订阅（需 `KAIROS_GITHUB_WEBHOOKS`） |

#### 基础设施测试

| 命令 | 实现状态 | 功能 |
|------|----------|------|
| `/mock-limits` | stub | 21 种限速场景模拟 |
| `/reset-limits` | stub | 重置限速 Mock |
| `/bridge-kick` | 已实现 | Bridge 故障注入（6 种故障类型） |
| `/teleport` | stub | 跨本地/远程会话迁移 |
| `/oauth-refresh` | stub | OAuth Token 手动刷新 |

#### 其他

| 命令 | 实现状态 | 功能 |
|------|----------|------|
| `/onboarding` | stub | 内部入职流程 |
| `/agents-platform` | 条件加载 | Agent 平台后端管理 |
| `/ultraplan` | feature-gated | 超级规划模式（需 `ULTRAPLAN`） |
| `/summary` | stub | 会话摘要生成 |
| `/init-verifiers` | 已实现 | 测试验证器初始化（5 阶段工作流） |
| `/force-snip` | feature-gated | 历史剪辑（需 `HISTORY_SNIP`） |

### 4.3 `/commit` 命令的 ant 差异

**Undercover 模式集成：**
- 开启时：剥离所有 `Co-Authored-By` 归属标记
- 提交信息中注入 UNDERCOVER 安全指令
- 不允许泄露 AI 参与的任何痕迹

**标准模式：**
- 正常附带 `Co-Authored-By: {modelName} <noreply@anthropic.com>`
- 未知公开模型回退到 "Claude Opus 4.6"（防止代号泄露）

### 4.4 `/commit-push-pr` 命令的 ant 差异

- Undercover ON：移除 reviewer 分配和 changelog 部分
- 支持通过 ToolSearch 发现 Slack 工具并自动发送 PR 链接
- 允许的工具集比标准 `/commit` 更丰富：含 `gh pr create/edit/view/merge`

---

## 五、权限与 Hook 系统差异

### 5.1 权限分类器——完全不同的 Prompt

**源码位置：** `src/utils/permissions/yoloClassifier.ts`

```typescript
const EXTERNAL_PERMISSIONS_TEMPLATE = feature('TRANSCRIPT_CLASSIFIER')
  ? txtRequire(require('./yolo-classifier-prompts/permissions_external.txt'))
  : ''

const ANTHROPIC_PERMISSIONS_TEMPLATE =
  feature('TRANSCRIPT_CLASSIFIER') && process.env.USER_TYPE === 'ant'
    ? txtRequire(require('./yolo-classifier-prompts/permissions_anthropic.txt'))
    : ''
```

ant 和外部用户使用**完全不同的权限分类 Prompt 文件**：
- `permissions_anthropic.txt` — ant 专用的分类规则
- `permissions_external.txt` — 外部用户的分类规则

**选择逻辑：**
```typescript
function isUsingExternalPermissions(): boolean {
  if (process.env.USER_TYPE !== 'ant') return true
  const config = getFeatureValue_CACHED_MAY_BE_STALE('tengu_auto_mode_config', {})
  return config?.forceExternalPermissions === true
}
```

ant 用户默认用内部模板，但可通过 GrowthBook 特性 `tengu_auto_mode_config.forceExternalPermissions` 强制切换到外部模板。

### 5.2 权限模式差异

| 模式 | ant | 外部 |
|------|-----|------|
| `default` | 有 | 有 |
| `plan` | 有 | 有 |
| `bypassPermissions` | 有 | 有 |
| `auto` | 有（需 `TRANSCRIPT_CLASSIFIER`） | **无** |
| `bubble` | 有 | **无** |

### 5.3 Tmux 工具危险标记（ant 独有）

```typescript
// src/hooks/toolPermission/permissionSetup.ts
if (process.env.USER_TYPE === 'ant') {
  // Tmux send-keys 可执行任意 shell，跟 Bash(*) 一样危险
  if (toolName === 'Tmux') return true
}
```

### 5.4 权限错误消息附带 Slack 频道

```typescript
return process.env.USER_TYPE === 'ant'
  ? `${base} · #claude-code-feedback`
  : base
```

### 5.5 Bash 安全环境变量差异

`src/tools/BashTool/bashPermissions.ts` 中 ant 用户有额外的安全环境变量白名单（多处门控），允许访问更多内部工具链相关的环境变量。

---

## 六、API 与 Query 引擎差异

### 6.1 查询配置

```typescript
// src/query/config.ts:39
isAnt: process.env.USER_TYPE === 'ant'
```

`config.gates.isAnt` 贯穿整个查询生命周期。

### 6.2 Prompt 缓存时长

```typescript
// src/services/api/claude.ts:408-413
userEligible = process.env.USER_TYPE === 'ant' ||
  (isClaudeAISubscriber() && !currentLimits.isUsingOverage)
```

- **ant：** 自动享有 1 小时 Prompt 缓存
- **外部：** 需要 Claude AI 订阅且未超额使用

### 6.3 Effort 数值覆盖

```typescript
// src/services/api/claude.ts:457-466
if (process.env.USER_TYPE === 'ant') {
  const existingInternal = (extraBodyParams.anthropic_internal as Record<string, unknown>) || {}
  extraBodyParams.anthropic_internal = {
    ...existingInternal,
    effort_override: effortValue,   // 数值型 effort
  }
}
```

- **ant：** 可以通过 `anthropic_internal.effort_override` 传递精确数值
- **外部：** 只能使用字符串级别的 effort（low/medium/high）

### 6.4 Research 字段捕获

```typescript
// src/services/api/claude.ts:2205-2206
...(process.env.USER_TYPE === 'ant' &&
  research !== undefined && { research })
```

- **ant：** 从 `message_start`、`content_block_delta`、`message_delta` 中捕获 `research` 字段并附加到助手消息
- **外部：** `research` 字段被忽略

### 6.5 Thinking 签名块处理

```typescript
// src/query.ts:927-929
if (process.env.USER_TYPE === 'ant') {
  messagesForQuery = stripSignatureBlocks(messagesForQuery)
}
```

- **ant：** 模型回退时剥离 protected-thinking 签名块（thinking 签名绑定模型，跨模型回放会失败）
- **外部：** 无此处理

### 6.6 Dump Prompts 调试

```typescript
// src/query.ts:588-590
const dumpPromptsFetch = config.gates.isAnt
  ? createDumpPromptsFetch(toolUseContext.agentId ?? config.sessionId)
  : undefined
```

- **ant：** 保存到 `~/.claude/dump-prompts/{sessionId}.jsonl`，供 `/issue` 和 `/share` 使用
- **外部：** 无此功能

### 6.7 内部 Beta Header

```typescript
// src/constants/betas.ts:30
export const CLI_INTERNAL_BETA_HEADER =
  process.env.USER_TYPE === 'ant' ? 'cli-internal-2026-02-09' : ''
```

ant 的所有 API 请求携带此 Header，可能在服务端解锁内部实验特性。

### 6.8 错误处理差异

**工具并发错误：**
- **ant：** 包含 `/share` 指引和反馈频道
- **外部：** 简化的错误消息

**无效模型名：**
- **ant：** 提示设置 `ANTHROPIC_MODEL` 或分享 orgId 到反馈频道
- **外部：** 标准错误

### 6.9 重试策略差异

```typescript
// src/services/api/withRetry.ts:748-750
if (!(process.env.USER_TYPE === 'ant' && is5xxError)) {
  return false  // 不重试
}
```

- **ant：** 5xx 错误时**忽略** `x-should-retry: false` Header，强制重试
- **外部：** 尊重 `x-should-retry` Header

### 6.10 Mock 限速注入点

```typescript
// src/services/api/withRetry.ts:202-210
if (process.env.USER_TYPE === 'ant') {
  const mockError = checkMockRateLimitError(retryContext.model, wasFastModeActive)
  if (mockError) throw mockError
}
```

在重试逻辑中嵌入 Mock 限速检查点，支持 `/mock-limits` 命令。

---

## 七、MCP 集成差异

### 7.1 VSCode SDK 文件通知

```typescript
// src/services/mcp/vscodeSdkMcp.ts:44-59
if (process.env.USER_TYPE !== 'ant' || !vscodeMcpClient) return
void vscodeMcpClient.client.notification({
  method: 'file_updated',
  params: { filePath, oldContent, newContent },
})
```

- **ant：** 文件变更时向 VSCode MCP Server 发送双向通知
- **外部：** 无通知

### 7.2 MCP 分析遥测

```typescript
// src/services/mcp/useManageMCPConnections.ts:989-1007
if (process.env.USER_TYPE === 'ant' && ...) {
  stdioCommands.push(basename(serverConfig.command))
}
logEvent('tengu_mcp_servers', {
  ...counts,
  ...(process.env.USER_TYPE === 'ant' && stdioCommands.length > 0
    ? { stdio_commands: stdioCommands.sort().join(',') }
    : {}),
})
```

- **ant：** MCP 遥测包含 stdio 命令名称列表
- **外部：** 仅基础计数

---

## 八、分析与遥测差异

### 8.1 采样率

| 维度 | ant | 外部 |
|------|-----|------|
| 启动性能采样 | **100%** | 0.5% |
| Debug 日志 | **默认开启** | 需 `--debug` 标志 |
| 1P 事件日志 | **控制台输出** | 静默 |
| GrowthBook 实验分配 | **记录日志** | 静默 |
| Thinking 输出 | **捕获到日志** | 剥离 |

### 8.2 Datadog 模型名

```typescript
// src/services/analytics/datadog.ts:205-208
if (process.env.USER_TYPE !== 'ant' && typeof allData.model === 'string') {
  allData.model = getCanonicalName(allData.model...)
}
```

- **ant：** 原始模型名（含内部代号）
- **外部：** 脱敏为成本类别（降低基数）

### 8.3 GrowthBook 特性覆盖

```typescript
// src/services/analytics/growthbook.ts:170-192
if (process.env.USER_TYPE === 'ant') {
  const raw = process.env.CLAUDE_INTERNAL_FC_OVERRIDES
  // 解析 JSON 覆盖，用于 A/B 测试
}
```

- **ant：** 支持 `CLAUDE_INTERNAL_FC_OVERRIDES` 环境变量手动覆盖特性值
- **ant：** `/config` Gates 页面运行时覆盖 feature flag
- **ant：** 自定义 GrowthBook Base URL（`CLAUDE_CODE_GB_BASE_URL`）
- **外部：** 无覆盖能力

### 8.4 Session Memory 门控日志

```typescript
// src/services/SessionMemory/sessionMemory.ts:286
if (process.env.USER_TYPE === 'ant' && !hasLoggedGateFailure) {
  hasLoggedGateFailure = true
  logEvent('tengu_session_memory_gate_disabled', {})
}
```

- **ant：** Session Memory 门控失败时上报诊断事件
- **外部：** 静默跳过

---

## 九、其他子系统差异

### 9.1 VCR — API 录制回放

```typescript
// src/services/vcr.ts:28
if (process.env.USER_TYPE === 'ant' && isEnvTruthy(process.env.FORCE_VCR)) return true
```

ant 用户可通过 `FORCE_VCR=1` 强制录制 API 交互，用于确定性回归测试。路径脱水（dehydration）让 fixtures 跨机器稳定：

```typescript
function dehydrateValue(s: unknown): unknown {
  // 替换动态值：num_files, duration_ms, cost_usd, CWD, CONFIG_HOME
}
```

### 9.2 Mock 限速系统（883 行）

21 种模拟场景覆盖所有计费边界：session 限额、周限额、超额使用、组织上限、席位层级、Opus/Sonnet 独立限额。每个函数入口检查 `USER_TYPE !== 'ant'` 并提前返回。

### 9.3 Bridge 故障注入

```typescript
// src/bridge/bridgeDebug.ts
// 可注入故障类型：pollForWork, heartbeatWork, registerBridgeEnvironment, reconnectSession
// 故障等级：fatal (403/404/5xx) 或 transient (网络抖动)
```

源码注释引用真实 BigQuery 数据：
```
// BQ 数据（2026-03-12，7 天窗口）：
// - poll 404/not_found_error: 147K 次/周
// - ws_closed 1002/1006: 22K 次/周
```

### 9.4 内部 OAuth 基础设施

| 环境 | 域名 | Client ID |
|------|------|-----------|
| 生产 | `console.anthropic.com` | 正式 ID |
| Staging | `*.staging.ant.dev` | `22422756-60c9-4084-8eb7-27705fd5cf9a` |
| 本地 | `localhost:8000/4000/3000` | 开发 ID |
| MCP Proxy | 生产/staging/本地 各一套 | — |

### 9.5 归属标记控制

| 场景 | Co-Authored-By | PR 描述归属 |
|------|----------------|-------------|
| ant + 内部仓库 | `Claude {内部代号}` | 正常 |
| ant + 公开仓库（Undercover） | **空** | **空** |
| ant + 远程会话 | 会话 URL | 会话 URL |
| 外部用户 | `Claude Opus 4.6` | 正常 |

### 9.6 模型配置覆盖

```typescript
// src/utils/model/antModels.ts
// GrowthBook feature: tengu_ant_model_override
interface AntModelOverrideConfig {
  defaultModel?: string
  defaultModelEffortLevel?: string
  defaultSystemPromptSuffix?: string
  antModels?: AntModel[]
  switchCallout?: string
}
```

ant 用户可通过 GrowthBook 远程配置默认模型、effort level、自定义系统提示词后缀。

### 9.7 内部模型名解析

```typescript
// src/utils/thinking.ts:95-98
resolveAntModel()  // 解析内部模型代号到实际 API model ID
```

### 9.8 邮箱回退链

| 优先级 | 来源 | ant | 外部 |
|--------|------|-----|------|
| 1 | OAuth 邮箱 | 有 | 有 |
| 2 | `COO_CREATOR` 环境变量 → `@anthropic.com` | 有 | 无 |
| 3 | `git config user.email` 子进程调用 | 有 | 无 |

### 9.9 Bootstrap 状态差异

```typescript
// src/bootstrap/state.ts:391-395
...(process.env.USER_TYPE === 'ant' ? { replBridgeActive: false } : {})
```

ant 构建初始化额外的 REPL Bridge 状态。

---

## 十、第一性原理分析

### 10.1 为什么需要员工模式？

**根本原因：Claude Code 是 Anthropic 自己的开发工具。**

员工模式的设计揭示了三层意图：

```
第一层：安全防护
  ├── Undercover 模式——防止代号泄露（没有关闭开关）
  ├── 内部仓库白名单——25+ 仓库精确区分
  ├── 归属标记动态控制——按场景决定是否暴露 AI 参与
  └── 模型身份隐藏——公开仓库中 Claude 不知道自己是谁

第二层：开发效率
  ├── 30+ 内部命令——覆盖 debug/测试/部署全链路
  ├── REPLTool——更可控的沙箱执行环境
  ├── VCR 录制回放——确定性回归测试
  ├── Mock 限速——21 种场景无风险测试
  ├── Bridge 故障注入——对标真实生产数据
  └── GrowthBook 覆盖——运行时 A/B 测试 feature flag

第三层：产品质量
  ├── /issue + /share → Slack 闭环——摩擦力最低的反馈路径
  ├── API 请求缓存——bug 报告自动附带精确上下文
  ├── 100% 性能采样——全量数据驱动优化
  ├── 忠实报告提示词——对抗 sycophancy 和 defensive hedging
  └── 不同权限分类 Prompt——内部更严格的安全基线
```

### 10.2 猜测：Anthropic 内部员工的典型作业模式

#### 日常开发循环

```
启动 Claude Code
    ↓ 自动检测 USER_TYPE=ant
    ↓ 加载 REPLTool、ConfigTool
    ↓ 检测仓库 → 白名单？
    ├── anthropics/claude-code → Undercover OFF，正常使用
    └── 开源项目 → Undercover ON，隐身作业
    ↓
工作：增强版系统提示词（主动指出问题、忠实报告、好的沟通）
    ↓
遇到模型 bug → /issue → 自动收集 5 个 API 请求 → 提交
    ↓
需要分享 → /share → ccshare 链接 → Claude 自动发到 #claude-code-feedback
    ↓
回复同事："看我刚分享的 session"
```

#### 限速/计费功能开发

```
/mock-limits opus-limit → 验证 Opus 限额 UI
/mock-limits overage-exhausted → 验证超额降级逻辑
/mock-limits org-spend-cap-hit → 验证组织上限提示
/reset-limits → 清除所有 Mock
```

21 种场景说明 Anthropic 的计费系统极其复杂——每种都需要独立的 UI 和逻辑。

#### Bridge 稳定性测试

```
/bridge-kick poll 404 → 模拟轮询失败（每周 147K 次真实发生）
/bridge-kick close 1006 → 模拟 WebSocket 异常关闭（每周 22K 次）
/bridge-kick status → 检查恢复状态
```

这些不是假设场景——是从 BigQuery 生产数据中提取的真实故障模式驱动的测试工具。

#### 在公开仓库贡献

```
Undercover 自动激活
→ Claude 不知道自己是什么模型
→ 提交无归属标记
→ PR 描述无 AI 痕迹
→ 外部观察者无法判断是否使用了 AI
```

#### 性能优化

```
启动性能 100% 采样 → Statsig 全量上报
CLAUDE_CODE_PROFILE_QUERY=1 → 查询性能分析
/ant-trace → 内部追踪
→ 定位瓶颈 → 优化 → 验证
```

### 10.3 从员工模式看 Anthropic 工程文化

**极致 Dogfooding：** 95+ 处门控不是偶然，每个内部命令都代表一个真实痛点被工具化。

**安全意识渗透工具层：** Undercover 没有关闭开关；默认安全；模型代号被视为需要保护的信息。

**测试驱动基础设施：** 883 行 Mock 限速 + VCR 录制 + Bridge 故障注入 = 测试基础设施和产品代码同等重要。

**摩擦力最低的反馈闭环：** 终端内 `/share` → 自动 Slack → 零窗口切换。

**对 False Claim 的高度警觉：** ant 用户 FC 率（29-30%）高于外部（16.7%），但选择用提示词工程缓解而非降低任务难度。

**内部代号线索：**
- **Tengu（天狗）** — GrowthBook 特性前缀（`tengu_*`），可能是项目或模型代号
- **Capybara（水豚）** — 内部仓库白名单 + BUDDY 物种列表
- **Tungsten（钨）** — 神秘工具名，功能完全不明

---

## 十一、差异全景总结表

| 子系统 | 差异维度 | ant | 外部 |
|--------|----------|-----|------|
| **系统提示词** | 注释规范 | "默认不写注释" | 无 |
| | 协作判断力 | "主动指出误解" | 无 |
| | 结果忠实性 | "测试失败就不能声称成功" | 无 |
| | Bug 上报 | `/issue` + `/share` + Slack | 无 |
| | 沟通风格 | 400 字详细指导 | 100 字精简指导 |
| | 长度锚 | ≤25 字/工具间，≤100 字/回复 | "简洁" |
| | 模型身份 | Undercover 时隐藏 | 始终显示 |
| | 动态后缀 | GrowthBook 远程注入 | 无 |
| **工具** | 专属工具 | 4 个（REPL、Config、SuggestBGPR、Tungsten） | 无 |
| | Agent 嵌套 | 允许 | 禁止 |
| | Agent 隔离 | worktree + remote | 仅 worktree |
| | 搜索实现 | Bun 内嵌快速版 | 标准版 |
| **命令** | 内部命令 | 27+ 个 | 0 |
| | Demo 门控 | `IS_DEMO` 可禁用内部命令 | 不适用 |
| **权限** | 分类 Prompt | `permissions_anthropic.txt` | `permissions_external.txt` |
| | 权限模式 | +auto, +bubble | 仅 default/plan/bypass |
| | Tmux 危险标记 | 有 | 无 |
| **API** | Prompt 缓存 | 自动 1h | 需订阅且无超额 |
| | Effort 覆盖 | 数值型（`anthropic_internal`） | 字符串型 |
| | Research 字段 | 捕获 | 忽略 |
| | Beta Header | `cli-internal-2026-02-09` | 空 |
| | 5xx 重试 | 忽略 `x-should-retry: false` | 尊重 |
| | Thinking 签名 | 回退时剥离 | 无处理 |
| **MCP** | VSCode 通知 | 文件变更双向通知 | 无 |
| | 遥测 | 含 stdio 命令名 | 仅计数 |
| **遥测** | 性能采样 | 100% | 0.5% |
| | Debug 日志 | 默认开启 | 需 --debug |
| | 模型名 | 原始 | 脱敏 |
| | Thinking 日志 | 捕获 | 剥离 |
| | GrowthBook 覆盖 | 环境变量 + /config | 无 |
| **归属** | Co-Authored-By | 动态（Undercover 时空） | 始终有 |
| **OAuth** | 基础设施 | 生产/staging/本地三套 | 仅生产 |
| **测试** | VCR 录制 | `FORCE_VCR=1` | 仅 test 环境 |
| | Mock 限速 | 21 种场景 | 无 |
| | Bridge 故障 | 6 种注入类型 | 无 |

---

## 关键文件索引

| 文件 | 内容 |
|------|------|
| `src/constants/prompts.ts` | 系统提示词所有 ant 差异 |
| `src/tools.ts` | 工具注册 4 处 ant 门控 |
| `src/commands.ts` | 27+ 内部命令注册 |
| `src/constants/tools.ts` | Agent 嵌套限制 |
| `src/constants/betas.ts` | 内部 Beta Header |
| `src/constants/oauth.ts` | 内部 OAuth 基础设施 |
| `src/utils/undercover.ts` | Undercover 隐身模式核心 |
| `src/utils/commitAttribution.ts` | 25+ 内部仓库白名单 |
| `src/utils/permissions/yoloClassifier.ts` | 权限分类 Prompt 选择 |
| `src/utils/permissions/PermissionMode.ts` | 权限模式差异 |
| `src/services/api/claude.ts` | API 行为差异（缓存、effort、research） |
| `src/services/api/withRetry.ts` | 重试策略差异 |
| `src/services/api/errors.ts` | 错误处理差异 |
| `src/services/api/dumpPrompts.ts` | API 请求缓存 |
| `src/services/vcr.ts` | VCR 录制回放 |
| `src/services/mockRateLimits.ts` | 21 种 Mock 限速场景 |
| `src/services/analytics/growthbook.ts` | GrowthBook 覆盖机制 |
| `src/services/analytics/datadog.ts` | Datadog 模型名脱敏 |
| `src/services/analytics/firstPartyEventLogger.ts` | 1P 事件日志差异 |
| `src/services/mcp/vscodeSdkMcp.ts` | VSCode MCP 通知 |
| `src/bridge/bridgeDebug.ts` | Bridge 故障注入 |
| `src/commands/bridge-kick.ts` | Bridge 调试命令 |
| `src/query.ts` | Dump Prompts、Thinking 签名处理 |
| `src/query/config.ts` | `isAnt` 查询配置 |
| `src/bootstrap/state.ts` | Bootstrap 状态差异 |
| `src/utils/model/antModels.ts` | 模型配置覆盖 |
| `src/tools/BashTool/bashPermissions.ts` | Bash 安全环境变量差异 |
