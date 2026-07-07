# Topic 9: 内部斜杠命令

## TLDR

共找到 **84+ 个命令目录**，其中约 **60+ 个公开命令**、**30+ 个内部命令**（需 `USER_TYPE=ant`）、**15+ 个需 feature flag** 的隐藏命令。最有趣的发现：`/bridge-kick` 可以精确注入桥接故障模式（对标 BigQuery 中的真实故障统计）；`/teleport` 实现会话跨设备迁移；`/good-claude` 是内部质量信号；`/bughunter` 启动分布式 bug 猎手舰队（5-20 个代理并行扫 PR）。

---

## 1. 内部命令的 Stub 机制

定义在 `src/commands.ts:225-254`：

```typescript
const INTERNAL_ONLY_COMMANDS = [
  'backfill-sessions', 'break-cache', 'bughunter', 'commit', 'commit-push-pr',
  'ctx-viz', 'good-claude', 'issue', 'init-verifiers', 'mock-limits',
  'bridge-kick', 'version', 'reset-limits', 'onboarding', 'share', 'summary',
  'teleport', 'ant-trace', 'perf-issue', 'env', 'oauth-refresh',
  'debug-tool-call', 'agents-platform', 'autofix-pr',
]
```

外部构建中这些命令不是"隐藏"——它们被 **stub**，调用时返回空操作。这让 `/help` 中看不到它们，但如果有人碰巧输入也不会崩溃。

---

## 2. 最有趣的内部命令

### /bridge-kick——对标真实故障数据的注入工具

定义在 `src/commands/bridge-kick.ts`：

```typescript
/**
 * Ant-only: inject bridge failure states to manually test recovery paths.
 *
 *   /bridge-kick close 1002            — fire ws_closed with code 1002
 *   /bridge-kick close 1006            — fire ws_closed with code 1006
 *   /bridge-kick poll 404              — next poll throws 404/not_found_error
 *   /bridge-kick poll 404 <type>       — next poll throws 404 with error_type
 *   /bridge-kick register fail         — next register transient-fails
 *   /bridge-kick register fail 3       — next 3 registers transient-fail
 *   /bridge-kick register fatal        — next register 403s (terminal)
 *   /bridge-kick reconnect-session fail — POST /bridge/reconnect fails
 *   /bridge-kick heartbeat 401         — next heartbeat 401s (JWT expired)
 *   /bridge-kick reconnect             — call doReconnect directly (SIGUSR2)
 *   /bridge-kick status                — print current bridge state
 */
```

注释中引用了真实的故障统计：

```
// BQ data (2026-03-12, 7-day window):
// - poll 404/not_found_error: 147K sessions/week
// - ws_closed 1002/1006: 22K sessions/week
// - register transient failures: residual network blips
```

还有**复合故障序列**测试——因为真实故障是链式的：

```
// #22148 residual: ws_closed → register transient-blips → teardown?
/bridge-kick register fail 2
/bridge-kick close 1002
→ expect: doReconnect tries register, fails, returns false → teardown
```

### /teleport——跨设备会话迁移

```typescript
// src/commands/teleport/
// 将当前会话传送到远程 CCR 实例或从远程传回本地
// 支持 ULTRAPLAN 的 "本地执行" 路径
```

`/teleport` 是 ULTRAPLAN 生态的一部分——在浏览器中规划完成后，可以把计划"传送"回本地终端执行。反过来，也可以把本地会话传送到云端继续。

### /good-claude——内部质量信号

这个命令在外部构建中完全被 stub，源码中没有详细说明。根据名称和上下文推断，它可能是：
- 质量反馈机制（标记"这次回答好"）
- 类似 👍 按钮的内部评分工具
- 用于收集正向训练信号

### /bughunter——分布式 Bug 猎手

```typescript
// src/commands/review/reviewRemote.ts
const commonEnvVars = {
  BUGHUNTER_FLEET_SIZE: String(posInt(raw?.fleet_size, 5, 20)),
  BUGHUNTER_MAX_DURATION: String(posInt(raw?.max_duration_minutes, 10, 25)),
  BUGHUNTER_AGENT_TIMEOUT: String(posInt(raw?.agent_timeout_seconds, 600, 1800)),
  BUGHUNTER_TOTAL_WALLCLOCK: String(posInt(raw?.total_wallclock_minutes, 22, 27)),
}
```

同时启动 5-20 个代理去扒一个 PR 里的 bug：
- 每个代理独立运行 10-25 分钟
- 单代理超时 600-1800 秒
- 总壁钟时间 22-27 分钟（留 3 分钟做最终汇总）
- `BUGHUNTER_DEV_BUNDLE_B64` 支持 hot-reload 开发包

---

## 3. Feature-Flag 门控命令

### KAIROS 系列

| 命令 | Flag | 功能 | 深入 |
|------|------|------|------|
| `/assistant` | `KAIROS` | 持久化助手模式 | 长会话 + 每日日志 |
| `/brief` | `KAIROS` / `KAIROS_BRIEF` | 简报模式 | 精简输出 |
| `/proactive` | `PROACTIVE` / `KAIROS` | 主动助手 | 不等用户提问就行动 |
| `/subscribe-pr` | `KAIROS_GITHUB_WEBHOOKS` | PR 事件订阅 | GitHub webhook 推送 |

