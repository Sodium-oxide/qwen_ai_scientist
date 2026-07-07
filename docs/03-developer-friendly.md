# Topic 3: 开发者友好到离谱

## TLDR

Claude Code 源码暴露了 **120+ 个环境变量**、完整的 **GrowthBook 远程特性开关系统**、**员工模式（ant）一键解锁**、**远程 Bridge** 和大量内部命令。整个系统的可配置程度远超任何公开文档的描述——API 端点可换、OAuth 可自定义、沙箱可绕过、遥测可注入。感觉不像是面向用户的产品，更像是 Anthropic 内部工程师的**私人玩具箱**。

---

## 1. 环境变量大全

### 核心配置

| 环境变量 | 用途 |
|----------|------|
| `CLAUDE_CODE_API_BASE_URL` | 自定义 API 端点（可接第三方） |
| `CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR` | 通过文件描述符读取 API Key |
| `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` | API Key Helper 缓存 TTL |
| `CLAUDE_CODE_SKIP_PROMPT_HISTORY` | 跳过提示历史存储 |
| `CLAUDE_CODE_DISABLE_CLAUDE_MDS` | 禁用 CLAUDE.md 扫描 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Context 压缩窗口大小 |
| `CLAUDE_CODE_ENTRYPOINT` | 当前入口点名称 |

### Agent 与协调

| 环境变量 | 用途 |
|----------|------|
| `CLAUDE_CODE_AGENT_ID` | Agent 标识（团队上下文） |
| `CLAUDE_CODE_AGENT_NAME` | Agent 名称（Inbox 轮询用） |
| `CLAUDE_CODE_AGENT_COLOR` | 团队 UI 中的颜色徽章 |
| `CLAUDE_CODE_COORDINATOR_MODE` | 启用 Coordinator 模式（`1`/`0`） |
| `CLAUDE_CODE_SIMPLE` | 限制 Worker 仅使用 Bash/Read/Edit |

### 远程与 Bridge

| 环境变量 | 用途 |
|----------|------|
| `CLAUDE_CODE_REMOTE` | 启用远程模式（自动调整 NODE_OPTIONS 内存） |
| `CLAUDE_CODE_CCR_V2` | 使用 CCR V2 传输层 |
| `CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2` | CCR V2 会话投递 |

### OAuth 与认证

| 环境变量 | 用途 |
|----------|------|
| `CLAUDE_CODE_CUSTOM_OAUTH_URL` | 自定义 OAuth Provider |
| `CLAUDE_CODE_OAUTH_CLIENT_ID` | OAuth Client ID 覆写 |
| `USE_LOCAL_OAUTH` | 使用本地 OAuth 服务器（ant-only） |
| `USE_STAGING_OAUTH` | 使用 Staging OAuth 服务器（ant-only） |
| `CLAUDE_LOCAL_OAUTH_API_BASE` | 本地 OAuth API URL（ant-only） |
| `CLAUDE_LOCAL_OAUTH_APPS_BASE` | 本地 OAuth Apps URL（ant-only） |
| `CLAUDE_LOCAL_OAUTH_CONSOLE_BASE` | 本地 OAuth Console URL（ant-only） |

### 安全与调试

| 环境变量 | 用途 |
|----------|------|
| `CLAUDE_CODE_UNDERCOVER` | 强制启用隐身模式（提交信息安全） |
| `CLAUDE_CODE_ABLATION_BASELINE` | 消融基线测试模式 |
| `CLAUDE_CODE_SKIP_DETECTION_WHEN_AUTOUPDATES_DISABLED` | 禁用更新检测时跳过版本检测 |

### 标准 `USER_TYPE`

| 值 | 含义 |
|----|------|
| `ant` | Anthropic 内部员工 |
| 其他/未设置 | 普通用户 |

---

## 2. GrowthBook 远程特性开关

**源码位置：** `src/services/analytics/growthbook.ts`

### 用户属性（用于灰度定向）

GrowthBook 可以根据以下属性精确控制功能发布：

```typescript
{
  id: string,                    // 用户 ID
  sessionId: string,             // 会话 ID
  deviceID: string,              // 设备 ID
  platform: 'win32' | 'darwin' | 'linux',
  apiBaseUrlHost: string,        // API 主机
  organizationUUID: string,      // 组织 UUID
  accountUUID: string,           // 账户 UUID
  userType: string,              // 用户类型（ant 等）
  subscriptionType: string,      // 订阅类型
  rateLimitTier: string,         // 速率限制级别
  firstTokenTime: number,        // 首次 Token 时间
  email: string,                 // 邮箱
  appVersion: string,            // 应用版本
  github: GitHubActionsMetadata  // GitHub Actions 元数据
}
```

### 缓存机制

- `getFeatureValue_CACHED_MAY_BE_STALE()` — 可用于热路径（渲染循环、校验器），接受过时值
- `remoteEvalFeatureValues` Map 缓存远程评估结果
- 曝光去重：`loggedExposures` 防止重复上报

### 实时刷新

- `onGrowthBookRefresh()` — 注册回调，功能值变更时触发
- 适用于长生命周期系统（如 firstPartyEventLogger）
- 初始化完成或定期刷新时在下一个微任务触发

### 异常处理

- 处理 API 返回 `value` 而非 `defaultValue` 的畸形响应
- 认证变更时重新初始化，使用 `reinitializingPromise` 门控安全检查

---

## 3. 员工模式（ant）

**判断方式：** `process.env.USER_TYPE === 'ant'`

