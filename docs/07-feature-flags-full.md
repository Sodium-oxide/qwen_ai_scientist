# Topic 7: 115 个 Build-Time Feature Flags 全解析

## TLDR

源码中通过 `bun:bundle` 的 `feature()` 函数发现了 **115 个编译时 feature flag**，远超社区此前猜测的 35 个。按实现完成度分为四级：**已发布**（6 个，外部构建中启用）、**完整实现但未发布**（~20 个，代码完整但 flag 关闭）、**部分实现**（~30 个，框架搭好但功能不完整）、**Stub/未实现**（~59 个，仅占位或零代码）。

最值得关注的未发布功能：**KAIROS**（持久化 AI 助手 + 自动做梦，389 行完整实现）、**BUDDY**（命令行电子宠物，1,687 行，计划 2026 年 4 月上线）、**COORDINATOR_MODE**（多 Agent 协调器，370 行）、**ULTRAPLAN**（30 分钟远程规划，2000+ 行）、**CHICAGO_MCP**（Computer Use 集成）。Anthropic 的产品路线图就藏在这些 flag 里——它们比任何官方博客都更真实地反映了未来方向。

---

## 一、机制原理

### 1.1 编译时死代码消除

```typescript
import { feature } from 'bun:bundle'

// 编译时：feature('VOICE_MODE') 被替换为 true 或 false 常量
// false 分支被 Bun tree-shake 完全删除——包括其 require() 的所有依赖
const voiceCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').default
  : null
```

**关键限制：必须正向匹配。**

```typescript
// ✅ 正确：正向 if 触发死代码消除
if (feature('BUDDY')) {
  const buddy = require('./buddy')
}

// ❌ 错误：反向 if 不触发 DCE
if (!feature('BUDDY')) return  // Bun 不会消除后续代码
```

### 1.2 与 GrowthBook 的两层控制

| 维度 | bun:bundle Flag | GrowthBook |
|------|----------------|------------|
| 求值时机 | 编译时 | 运行时（带缓存） |
| 开销 | 零（代码不存在） | if/else + 缓存读取 |
| 粒度 | 整个模块/功能 | 细粒度行为参数 |
| 回滚 | 需重新构建发布 | 即时远程切换 |
| 用途 | 未发布功能隔离 | A/B 测试、渐进发布、参数调优 |
| 典型 | `feature('BUDDY')` | `tengu_onyx_plover.minHours` |

**流程：** 功能开发 → `feature()=false` 隔离 → 代码完成 → `feature()=true` 编译 → GrowthBook 灰度 → 全量。

---

## 二、公开构建中已启用的 Flag（6 个）

这些 flag 在外部构建中为 `true`，用户可以体验到：

| Flag | 功能 | 证据 |
|------|------|------|
| **BRIDGE_MODE** | IDE 桥接（VS Code / JetBrains 远程控制） | 完整 Bridge 系统已发布 |
| **EXTRACT_MEMORIES** | 自动从对话中提取记忆 | Memory 系统已上线 |
| **COMMIT_ATTRIBUTION** | Git 提交中添加 `Co-Authored-By` | 用户可见功能 |
| **ANTI_DISTILLATION_CC** | 反蒸馏保护 | 安全核心功能 |
| **UDS_INBOX** | Unix Domain Socket 跨会话消息总线 | Agent 通信已发布 |
| **BUILTIN_EXPLORE_PLAN_AGENTS** | 内置探索/规划 Agent | Agent 工具中可用 |

**构建时平台检测（非功能 flag）：**
- `IS_LIBC_GLIBC` / `IS_LIBC_MUSL` — 用于二进制兼容性选择，非用户功能

---

## 三、完整 Flag 清单（115 个，按功能域 + 实现状态分类）

### 3.1 核心运行模式（8 个）

定义 Claude Code 能以什么"形态"运行：

