# Topic 2: 功能开关驱动开发

## TLDR

Claude Code 使用 Bun 的 `bun:bundle` 编译时 feature flag 实现死代码消除，源码中发现了 **80+ 个 feature flag**，远超之前社区猜测的数量。大量 flag 处于未发布状态，涵盖"AI 做梦"（`KAIROS_DREAM`）、"伴侣精灵"（`BUDDY`）、"语音模式"（`VOICE_MODE`）、"主动模式"（`PROACTIVE`）等实验性功能，说明 Anthropic 内部正在疯狂实验"AI 伴侣化"方向。

---

## 1. Feature Flag 机制

### 工作原理

```typescript
import { feature } from 'bun:bundle'

// 编译时常量折叠 —— 未启用的分支在构建产物中完全消失
const voiceCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').default
  : null
```

**关键特性：**
- **编译时消除**：Bun 在打包时将 `feature()` 调用替换为布尔常量，未启用的代码分支被完全移除
- **零运行时开销**：不像传统 feature flag 需要运行时判断
- **必须正向匹配**：`if (!feature(...)) return` 的写法**不会**触发死代码消除，必须用 `if (feature(...)) { ... }` 的正向模式

### 与 GrowthBook 的分工

| 维度 | bun:bundle Flag | GrowthBook |
|------|----------------|------------|
| 时机 | 编译时 | 运行时 |
| 粒度 | 全量开/关 | 按用户/组织灰度 |
| 用途 | 未完成功能的完全隔离 | 已完成功能的渐进发布 |
| 成本 | 零（代码不存在） | 极低（一次判断） |

---

## 2. 完整 Flag 清单

### Agent 与协调

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `COORDINATOR_MODE` | 多 Agent 协调器模式 | 内部测试 |
| `BUILTIN_EXPLORE_PLAN_AGENTS` | 内置探索/规划 Agent | 已发布部分 |
| `FORK_SUBAGENT` | Agent 分叉 | 实验中 |
| `VERIFICATION_AGENT` | 验证 Agent | 实验中 |
| `UDS_INBOX` | 跨会话 Unix Socket 消息通信 | 已发布 |
| `TEAMMEM` | 团队记忆共享 | 实验中 |

### AI 伴侣化 / 拟人化

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `BUDDY` | 伴侣精灵系统 | 2026年4月上线 |
| `KAIROS` | 长时间运行/主动助手模式 | 实验中 |
| `KAIROS_BRIEF` | KAIROS 简报模式 | 实验中 |
| `KAIROS_CHANNELS` | KAIROS 频道系统 | 实验中 |
| `KAIROS_DREAM` | AI "做梦"功能 | 实验中 |
| `KAIROS_GITHUB_WEBHOOKS` | KAIROS GitHub 事件集成 | 实验中 |
| `KAIROS_PUSH_NOTIFICATION` | KAIROS 推送通知 | 实验中 |
| `PROACTIVE` | 主动模式（无需用户提示自动执行） | 实验中 |
| `AWAY_SUMMARY` | 离开时生成摘要 | 实验中 |

### 语音与交互

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `VOICE_MODE` | 语音输入模式 | 实验中 |
| `MESSAGE_ACTIONS` | 消息操作菜单 | 实验中 |
| `NATIVE_CLIPBOARD_IMAGE` | 原生剪贴板图片支持 | 实验中 |

### 工具与能力

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `MONITOR_TOOL` | 监控工具 | 实验中 |
| `WEB_BROWSER_TOOL` | 网页浏览器工具 | 实验中 |
| `OVERFLOW_TEST_TOOL` | 溢出测试工具 | 内部测试 |
| `QUICK_SEARCH` | 快速搜索 | 实验中 |
| `MCP_RICH_OUTPUT` | MCP 富文本输出 | 实验中 |
| `MCP_SKILLS` | MCP 技能支持 | 实验中 |

### 上下文与记忆

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `EXTRACT_MEMORIES` | 自动提取记忆 | 已发布 |
| `AGENT_MEMORY_SNAPSHOT` | Agent 记忆快照 | 实验中 |
| `MEMORY_SHAPE_TELEMETRY` | 记忆形态遥测 | 内部分析 |
| `COMPACTION_REMINDERS` | 压缩提醒 | 实验中 |
| `REACTIVE_COMPACT` | 响应式压缩 | 实验中 |
| `CACHED_MICROCOMPACT` | 缓存微压缩 | 实验中 |
| `CONTEXT_COLLAPSE` | 上下文折叠 | 实验中 |
| `TOKEN_BUDGET` | Token 预算控制 | 实验中 |

### 开发与构建

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `BRIDGE_MODE` | IDE 桥接模式 | 已发布 |
| `DAEMON` | 守护进程模式 | 实验中 |
| `BG_SESSIONS` | 后台会话 | 实验中 |
| `AGENT_TRIGGERS` | Agent 触发器 | 实验中 |
| `AGENT_TRIGGERS_REMOTE` | 远程 Agent 触发器 | 实验中 |
| `WORKFLOW_SCRIPTS` | 工作流脚本 | 实验中 |
| `TEMPLATES` | 模板系统 | 实验中 |

### Shell 与终端

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `BASH_CLASSIFIER` | Bash 命令分类器（自动审批安全命令） | 实验中 |
| `POWERSHELL_AUTO_MODE` | PowerShell 自动模式 | 实验中 |
| `TREE_SITTER_BASH` | Tree-sitter Bash 解析 | 实验中 |
| `TREE_SITTER_BASH_SHADOW` | Tree-sitter Bash 影子模式 | 内部对比测试 |
| `TERMINAL_PANEL` | 终端面板 | 实验中 |