### Undercover 模式

**源码位置：** `src/utils/undercover.ts`（90 行）

这是最有趣的内部功能之一——当 Anthropic 员工在**公开/开源仓库**中使用 Claude Code 时，自动启用"隐身模式"：

| 行为 | 说明 |
|------|------|
| 模型不知道自己是什么模型 | System Prompt 中不告知模型身份 |
| 剥离内部代号 | Capybara、Tengu 等内部代号从输出中移除 |
| 清理提交信息 | Co-Authored-By 等归属标记在隐身模式下被移除 |
| 清理项目名称 | Anthropic 内部项目名从输出中移除 |

**触发条件：**
- 自动检测：当前仓库被识别为公开/开源
- 手动强制：`CLAUDE_CODE_UNDERCOVER=1`

### ant-only 工具

| 工具 | 描述 |
|------|------|
| `REPLTool` | 交互式 REPL（不暴露给外部用户） |
| `SuggestBackgroundPRTool` | 后台建议 PR |
| `agents-platform` 命令 | Agent 平台后端 |

### ant-only 命令

部分 slash 命令仅对内部员工可见（通过 `isAnt()` 检查控制）。

### 内部 OAuth

```
USE_LOCAL_OAUTH=1     → 连接本地 OAuth 服务器
USE_STAGING_OAUTH=1   → 连接 Staging OAuth 服务器
```

配套一组 `CLAUDE_LOCAL_OAUTH_*` 环境变量配置内部 OAuth URL。

---

## 4. 远程 Bridge 系统

**源码位置：** `src/bridge/`（12,600+ 行）

Bridge 是 Claude Code 与 IDE（VS Code、JetBrains）之间的双向通信层。

### 架构

```
┌──────────┐     JWT 认证      ┌──────────────┐
│  IDE 扩展  │ ◄──────────────► │  Claude Code  │
│ (VS Code) │   双向消息协议    │    (CLI)      │
└──────────┘                   └──────────────┘
```

### 核心文件

| 文件 | 职责 |
|------|------|
| `bridgeMain.ts` | Bridge 主循环 |
| `bridgeMessaging.ts` | 消息协议 |
| `bridgePermissionCallbacks.ts` | 权限回调 |
| `sessionRunner.ts` | 会话执行管理 |
| `jwtUtils.ts` | JWT 认证 |
| `replBridge.ts` | REPL 会话桥接 |

### 传输方式

支持多种传输层：
- **Stdio** — 子进程标准输入输出
- **SSE** — Server-Sent Events
- **StreamableHTTP** — HTTP 流
- **WebSocket** — WebSocket 连接

---

## 5. 内部命令与调试工具

### 86+ Slash 命令

源码中注册了 86+ 个 slash 命令，很多从未出现在公开文档中：

| 命令 | 描述 | 公开状态 |
|------|------|----------|
| `/commit` | Git 提交 | 公开 |
| `/review` | 代码审查 | 公开 |
| `/ultrareview` | 深度代码审查 | 未知 |
| `/security-review` | 安全审查 | 未知 |
| `/autofix-pr` | 自动修复 PR | 未知 |
| `/proactive` | 主动模式 | Feature-gated |
| `/brief` | 简洁模式 | Feature-gated |
| `/voice` | 语音模式 | Feature-gated |
| `/workflows` | 工作流 | Feature-gated |
| `/assistant` | 助手模式 | Feature-gated |
| `/bridge` | IDE Bridge | Feature-gated |
| `/desktop` | 桌面应用切换 | 公开 |
| `/mobile` | 移动应用切换 | 公开 |

### 调试用 Feature Flag

| Flag | 用途 |
|------|------|
| `DUMP_SYSTEM_PROMPT` | 导出完整系统提示词 |
| `BREAK_CACHE_COMMAND` | 手动触发缓存中断 |
| `SLOW_OPERATION_LOGGING` | 慢操作日志 |
| `PERFETTO_TRACING` | Perfetto 性能追踪 |

---

## 6. 启动优化——并行预取

**源码位置：** `src/main.tsx`

Claude Code 的启动流程经过极致优化，多项操作并行执行：

```typescript
// 在其他 import 之前就作为副作用触发
startMdmRawRead()         // MDM 设置预读
startKeychainPrefetch()    // 钥匙串预取
// API 预连接 + GrowthBook 初始化也并行进行
```

### 懒加载策略

重型模块按需加载，避免拖慢启动：

| 模块 | 加载时机 |
|------|----------|
| OpenTelemetry | 首次遥测事件 |
| gRPC | 首次远程调用 |
| Analytics | 首次分析事件 |
| Feature-gated 子系统 | 相关功能被触发时 |

### 循环依赖打破

使用惰性 `require()` 包装在 getter 函数中：

```typescript
// 避免循环引用
function getTeamCreateTool() {
  return require('./tools/TeamCreateTool').default
}
```

---

## 总结

Claude Code 的可配置程度堪称"开发者的天堂"：

1. **120+ 环境变量** — 几乎每个行为都可以通过环境变量控制
2. **GrowthBook** — 运行时远程特性开关，支持按用户/组织/平台精准灰度
3. **员工模式** — `USER_TYPE=ant` 一键解锁所有内部工具和命令
4. **Undercover** — 内部员工在公开仓库的"隐身衣"
5. **Bridge** — 完整的 IDE 双向通信协议
6. **86+ 命令** — 公开文档只展示了冰山一角

这不是一个简单的 CLI 工具的配置量级——这是一个**平台**的配置量级。