| Flag | 引用次数 | 实现状态 | 描述 | 关键文件 | 代码量 |
|------|----------|----------|------|----------|--------|
| **BRIDGE_MODE** | 29 | 已发布 | IDE 桥接 + 远程控制 | `src/bridge/` | 12,600+ 行 |
| **COORDINATOR_MODE** | 32 | 完整未发布 | 多 Agent 协调器 | `src/coordinator/coordinatorMode.ts` | 370 行 |
| **PROACTIVE** | 37 | 完整未发布 | 主动助手模式（不等用户就行动） | Brief tool status="proactive" | 集成式 |
| **KAIROS** | 156 | 完整未发布 | 持久化助手 + 做梦 | `src/services/autoDream/` | 389 行 |
| **ULTRAPLAN** | 10 | 完整未发布 | 远程 30 分钟规划 | `src/commands/ultraplan.tsx` | ~2000+ 行 |
| **BUDDY** | 17 | 完整未发布 | 命令行电子宠物 | `src/buddy/` | 1,687 行 |
| **VOICE_MODE** | 49 | 部分实现 | 语音输入模式 | `src/voice/`, `src/commands/voice/` | ~224 行 |
| **DAEMON** | 3 | Stub | 后台守护进程（需 BRIDGE_MODE） | 分散引用 | ~0 |

### 3.2 KAIROS 子系统（6 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **KAIROS_BRIEF** | 39 | 完整未发布 | 简报模式（BriefTool） | 334 行 |
| **KAIROS_CHANNELS** | 21 | 完整未发布 | MCP 频道支持 | 集成式 |
| **KAIROS_DREAM** | 1 | Stub | `/dream` 技能命令入口 | ~0（autoDream 是独立系统） |
| **KAIROS_GITHUB_WEBHOOKS** | 4 | 未实现 | GitHub PR/事件 Webhook 订阅 | 0 |
| **KAIROS_PUSH_NOTIFICATION** | 4 | 未实现 | 推送通知 | 0 |
| **AWAY_SUMMARY** | 2 | Stub | 离开时生成摘要 | 极少 |

### 3.3 Agent / 触发器（7 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **AGENT_TRIGGERS** | 11 | 完整未发布 | Cron 定时 Agent 触发 | 620+ 行（含 3 个子工具） |
| **AGENT_TRIGGERS_REMOTE** | 2 | 部分实现 | 远程 Agent 触发 | 少量 |
| **AGENT_MEMORY_SNAPSHOT** | 2 | Stub | Agent 记忆快照 | 极少 |
| **FORK_SUBAGENT** | 5 | 部分实现 | 轻量子 Agent Fork | 100+ 行 |
| **BUILTIN_EXPLORE_PLAN_AGENTS** | 1 | 已发布 | 内置探索/规划 Agent | 集成式 |
| **VERIFICATION_AGENT** | 4 | Stub | 验证 Agent | 少量引用 |
| **MONITOR_TOOL** | 13 | 部分实现 | 进程/资源监控工具 | 部分 |

### 3.4 远程 / 分布式（8 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **UDS_INBOX** | 18 | 已发布 | Unix Socket 消息总线 | 970 行 |
| **BG_SESSIONS** | 11 | 完整未发布 | 后台会话管理 | 完整生命周期 |
| **CCR_AUTO_CONNECT** | 3 | Stub | CCR 自动连接 | 少量 |
| **CCR_MIRROR** | 4 | Stub | CCR 镜像 | 少量 |
| **CCR_REMOTE_SETUP** | 1 | Stub | 远程环境设置 | 极少 |
| **SSH_REMOTE** | 4 | Stub | SSH 远程连接 | 配置引用 |
| **SELF_HOSTED_RUNNER** | 1 | Stub | 自托管运行器 | 极少 |
| **BYOC_ENVIRONMENT_RUNNER** | 1 | 未实现 | BYOC 环境运行器 | 0 |

### 3.5 上下文 / 压缩（5 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **CONTEXT_COLLAPSE** | 23 | 完整未发布 | 上下文折叠/压缩 | 完整（compact/ 目录） |
| **CACHED_MICROCOMPACT** | 12 | 完整未发布 | 缓存微压缩 + GrowthBook | 集成式 |
| **REACTIVE_COMPACT** | 5 | 部分实现 | 响应式自动压缩 | 基础框架 |
| **COMPACTION_REMINDERS** | 1 | Stub | 上下文膨胀时提醒 | 极少 |
| **TOKEN_BUDGET** | 9 | 部分实现 | Token 预算管理 | UI 组件存在 |

