# Topic 8: 478 个环境变量

## TLDR

社区说 120+，实际找到 **478 个**独立环境变量。最大类别是 `CLAUDE_CODE_*`（196 个，占 41%），覆盖网络、认证、性能调优、UI、插件、远程等几乎所有子系统。还有 `ANTHROPIC_*`（26 个）API 配置、`OTEL_*`（22 个）可观测性、`DISABLE_*/ENABLE_*`（33 个）运行时开关。最有趣的发现：SWE-Bench 评测集成、VCR API 录制回放系统、神秘的 `CLAUBBIT` 标志、以及一个完整的"安全 vs 危险"环境变量分类系统。

---

## 1. 安全 vs 危险——环境变量信任模型

定义在 `src/utils/managedEnvConstants.ts`。这是整个环境变量系统最值得关注的设计：

**Safe 变量**（信任对话框之前就应用，共 191 个）：

```typescript
export const SAFE_ENV_VARS = new Set([
  'ANTHROPIC_CUSTOM_HEADERS',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'AWS_REGION', 'AWS_PROFILE',
  'BASH_DEFAULT_TIMEOUT_MS', 'BASH_MAX_OUTPUT_LENGTH',
  'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
  'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
  'DISABLE_AUTOUPDATER', 'DISABLE_TELEMETRY',
  'ENABLE_TOOL_SEARCH',
  'MCP_*', 'OTEL_*',
  // ... 共 191 个
])
```

**Dangerous 变量**（需要信任对话框）：
- `ANTHROPIC_BASE_URL` — 可重定向到攻击者服务器
- `NODE_TLS_REJECT_UNAUTHORIZED` — 禁用 SSL 验证
- `NODE_EXTRA_CA_CERTS` — 信任攻击者证书
- `LD_PRELOAD`, `PATH` — 可执行代码注入
- HTTP 代理变量

### 供应商路由保护

```typescript
// src/utils/managedEnv.ts
// 当宿主（如 Claude Desktop）设置了 CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST，
// 剥离用户 settings.json 中的供应商选择变量，防止请求被重定向
function withoutHostManagedProviderVars(env) {
  if (!isEnvTruthy(process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST)) return env
  const out = {}
  for (const [key, value] of Object.entries(env)) {
    if (!isProviderManagedEnvVar(key)) out[key] = value
  }
  return out
}
```

---

## 2. 分类统计

| 类别 | 数量 | 占比 | 代表 |
|------|------|------|------|
| `CLAUDE_CODE_*` | 196 | 41% | API_BASE_URL, MAX_CONTEXT_TOKENS |
| 其他/内部 | 105 | 22% | CLAUBBIT, SWE_BENCH_*, FORCE_VCR |
| `DISABLE_*` / `ENABLE_*` | 33 | 7% | DISABLE_AUTO_COMPACT, ENABLE_LSP_TOOL |
| `CLAUDE_*`（桥接/SDK） | 31 | 6% | BRIDGE_BASE_URL, AGENT_SDK_VERSION |
| `ANTHROPIC_*` | 26 | 5% | API_KEY, MODEL, BEDROCK_BASE_URL |
| 系统标准 | 26 | 5% | HOME, SHELL, TERM |
| `OTEL_*` / `ANT_OTEL_*` | 22 | 5% | EXPORTER_OTLP_ENDPOINT |
| CI/CD 检测 | 20 | 4% | GITHUB_ACTIONS, GITLAB_CI |
| AWS/Cloud | 11 | 2% | AWS_REGION, VERTEX_BASE_URL |
| `MCP_*` | 8 | 2% | TOOL_TIMEOUT, OAUTH_CALLBACK_PORT |

---

## 3. 最有趣的发现

### A. VCR——API 录制/回放系统

定义在 `src/services/vcr.ts`：

```typescript
function shouldUseVCR(): boolean {
  if (process.env.NODE_ENV === 'test') return true
  if (process.env.USER_TYPE === 'ant' && isEnvTruthy(process.env.FORCE_VCR)) return true
  return false
}
```