### 远程与部署

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `SSH_REMOTE` | SSH 远程连接 | 实验中 |
| `CCR_AUTO_CONNECT` | CCR 自动连接 | 实验中 |
| `CCR_MIRROR` | CCR 镜像 | 实验中 |
| `CCR_REMOTE_SETUP` | CCR 远程设置 | 实验中 |
| `SELF_HOSTED_RUNNER` | 自托管运行器 | 实验中 |
| `BYOC_ENVIRONMENT_RUNNER` | BYOC 环境运行器 | 实验中 |
| `DIRECT_CONNECT` | 直连模式 | 实验中 |

### 安全与分析

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `ANTI_DISTILLATION_CC` | 反蒸馏保护 | 已启用 |
| `NATIVE_CLIENT_ATTESTATION` | 原生客户端认证 | 实验中 |
| `ENHANCED_TELEMETRY_BETA` | 增强遥测 Beta | 内部测试 |
| `PERFETTO_TRACING` | Perfetto 追踪 | 内部调试 |
| `SLOW_OPERATION_LOGGING` | 慢操作日志 | 内部调试 |
| `TRANSCRIPT_CLASSIFIER` | 对话分类器 | 内部分析 |
| `COWORKER_TYPE_TELEMETRY` | 协作类型遥测 | 内部分析 |

### UI 与输出

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `AUTO_THEME` | 自动主题切换 | 实验中 |
| `STREAMLINED_OUTPUT` | 精简输出 | 实验中 |
| `CONNECTOR_TEXT` | 连接器文本 | 实验中 |
| `HISTORY_PICKER` | 历史选择器 | 实验中 |
| `HISTORY_SNIP` | 历史剪辑 | 实验中 |
| `REVIEW_ARTIFACT` | 审查产物 | 实验中 |
| `SHOT_STATS` | 统计面板 | 实验中 |

### 技能与扩展

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `SKILL_IMPROVEMENT` | 技能自我改进 | 实验中 |
| `EXPERIMENTAL_SKILL_SEARCH` | 实验性技能搜索 | 实验中 |
| `RUN_SKILL_GENERATOR` | 运行技能生成器 | 实验中 |
| `BUILDING_CLAUDE_APPS` | 构建 Claude 应用 | 实验中 |
| `CHICAGO_MCP` | Chicago MCP（含义不明） | 实验中 |

### 其他

| Flag | 描述 | 状态推测 |
|------|------|----------|
| `ABLATION_BASELINE` | 消融基线测试 | 内部测试 |
| `ALLOW_TEST_VERSIONS` | 允许测试版本 | 内部开发 |
| `BREAK_CACHE_COMMAND` | 缓存破坏命令 | 内部调试 |
| `COMMIT_ATTRIBUTION` | 提交归属标记 | 已发布 |
| `DUMP_SYSTEM_PROMPT` | 导出系统提示词 | 内部调试 |
| `FILE_PERSISTENCE` | 文件持久化 | 实验中 |
| `HARD_FAIL` | 硬失败模式 | 内部测试 |
| `HOOK_PROMPTS` | 钩子提示 | 实验中 |
| `IS_LIBC_GLIBC` / `IS_LIBC_MUSL` | libc 类型检测 | 构建适配 |
| `LODESTONE` | Lodestone（含义不明） | 实验中 |
| `NEW_INIT` | 新初始化流程 | 实验中 |
| `PROMPT_CACHE_BREAK_DETECTION` | 提示缓存中断检测 | 实验中 |
| `TORCH` | Torch（含义不明） | 实验中 |
| `ULTRAPLAN` | 超级规划模式 | 实验中 |
| `ULTRATHINK` | 超级思考模式 | 实验中 |
| `UNATTENDED_RETRY` | 无人值守重试 | 实验中 |
| `UPLOAD_USER_SETTINGS` / `DOWNLOAD_USER_SETTINGS` | 用户设置云同步 | 实验中 |

---

## 3. 值得关注的趋势

### AI 伴侣化方向

KAIROS 系列 flag（`KAIROS`, `KAIROS_DREAM`, `KAIROS_CHANNELS`, `KAIROS_PUSH_NOTIFICATION`）+ `BUDDY` + `PROACTIVE` 勾勒出一个清晰的方向：

> Anthropic 想让 Claude Code 从"被动的代码工具"变成"主动的 AI 伙伴"——它会做梦、会主动干活、有自己的精灵形象、会推送通知提醒你。

### 多 Agent 生态

`COORDINATOR_MODE` + `UDS_INBOX` + `TEAMMEM` + `FORK_SUBAGENT` + `VERIFICATION_AGENT` 表明 Anthropic 在构建完整的多 Agent 协作生态。

### 内部实验密度

80+ 个 flag 中估计只有不到 20 个在公开版本中启用，其余 60+ 个都是内部实验。这说明 Claude Code 的公开版只是冰山一角。

---

## 4. 对社区的影响

之前社区通过抓包和逆向猜测的 feature flag，现在有了官方对照版。几个重要发现：

1. **`ANTI_DISTILLATION_CC`** — 证实了 Anthropic 在对抗模型蒸馏
2. **`ABLATION_BASELINE`** — 内部有系统的消融测试流程
3. **`DUMP_SYSTEM_PROMPT`** — 内部调试时可以直接导出完整系统提示词
4. **`ULTRATHINK` / `ULTRAPLAN`** — 暗示有更高级的思考/规划模式尚未发布

这些 flag 就是 Anthropic 内部的实验路线图，比任何官方博客都更真实地反映了产品方向。