### 3.6 工具增强（7 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **BASH_CLASSIFIER** | 49 | 完整未发布 | ML Bash 命令安全分类器 | 完整分类管道 |
| **TREE_SITTER_BASH** | 3 | 部分实现 | Tree-sitter Bash 解析 | 基础 |
| **TREE_SITTER_BASH_SHADOW** | 5 | 部分实现 | Shadow 模式（A/B 对比） | 对比框架 |
| **POWERSHELL_AUTO_MODE** | 2 | Stub | PowerShell 自动模式 | 少量 |
| **WEB_BROWSER_TOOL** | 4 | 未实现 | 网页浏览器工具 | 0 |
| **MCP_RICH_OUTPUT** | 3 | 部分实现 | MCP 富文本输出 | 少量 |
| **MCP_SKILLS** | 9 | 部分实现 | MCP 技能系统 | 框架存在 |

### 3.7 UI / 终端（8 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **CONNECTOR_TEXT** | 8 | 部分实现 | 连接器文本渲染 | UI 引用 |
| **AUTO_THEME** | 3 | Stub | 自动跟随终端深浅色 | 少量 |
| **STREAMLINED_OUTPUT** | 1 | Stub | 精简输出格式 | 极少 |
| **TERMINAL_PANEL** | 5 | Stub | 终端面板 UI | 偏好设置 |
| **MESSAGE_ACTIONS** | 5 | Stub | 消息操作菜单 | 键位绑定引用 |
| **HISTORY_PICKER** | 4 | Stub | 历史会话选择器 | 键位绑定引用 |
| **HISTORY_SNIP** | 16 | 完整未发布 | 历史裁剪工具 | 工具注册完整 |
| **QUICK_SEARCH** | 5 | Stub | 快速搜索覆盖层 | UI 引用 |

### 3.8 安全 / 合规（3 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **ANTI_DISTILLATION_CC** | 1 | 已发布 | 反蒸馏保护 | 可能在 API 层 |
| **NATIVE_CLIENT_ATTESTATION** | 1 | Stub | 原生客户端认证 | 极少 |
| **ABLATION_BASELINE** | 1 | Stub | 消融测试基线 | 极少 |

### 3.9 技能 / 插件（5 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **EXPERIMENTAL_SKILL_SEARCH** | 21 | 完整未发布 | 实验性技能搜索索引 | 完整 |
| **WORKFLOW_SCRIPTS** | 10 | 部分实现 | 工作流脚本系统 | 框架 |
| **SKILL_IMPROVEMENT** | 1 | Stub | 技能自我改进建议 | 极少 |
| **RUN_SKILL_GENERATOR** | 1 | Stub | 技能自动生成器 | 极少 |
| **TEMPLATES** | 6 | Stub | 模板系统 | 配置引用 |

### 3.10 数据 / 记忆（5 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **EXTRACT_MEMORIES** | 7 | 已发布 | 自动提取记忆 | 完整 |
| **FILE_PERSISTENCE** | 3 | Stub | 文件持久化层 | 少量 |
| **DOWNLOAD_USER_SETTINGS** | 5 | Stub | 用户设置云同步（下载） | 少量 |
| **UPLOAD_USER_SETTINGS** | 2 | Stub | 用户设置云同步（上传） | 少量 |
| **TEAMMEM** | — | Stub | 团队记忆共享 | 路径存在 |

### 3.11 遥测 / 调试（9 个）

| Flag | 引用次数 | 实现状态 | 描述 | 代码量 |
|------|----------|----------|------|--------|
| **TRANSCRIPT_CLASSIFIER** | 110 | 完整未发布 | 对话分类器（权限自动审批） | 大量 |
| **SHOT_STATS** | 10 | 部分实现 | Shot 统计面板 | UI 组件 |
| **ENHANCED_TELEMETRY_BETA** | 2 | Stub | 增强遥测 | 少量 |
| **COWORKER_TYPE_TELEMETRY** | 2 | Stub | 协作类型遥测 | 少量 |
| **MEMORY_SHAPE_TELEMETRY** | 3 | Stub | 记忆形态遥测 | 少量 |
| **PERFETTO_TRACING** | 1 | Stub | Perfetto 性能追踪 | 极少 |
| **SLOW_OPERATION_LOGGING** | 1 | Stub | 慢操作日志 | 极少 |
| **DUMP_SYSTEM_PROMPT** | 1 | Stub | 导出系统提示词 | 极少 |
| **PROMPT_CACHE_BREAK_DETECTION** | 9 | 部分实现 | 提示缓存中断检测 | 框架 |