VCR 是一个完整的 API mocking 系统：
- `FORCE_VCR=1`：强制 API 录制/回放（ant-only）
- `VCR_RECORD=1`：CI 中录制新 fixtures
- Fixtures 按输入消息 SHA1 哈希缓存
- **路径脱水**让 fixtures 跨机器稳定：

```typescript
function dehydrateValue(s) {
  return s
    .replace(/num_files="\d+"/g, 'num_files="[NUM]"')
    .replace(/duration_ms="\d+"/g, 'duration_ms="[DURATION]"')
    .replace(/cost_usd="\d+"/g, 'cost_usd="[COST]"')
    .replaceAll(configHome, '[CONFIG_HOME]')
    .replaceAll(cwd, '[CWD]')
}
```

将路径、成本、时长全部替换为占位符——这样 `home/alice` 和 `home/bob` 的 fixtures 完全一样。

### B. SWE-Bench 集成

```typescript
// src/services/analytics/metadata.ts
sweBenchRunId:      process.env.SWE_BENCH_RUN_ID || '',
sweBenchInstanceId: process.env.SWE_BENCH_INSTANCE_ID || '',
sweBenchTaskId:     process.env.SWE_BENCH_TASK_ID || '',
```

三个 ID 追踪 Claude Code 在 SWE-Bench 评测上的表现。SWE-Bench 评测 AI 解决真实 GitHub issue 的能力——这说明 Anthropic 把 Claude Code 当作 SWE-Bench 的跑分工具在用。

### C. CLAUBBIT——神秘标志

```typescript
// src/interactiveHelpers.tsx
if (!isEnvTruthy(process.env.CLAUBBIT)) {
  // ... 某个交互功能
}

// src/services/analytics/metadata.ts
isClaubbit: isEnvTruthy(process.env.CLAUBBIT),
```

完整搜索后只在这两处出现。影响交互功能 + 被遥测追踪，但没有任何文档或注释解释它是什么。可能是内部 A/B 测试的代号——`Clau` + `rabbit`？与 BUDDY 系统的 rabbit 物种有关？纯猜测。

### D. BugHunter 开发包

```typescript
// src/commands/review/reviewRemote.ts
...(process.env.BUGHUNTER_DEV_BUNDLE_B64 && {
  BUGHUNTER_DEV_BUNDLE_B64: process.env.BUGHUNTER_DEV_BUNDLE_B64,
}),
```

可以注入 Base64 编码的开发包到 BugHunter 舰队。这是一个开发时的 hot-reload 机制——不需要发布新版本就能测试 BugHunter 的代码变更。

### E. ULTRAPLAN 自定义提示词

```typescript
// src/commands/ultraplan.tsx — ant-only
const ULTRAPLAN_INSTRUCTIONS: string =
  "external" === 'ant' && process.env.ULTRAPLAN_PROMPT_FILE
    ? readFileSync(process.env.ULTRAPLAN_PROMPT_FILE, 'utf8').trimEnd()
    : DEFAULT_INSTRUCTIONS
```

Ant 用户可以通过文件覆盖 ULTRAPLAN 的提示词——快速迭代规划指令。

---

## 4. 多入口检测

```typescript
// src/entrypoints/cli.tsx
process.env.CLAUDE_CODE_ENTRYPOINT
// 可能的值：
// 'cli'           — 命令行
// 'sdk-ts'        — TypeScript SDK
// 'sdk-py'        — Python SDK
// 'claude-desktop' — Claude Desktop
// 'remote'        — 远程会话
// 'local-agent'   — 本地代理
// 'github-actions' — GitHub Actions
```

不同入口有不同的行为——比如 `claude-desktop` 入口可能跳过某些终端特定功能。

---

## 5. Bare 模式——极简运行

```typescript
// src/utils/envUtils.ts
/**
 * --bare / CLAUDE_CODE_SIMPLE:
 * 跳过 hooks, LSP, plugin sync, skill dir-walk, attribution,
 * background prefetches, 以及所有 keychain/credential 读取。
 * 认证仅通过 ANTHROPIC_API_KEY 或 apiKeyHelper。
 * 代码库中约 ~30 处检查。
 */
export function isBareMode(): boolean {
  return isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE) || process.argv.includes('--bare')
}
```