### 代理/规划

| 命令 | Flag | 功能 | 深入 |
|------|------|------|------|
| `/ultraplan` | `ULTRAPLAN` | 30 分钟远程规划 | Opus 级模型 + CCR |
| `/fork` | `FORK_SUBAGENT` | Fork 子代理 | 轻量级并行 |
| `/peers` | `UDS_INBOX` | 本地会话发现 | 列出 UDS 可达的会话 |

### 基础设施

| 命令 | Flag | 功能 | 深入 |
|------|------|------|------|
| `/remote-control` | `BRIDGE_MODE` | 远程控制（别名 `/rc`） | QR 码 + WebSocket |
| `/remoteControlServer` | `DAEMON` + `BRIDGE_MODE` | 守护进程服务器 | 无头多会话 |
| `/voice` | `VOICE_MODE` | 语音模式 | STT 流式输入 |
| `/web-setup` | `CCR_REMOTE_SETUP` | Web 远程设置 | claude.ai 设置 |

### 其他

| 命令 | Flag | 功能 |
|------|------|------|
| `/buddy` | `BUDDY` | 电子宠物 |
| `/workflows` | `WORKFLOW_SCRIPTS` | 工作流脚本 |
| `/torch` | `TORCH` | 用途不明 |
| `/force-snip` | `HISTORY_SNIP` | 强制历史裁剪（内部） |

---

## 4. GrowthBook/Statsig 门控命令

这些需要远程 feature gate 开启——不是编译时 flag，是运行时远程开关：

| 命令 | Feature Gate | 功能 |
|------|-------------|------|
| `/thinkback` | `tengu_thinkback` | 2025 年度回顾（统计你用 Claude Code 写了多少代码） |
| `/thinkback-play` | `tengu_thinkback` | 回放回顾动画（隐藏命令） |
| `/web-setup` | `tengu_cobalt_lantern` | Web 远程设置 |
| `/passes` | 推荐资格检查 | 分享免费体验周 |

---

## 5. 隐藏命令（isHidden: true）

在 `/help` 中不显示但可以使用：

| 命令 | 用途 | 为什么隐藏 |
|------|------|-----------|
| `/heapdump` | 导出 JS 堆到 ~/Desktop | 调试用 |
| `/output-style` | 输出格式配置 | 实验性 |
| `/rate-limit-options` | 速率限制配置 | 高级选项 |
| `/thinkback-play` | 回放 thinkback 动画 | 彩蛋性质 |

---

## 6. 其他内部命令速览

| 命令 | 用途 |
|------|------|
| `/backfill-sessions` | 会话数据回填（可能用于数据修复） |
| `/break-cache` | 缓存调试（强制清除内部缓存） |
| `/mock-limits` | 模拟速率限制（测试限流 UI） |
| `/reset-limits` | 重置速率限制 |
| `/debug-tool-call` | 工具调用调试（查看完整输入/输出） |
| `/env` | 环境变量内省（设置会话级 env） |
| `/oauth-refresh` | OAuth token 手动刷新 |
| `/ant-trace` | 内部追踪（可能是 OpenTelemetry trace 导出） |
| `/perf-issue` | 性能问题报告 |
| `/agents-platform` | 代理平台管理（Anthropic 内部代理基础设施） |
| `/autofix-pr` | 自动修复 PR |
| `/init-verifiers` | 验证器初始化 |

---

## 7. 公开命令中的 Ant 增强

某些公开命令在 ant 构建中有额外功能：

| 命令 | Ant 增强 |
|------|---------|
| `/commit` | Undercover 模式（隐藏内部模型代号） |
| `/commit-push-pr` | Undercover + 集成 PR 流程 |
| `/share` | 扩展内部诊断信息 + Slack 自动发送 |
| `/summary` | 内部指标（API 成本、token 使用量） |
| `/issue` | 扩展 bug 报告（含内部 trace） |
| `/insights` | 远程指标 + 内部成本追踪 |
| `/context` | 扩展报告（消息分解、分析详情） |

---

## 8. 统计总结

| 类别 | 数量 |
|------|------|
| 公开命令 | ~60+ |
| 内部命令（ant-only, stubbed） | ~30+ |
| Feature-flag 门控 | ~15+ |
| GrowthBook 门控 | ~5+ |
| 隐藏命令 | ~4 |
| **总计命令目录** | **84+** |

---

## 设计哲学总结

1. **Stub 而非 404**：内部命令在外部构建中被 stub，不会让用户困惑或报错
2. **数据驱动测试**：`/bridge-kick` 的故障模式直接来自 BigQuery 生产数据
3. **分层门控**：编译时 flag → 运行时 GrowthBook → `isHidden` → `isEnabled`，四层控制
4. **增强而非分叉**：公开命令通过 ant 分支增强，而不是维护完全独立的版本