### 3.12 构建 / 平台（4 个）

| Flag | 引用次数 | 实现状态 | 描述 |
|------|----------|----------|------|
| **IS_LIBC_GLIBC** | 1 | 构建检测 | glibc 环境检测 |
| **IS_LIBC_MUSL** | 1 | 构建检测 | musl libc 检测（Alpine） |
| **ALLOW_TEST_VERSIONS** | 2 | 内部开发 | 允许下载测试版本 |
| **BYOC_ENVIRONMENT_RUNNER** | 1 | 未实现 | BYOC 运行器 |

### 3.13 其他 / 未分类（17 个）

| Flag | 引用次数 | 实现状态 | 描述 |
|------|----------|----------|------|
| **CHICAGO_MCP** | 16 | 完整未发布 | Computer Use MCP 集成 |
| **COMMIT_ATTRIBUTION** | 12 | 已发布 | 提交归属标记 |
| **LODESTONE** | 6 | 部分实现 | 深度链接协议注册（`claude-code://`） |
| **DIRECT_CONNECT** | 5 | Stub | 直连模式（跳过代理） |
| **BUILDING_CLAUDE_APPS** | 1 | Stub | Claude 应用构建 |
| **BREAK_CACHE_COMMAND** | 2 | Stub | 缓存中断命令 |
| **CONNECTOR_TEXT** | 8 | 部分实现 | 连接器文本 |
| **HARD_FAIL** | 2 | Stub | 硬失败模式 |
| **HOOK_PROMPTS** | 1 | Stub | Hook 提示词 |
| **NEW_INIT** | 2 | Stub | 新初始化流程 |
| **OVERFLOW_TEST_TOOL** | 2 | 内部测试 | 溢出测试工具 |
| **REVIEW_ARTIFACT** | 4 | Stub | 审查产物 |
| **TORCH** | 1 | Stub | 用途不明 |
| **ULTRATHINK** | 1 | 部分实现 | 超级思考模式 |
| **UNATTENDED_RETRY** | 1 | Stub | 无人值守重试 |
| **NATIVE_CLIPBOARD_IMAGE** | 2 | Stub | 原生剪贴板图片 |
| **SKIP_DETECTION_WHEN_AUTOUPDATES_DISABLED** | 1 | Stub | 跳过版本检测 |

---

## 四、未发布功能深度分析

### 4.1 KAIROS + autoDream——AI 做梦系统

**实现状态：完整（389 行核心 + 多子系统集成）**
**引用次数：156（全码库最高）**

KAIROS 是源码中引用最频繁的 flag，156 处引用遍布核心系统。它的 autoDream 子系统实现了完整的"AI 做梦"功能：

**工作原理：**
1. 用户累积 5+ 个会话后触发
2. 每 24 小时检查一次（时间窗口门控）
3. 使用 PID 锁防止跨进程竞争
4. Fork 子 Agent 执行记忆整合 Prompt
5. 整合结果写入持久化记忆

**6 层门控系统：**
```
① 编译时 flag: feature('KAIROS')
② GrowthBook 远程开关
③ 会话数量阈值（≥5）
④ 时间间隔（≥24h）
⑤ PID 锁（跨进程互斥）
⑥ 用户设置（可手动禁用）
```

**关键代码：** `src/services/autoDream/consolidationLock.ts` 实现了完整的文件锁机制，说明这不是实验代码——是准备上线的生产代码。

### 4.2 BUDDY——命令行电子宠物

**实现状态：完整（1,687 行）**
**计划上线：2026 年 4 月 1-7 日预告，4 月正式**

BUDDY 是实现最完整的未发布功能之一：