Bare 模式关闭约 30 个功能以实现最快启动。`process.argv.includes('--bare')` 直接检查命令行参数——因为某些门控（如 `startKeychainPrefetch()`）在 `main.tsx` 顶层就运行了，那时 Commander.js 还没解析参数。

---

## 6. 终端模拟器检测

```typescript
// 检测的终端列表
ALACRITTY_LOG     // Alacritty
ITERM_SESSION_ID  // iTerm2
KITTY_WINDOW_ID   // Kitty
KONSOLE_VERSION   // KDE Konsole
TILIX_ID          // Tilix
VTE_VERSION       // GNOME VTE
XTERM_VERSION     // xterm
ZED_TERM          // Zed editor
ConEmu*           // ConEmu (Windows)
GNOME_TERMINAL_SERVICE
WSL_DISTRO_NAME   // WSL 检测
```

不同终端有不同的能力（颜色深度、Unicode 支持、鼠标事件等）。

---

## 7. CI/CD 平台检测

```typescript
GITHUB_ACTIONS, GITHUB_REPOSITORY, GITHUB_ACTOR
GITLAB_CI
GITPOD_WORKSPACE_ID
RAILWAY_*
FLY_*
DENO_DEPLOYMENT_ID
CF_PAGES           // Cloudflare Pages
BUILDKITE
NETLIFY
VERCEL
RENDER
AZURE_FUNCTIONS_ENVIRONMENT
AWS_LAMBDA_FUNCTION_NAME
```

Claude Code 知道自己运行在哪个 CI/CD 平台上，可能会调整行为（比如跳过交互式提示）。

---

## 8. CLAUDE_CODE_* 关键变量精选

| 变量 | 用途 | 有趣程度 |
|------|------|---------|
| `CLAUDE_CODE_COORDINATOR_MODE=1` | 启用多代理协调 | ★★★★★ |
| `CLAUDE_CODE_UNDERCOVER` | 隐身模式（隐藏 AI 身份） | ★★★★★ |
| `CLAUDE_CODE_EAGER_FLUSH` | 强制立即刷新输出 | ★★★ |
| `CLAUDE_CODE_IS_COWORK` | 协作模式 | ★★★★ |
| `CLAUDE_CODE_OVERRIDE_DATE` | 覆盖日期（测试时间相关功能） | ★★★ |
| `CLAUDE_CODE_CLIENT_CERT` | mTLS 客户端证书 | ★★★ |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | 上下文窗口上限 | ★★★★ |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | 自动压缩窗口 | ★★★ |
| `CLAUDE_CODE_IDLE_THRESHOLD_MINUTES` | 空闲阈值 | ★★★ |
| `CLAUDE_CODE_ENVIRONMENT_RUNNER_VERSION` | 环境运行器版本兼容检查 | ★★ |
| `CLAUDE_CODE_SESSION_KIND` | 会话类型（interactive/bg/daemon） | ★★★★ |

---

## 9. 会话级环境变量

```typescript
// src/utils/sessionEnvVars.ts
const sessionEnvVars = new Map<string, string>()

export function setSessionEnvVar(name: string, value: string): void {
  sessionEnvVars.set(name, value)
}
```

通过 `/env` 命令设置的变量只影响子进程，不影响 REPL 自身——这是一个安全隔离设计。

---

## 设计哲学总结

1. **安全分层**：Safe/Dangerous 分类 + 供应商路由保护，防止恶意 settings.json
2. **可观测到极致**：22 个 OTEL 变量 + SWE-Bench 追踪 + 遥测，Anthropic 对 Claude Code 的性能数据非常重视
3. **平台无关**：同时支持 AWS Bedrock、Google Vertex、Azure，10+ CI/CD 平台检测
4. **VCR 不是玩具**：路径脱水 + SHA1 索引，这是工业级的 API 测试基础设施
5. **~30 处 Bare 模式检查**：说明他们认真考虑了"最小可运行"的场景