- **18 种物种**（duck, cat, dragon, octopus, capybara...）
- **5 级稀有度**（Common 60% → Legendary 1%）
- **5 维属性**（DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK）
- **确定性生成**（Mulberry32 PRNG，种子 = hash(userId) + `'friend-2026-401'`）
- **500ms 动画 tick**（`CompanionSprite.tsx` 370 行渲染组件）
- **514 行精灵动画**（`sprites.ts`）

4/1 种子暗示这是 **愚人节彩蛋**。但 1,687 行代码量说明这不是玩笑——是认真做的功能。

### 4.3 COORDINATOR_MODE——多 Agent 协调器

**实现状态：完整（370 行系统提示词 + 工具配置）**

完整的多 Worker 编排系统：
- Coordinator 接收任务 → 拆解 → 派遣 Worker
- Worker 通过 XML `<task-notification>` 汇报
- 支持 `continue`（SendMessage）和 `spawn`（新 Agent）两种策略
- 4 阶段工作流：Research → Synthesis → Implementation → Verification

核心设计原则写在源码注释中：**"Never delegate understanding"**。

### 4.4 ULTRAPLAN——30 分钟远程规划

**实现状态：完整（2000+ 行）**

使用 CCR（Claude Code Remote）在云端执行长时间规划：
- 默认使用 Opus 4.6 模型
- ExitPlanModeScanner 状态机追踪审批工作流
- 支持远程执行或 teleport 回本地
- 两路同步：远程 ↔ 本地

### 4.5 CHICAGO_MCP——Computer Use 集成

**实现状态：完整（17 处引用，完整 MCP 配置 + 包装器）**

内部代号"Chicago"实际上是 **Computer Use** 的 MCP 集成：
- 包装 computer-use-enabled MCP Server
- 本地回退机制
- 完整的工具分发和清理逻辑

这意味着 Claude Code 的 Computer Use 能力已经**开发完成**，只是还没发布。

### 4.6 BASH_CLASSIFIER——AI 驱动的命令安全分类

**实现状态：完整（49 处引用，完整分类管道）**

```typescript
if (feature('BASH_CLASSIFIER') && tool.name === BASH_TOOL_NAME
    && result.pendingClassifierCheck) {
  const classifierDecision = await awaitClassifierAutoApproval(
    result.pendingClassifierCheck
  )
  if (classifierDecision)
    return { behavior: 'allow', decisionReason: classifierDecision }
}
```

用 ML 分类器判断 Bash 命令是否安全（如 `ls`、`git status`），安全命令自动审批、不打扰用户。与 `TRANSCRIPT_CLASSIFIER`（110 处引用）配合形成完整的自动权限系统。

### 4.7 BG_SESSIONS——后台会话管理

**实现状态：完整（11 处引用，完整生命周期管理）**

```bash
claude --bg "run tests"     # 后台启动
claude ps                    # 列出后台会话
claude logs <id>             # 查看日志
claude attach <id>           # 附加到会话
claude kill <id>             # 终止会话
```

完整的并发会话管理系统，类似 `tmux` 的体验。

### 4.8 AGENT_TRIGGERS——Cron 定时 Agent

**实现状态：完整（620+ 行，含 3 个子工具）**

- `CronCreateTool`：标准 5 字段 cron 语法创建定时任务
- `CronDeleteTool`：删除定时任务
- `CronListTool`：列出所有定时任务
- `RemoteTriggerTool`：远程 API 触发

支持时区处理、状态持久化、Zod schema 校验。

### 4.9 TRANSCRIPT_CLASSIFIER——对话分类器

**实现状态：完整（110 处引用——第二高频 flag）**

用于 `auto` 权限模式：AI 分析对话上下文自动决定是否批准工具调用。两套分类 Prompt：
- `permissions_anthropic.txt`（ant 用户）
- `permissions_external.txt`（外部用户）

这是实现"零打扰自动模式"的核心技术。

### 4.10 VOICE_MODE——语音输入

**实现状态：部分（224 行，STT 集成存在但命令本身较简）**

- 使用 claude.ai 的 `voice_stream` 端点（需 OAuth Token）
- GrowthBook kill-switch：`tengu_amber_quartz_disabled`
- STT（Speech-to-Text）和关键词提取已有实现
- 音频可视化组件存在

### 4.11 其他值得注意的未实现 Flag

| Flag | 含义 | 分析 |
|------|------|------|
| **WEB_BROWSER_TOOL** | 网页浏览器工具 | 0 行代码。但 CHICAGO_MCP 已实现 Computer Use，可能浏览器功能走 MCP 路线 |
| **DAEMON** | 后台守护进程 | 仅 3 处引用，都是 `feature('DAEMON') && feature('BRIDGE_MODE')` 的组合检查。可能是 KAIROS 的底层依赖 |
| **SSH_REMOTE** | SSH 远程连接 | 仅配置引用，无执行逻辑。可能被 CCR 方案替代 |
| **TEMPLATES** | 模板系统 | 仅配置 hook，无模板引擎。可能在 Skills 系统中实现 |
| **TORCH** | 用途完全不明 | 仅 1 处命令注册 stub，零实现。可能是内部代号 |
| **ULTRATHINK** | 超级思考模式 | 通过 `ultrathink` 关键词触发，GrowthBook gate `tengu_turtle_carbon`。可能是 Extended Thinking 的增强版 |
| **ANTI_DISTILLATION_CC** | 反蒸馏保护 | 已启用但 TypeScript 层无可见代码——保护可能在 API/模型层实现 |
| **NATIVE_CLIENT_ATTESTATION** | 客户端认证 | Stub。可能用于验证请求来自官方 Claude Code 而非第三方包装器 |

---

## 五、Flag 依赖关系图

```
KAIROS ─────┬── KAIROS_BRIEF
             ├── KAIROS_CHANNELS
             ├── KAIROS_DREAM (autoDream 系统)
             ├── KAIROS_GITHUB_WEBHOOKS
             ├── KAIROS_PUSH_NOTIFICATION
             └── AWAY_SUMMARY

BRIDGE_MODE ─┬── DAEMON (requires BRIDGE_MODE)
              ├── CCR_AUTO_CONNECT
              ├── CCR_MIRROR
              └── CCR_REMOTE_SETUP

PROACTIVE ───── KAIROS (常组合使用: feature('PROACTIVE') || feature('KAIROS'))

BASH_CLASSIFIER ── TRANSCRIPT_CLASSIFIER (共享分类管道)

TREE_SITTER_BASH ── TREE_SITTER_BASH_SHADOW (A/B 对比)

AGENT_TRIGGERS ── AGENT_TRIGGERS_REMOTE (远程扩展)

COORDINATOR_MODE ── UDS_INBOX (Worker 通信依赖)

DOWNLOAD_USER_SETTINGS ── UPLOAD_USER_SETTINGS (双向同步)
```

---

## 六、实现状态统计

| 状态 | 数量 | 占比 | 含义 |
|------|------|------|------|
| **已发布** | 6 | 5% | 外部构建中启用 |
| **完整未发布** | ~20 | 17% | 代码完整，flag 关闭 |
| **部分实现** | ~30 | 26% | 框架搭好，功能不完整 |
| **Stub/未实现** | ~59 | 52% | 仅占位或零代码 |
| **总计** | 115 | 100% | |

**按引用频率排名 Top 10：**

| 排名 | Flag | 引用次数 | 状态 |
|------|------|----------|------|
| 1 | KAIROS | 156 | 完整未发布 |
| 2 | TRANSCRIPT_CLASSIFIER | 110 | 完整未发布 |
| 3 | VOICE_MODE | 49 | 部分实现 |
| 4 | BASH_CLASSIFIER | 49 | 完整未发布 |
| 5 | KAIROS_BRIEF | 39 | 完整未发布 |
| 6 | PROACTIVE | 37 | 完整未发布 |
| 7 | COORDINATOR_MODE | 32 | 完整未发布 |
| 8 | BRIDGE_MODE | 29 | 已发布 |
| 9 | CONTEXT_COLLAPSE | 23 | 完整未发布 |
| 10 | EXPERIMENTAL_SKILL_SEARCH | 21 | 完整未发布 |

---

## 七、未来发布计划猜测

基于实现完成度、引用频率、代码中的日期线索和功能依赖关系，推测 Anthropic 的发布路线图：

### 第一梯队：近期发布（2026 Q2）

| 功能 | 信号 | 猜测时间 |
|------|------|----------|
| **BUDDY** | 代码中硬编码 `2026年4月1日`预告窗口、`4月`正式上线 | **2026年4月** |
| **BASH_CLASSIFIER** | 49 处引用，完整分类管道，权限系统核心组件 | **2026 Q2** |
| **TRANSCRIPT_CLASSIFIER** | 110 处引用（第二高），`auto` 权限模式的基础 | **2026 Q2** |
| **BG_SESSIONS** | 完整 CLI 接口（`--bg`, `ps`, `logs`, `attach`, `kill`） | **2026 Q2** |
| **CONTEXT_COLLAPSE** | 23 处引用，紧耦合 compact 系统，用户体验关键 | **2026 Q2** |

**理由：** 这些功能代码完整、引用密集、与现有系统深度集成。BUDDY 有明确日期线索。BASH_CLASSIFIER + TRANSCRIPT_CLASSIFIER 是实现"零打扰自动模式"的前置条件——这是 Claude Code 竞争力的关键差异点。

### 第二梯队：中期发布（2026 Q3）

| 功能 | 信号 | 猜测时间 |
|------|------|----------|
| **KAIROS**（核心） | 156 处引用，autoDream 完整，但涉及用户习惯重大改变 | **2026 Q3** |
| **KAIROS_BRIEF** | 依赖 KAIROS，334 行完整实现 | **与 KAIROS 同步** |
| **PROACTIVE** | 37 处引用，常与 KAIROS 组合 | **与 KAIROS 同步** |
| **COORDINATOR_MODE** | 370 行完整实现，但多 Agent 协调是高级功能 | **2026 Q3** |
| **AGENT_TRIGGERS** | 620+ 行，Cron 系统完整，可能作为 KAIROS 的子功能 | **2026 Q3** |
| **VOICE_MODE** | 49 处引用，STT 存在但命令较简，需更多打磨 | **2026 Q3** |

**理由：** KAIROS 是"AI 伴侣化"战略的核心——但它改变了用户与 AI 的交互范式（从被动响应到主动存在），需要更多用户教育和渐进引入。COORDINATOR_MODE 是高级用户功能，可能先灰度给 Pro/Enterprise 用户。

### 第三梯队：远期发布（2026 Q4+）

| 功能 | 信号 | 猜测时间 |
|------|------|----------|
| **ULTRAPLAN** | 2000+ 行完整实现，但依赖 CCR 基础设施 | **2026 Q4** |
| **CHICAGO_MCP**（Computer Use） | 完整 MCP 包装器，但 Computer Use 本身仍在演进 | **2026 Q4** |
| **KAIROS_GITHUB_WEBHOOKS** | 0 行代码，完全未实现 | **2027 H1** |
| **KAIROS_PUSH_NOTIFICATION** | 0 行代码，需要推送基础设施 | **2027 H1** |
| **WORKFLOW_SCRIPTS** | 部分实现，与 Skills 系统有重叠 | **2026 Q4** |
| **UPLOAD/DOWNLOAD_USER_SETTINGS** | Stub，需要云同步后端 | **2027** |

**理由：** 这些功能要么依赖尚未就绪的基础设施（CCR、推送服务），要么代码完成度低。KAIROS_GITHUB_WEBHOOKS 和 PUSH_NOTIFICATION 是 KAIROS 生态的终极形态——让 Claude 成为真正的"团队成员"，主动响应 GitHub 事件并推送通知。

### 可能永远不发布

| 功能 | 理由 |
|------|------|
| **TORCH** | 1 处引用，零实现，可能是废弃实验 |
| **LODESTONE** | 深度链接协议注册，可能被 Bridge 方案替代 |
| **SSH_REMOTE** | 可能被 CCR 方案完全替代 |
| **DAEMON** | 仅 3 处引用，可能合并入 KAIROS 的后台运行模式 |
| **SELF_HOSTED_RUNNER** | 可能被 BYOC 方案替代或合并 |

### 猜测的发布路线图时间线

```
2026-04 ┃ BUDDY 上线（愚人节彩蛋 + 正式功能）
        ┃ 
2026-Q2 ┃ BASH_CLASSIFIER + TRANSCRIPT_CLASSIFIER → "auto 模式"
        ┃ BG_SESSIONS → 后台会话
        ┃ CONTEXT_COLLAPSE → 上下文优化
        ┃ HISTORY_SNIP → 历史管理
        ┃
2026-Q3 ┃ KAIROS 核心 + BRIEF + PROACTIVE → "持久化 AI 助手"
        ┃ COORDINATOR_MODE → 多 Agent 协调
        ┃ AGENT_TRIGGERS → Cron 定时任务
        ┃ VOICE_MODE → 语音输入
        ┃
2026-Q4 ┃ ULTRAPLAN → 远程深度规划
        ┃ CHICAGO_MCP → Computer Use
        ┃ WORKFLOW_SCRIPTS → 工作流自动化
        ┃ FORK_SUBAGENT → 轻量 Agent Fork
        ┃
2027-H1 ┃ KAIROS_GITHUB_WEBHOOKS → GitHub 事件驱动
        ┃ KAIROS_PUSH_NOTIFICATION → 推送通知
        ┃ 用户设置云同步
        ┃ WEB_BROWSER_TOOL（如果不走 MCP 路线）
```

---

## 八、战略解读

### 8.1 从 115 个 Flag 看产品方向

按 flag 数量统计各方向的投入：

| 方向 | Flag 数 | 占比 | 解读 |
|------|---------|------|------|
| AI 伴侣化（KAIROS + BUDDY + PROACTIVE） | 15 | 13% | **核心战略方向** |
| Agent 生态（Coordinator + Triggers + Fork） | 7 | 6% | 多 Agent 协作 |
| 远程/分布式 | 8 | 7% | 云端执行 |
| 上下文管理 | 5 | 4% | 性能优化 |
| 工具增强 | 7 | 6% | 能力扩展 |
| UI/终端 | 8 | 7% | 体验打磨 |
| 遥测/调试 | 9 | 8% | 内部运营 |
| 其他 | 56 | 49% | 基础设施/实验 |

**结论：** AI 伴侣化和 Agent 生态是 Anthropic 投入最集中的两个方向。KAIROS 的 156 处引用说明这不是边缘实验——是贯穿整个代码库的核心架构决策。

### 8.2 115 个 Flag 不是技术债

编译时消除意味着：
- 未使用的 flag **零运行时开销**
- 不需要"清理" flag——它们就是不存在
- 一个代码库通过 flag 组合产出不同的"产品形态"（外部版、ant 版、Demo 版）
- 渐进发布：`feature()` → GrowthBook → 全量，全链路可控

### 8.3 竞争壁垒

最具战略意义的 flag：

1. **ANTI_DISTILLATION_CC** — 防止竞品通过 Claude Code 蒸馏模型
2. **NATIVE_CLIENT_ATTESTATION** — 确保请求来自官方客户端
3. **BASH_CLASSIFIER** + **TRANSCRIPT_CLASSIFIER** — AI 驱动的权限自动化（竞品难以复制）
4. **KAIROS** 全家族 — "会做梦的 AI"不是功能点，是产品范式

---

## 九、关键文件索引

| 文件 | Flag 门控数 | 内容 |
|------|------------|------|
| `src/commands.ts` | 15+ | 命令条件注册 |
| `src/tools.ts` | 15+ | 工具条件注册 |
| `src/cli/print.ts` | 25+ | 输出渲染条件 |
| `src/bridge/bridgeMain.ts` | 10+ | Bridge 会话管理 |
| `src/constants/prompts.ts` | 10+ | 系统提示词条件段落 |
| `src/main.tsx` | 8+ | 启动初始化 |
| `src/components/**` | 20+ | UI 渲染条件 |
| `src/coordinator/coordinatorMode.ts` | — | 多 Agent 编排 |
| `src/services/autoDream/` | — | KAIROS 做梦系统 |
| `src/buddy/` | — | BUDDY 伴侣精灵 |
| `src/tools/ScheduleCronTool/` | — | Cron 定时工具 |
| `src/tools/BriefTool/` | — | KAIROS Brief |
