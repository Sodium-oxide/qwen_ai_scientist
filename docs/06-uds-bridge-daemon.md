# Topic 6: UDS Inbox / Bridge / Daemon — 多会话互联与远程控制

## TLDR

三大系统组成完整的分布式会话架构。**UDS Inbox** 通过 Unix Domain Socket 让同一台机器上的多个 Claude 会话互相通信。**Bridge Mode** 实现 VS Code/JetBrains 桥接和 Web/手机远程控制——轮询式工作队列 + WebSocket 双向通信 + JWT 认证自动续期，完整实现了"手机扫码操控终端 Claude"。**Daemon Mode** 是无头守护进程，通过 IPC 与 supervisor 通信，处理多并发会话。三者共享会话注册机制（PID 文件），通过统一地址解析（`uds:<path>` / `bridge:<session_id>`）实现跨进程/跨网络消息路由。

---

## 1. Bridge 协议——从注册到心跳的完整生命周期

定义在 `src/bridge/bridgeApi.ts`。这是一个基于 HTTP 轮询的工作队列：

### 协议流程

```
1. POST /v1/environments/bridge               → 注册环境（返回 environment_id + secret）
2. GET  /v1/environments/{id}/work/poll        → 轮询工作项（返回 WorkResponse + JWT）
3. POST /v1/environments/{id}/work/{wid}/ack   → 确认接手工作
4. WebSocket 双向通信                           → 执行工具、同步状态
5. POST /v1/environments/{id}/work/{wid}/heartbeat → 延续租约
6. POST /v1/sessions/{sid}/archive             → 归档完成的会话
7. POST /v1/environments/{id}/bridge/reconnect → JWT 过期时重新分发
8. DELETE /v1/environments/bridge/{id}         → 注销环境
```

### WorkSecret——工作项中的秘密

```typescript
export type WorkSecret = {
  version: number
  session_ingress_token: string    // JWT，用于后续所有会话级操作
  api_base_url: string
  sources: Array<{
    type: string
    git_info?: { type: string; repo: string; ref?: string; token?: string }
  }>
  auth: Array<{ type: string; token: string }>
  claude_code_args?: Record<string, string> | null
  mcp_config?: unknown | null
  environment_variables?: Record<string, string> | null
  use_code_sessions?: boolean      // CCR v2 选择器
}
```

工作项通过 base64url 编码的 `secret` 字段传递 JWT 和认证信息。

### 三层认证

| 层 | Token | 用途 |
|----|-------|------|
| 1. OAuth | 用户 OAuth token | 注册环境 |
| 2. Environment Secret | 注册时返回的密钥 | 轮询工作项 |
| 3. Session JWT | 工作项中的 ingress token | 心跳、确认、归档 |

---

## 2. JWT 自动续期——5 分钟提前量

定义在 `src/bridge/jwtUtils.ts`：

```typescript
const TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000      // 过期前 5 分钟刷新
const FALLBACK_REFRESH_INTERVAL_MS = 30 * 60 * 1000 // 30 分钟 fallback 刷新

export function createTokenRefreshScheduler({ getAccessToken, onRefresh, label }) {
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const failureCounts = new Map<string, number>()
  const generations = new Map<string, number>()  // 代次计数器防止 stale 刷新

  function schedule(sessionId: string, token: string): void {
    const expiry = decodeJwtExpiry(token)  // 解码 JWT 的 exp claim
    if (!expiry) return

    const gen = nextGeneration(sessionId)
    const delayMs = expiry * 1000 - Date.now() - refreshBufferMs

    if (delayMs <= 0) {
      // 已过期或在缓冲区内——立即刷新
      void doRefresh(sessionId, gen)
      return
    }

    const timer = setTimeout(doRefresh, delayMs, sessionId, gen)
    timers.set(sessionId, timer)
  }

  async function doRefresh(sessionId: string, gen: number): Promise<void> {
    // 代次检查——session 可能已被取消/重调度
    if (generations.get(sessionId) !== gen) return

    const oauthToken = await getAccessToken()
    if (!oauthToken) {
      const failures = (failureCounts.get(sessionId) ?? 0) + 1
      failureCounts.set(sessionId, failures)
      if (failures < 3) {
        setTimeout(doRefresh, 60_000, sessionId, gen)  // 1 分钟后重试
      }
      return
    }

    onRefresh(sessionId, oauthToken)
    // 安排后续刷新（长会话保持认证）
    setTimeout(doRefresh, FALLBACK_REFRESH_INTERVAL_MS, sessionId, gen)
  }
}

// 无签名验证的 JWT 解码
export function decodeJwtExpiry(token: string): number | null {
  const jwt = token.startsWith('sk-ant-si-') ? token.slice('sk-ant-si-'.length) : token
  const parts = jwt.split('.')
  if (parts.length !== 3 || !parts[1]) return null
  const payload = jsonParse(Buffer.from(parts[1], 'base64url').toString('utf8'))
  return typeof payload?.exp === 'number' ? payload.exp : null
}
```

**代次（generation）机制**：防止 "刷新 A 的 token → A 被取消 → 刷新回调触发时 A 已不存在" 的竞态。每次 schedule 递增代次，doRefresh 执行前检查代次是否匹配。

---

## 3. 消息路由——Echo 过滤与控制协议

定义在 `src/bridge/bridgeMessaging.ts`：

### 环形缓冲区去重

```typescript
export class BoundedUUIDSet {
  private readonly ring: (string | undefined)[]
  private readonly set = new Set<string>()
  private writeIdx = 0

  constructor(capacity: number) {
    this.ring = new Array<string | undefined>(capacity)
  }

  add(uuid: string): void {
    if (this.set.has(uuid)) return
    const evicted = this.ring[this.writeIdx]
    if (evicted !== undefined) this.set.delete(evicted)
    this.ring[this.writeIdx] = uuid
    this.set.add(uuid)
    this.writeIdx = (this.writeIdx + 1) % this.capacity
  }

  has(uuid: string): boolean { return this.set.has(uuid) }
}
```

两个 `BoundedUUIDSet`：
- `recentPostedUUIDs`: 过滤自己发出消息的回声（WebSocket 全双工会收到自己的消息）
- `recentInboundUUIDs`: 过滤 SSE 历史重放时重复投递的入站消息

### 入站消息路由

```typescript
export function handleIngressMessage(
  data: string,
  recentPostedUUIDs: BoundedUUIDSet,
  recentInboundUUIDs: BoundedUUIDSet,
  onInboundMessage?: (msg: SDKMessage) => void,
  onPermissionResponse?: (response: SDKControlResponse) => void,
  onControlRequest?: (request: SDKControlRequest) => void,
): void {
  const parsed = normalizeControlMessageKeys(jsonParse(data))

  // 控制响应（权限审批结果）
  if (isSDKControlResponse(parsed)) { onPermissionResponse?.(parsed); return }

  // 服务器控制请求（initialize, set_model, interrupt 等）
  if (isSDKControlRequest(parsed)) { onControlRequest?.(parsed); return }

  if (!isSDKMessage(parsed)) return

  // UUID 回声过滤
  const uuid = parsed.uuid
  if (uuid && recentPostedUUIDs.has(uuid)) return   // 自己发的
  if (uuid && recentInboundUUIDs.has(uuid)) return   // 重复投递

  if (parsed.type === 'user') {
    if (uuid) recentInboundUUIDs.add(uuid)
    void onInboundMessage?.(parsed)
  }
}
```

### 服务器控制请求处理

```typescript
// 支持的控制请求类型
switch (request.request.subtype) {
  case 'initialize':          // 返回能力列表（commands, models, account, pid）
  case 'interrupt':           // 中断当前操作
  case 'set_model':           // 切换模型
  case 'set_permission_mode': // 切换权限模式
}

// 单向模式（outbound-only）时拒绝可变请求
if (outboundOnly && request.request.subtype !== 'initialize') {
  response = { type: 'control_response', response: {
    subtype: 'error', error: 'This session is outbound-only.'
  }}
}
```

---

## 4. 双版本传输层

定义在 `src/bridge/replBridgeTransport.ts`：

```typescript
export type ReplBridgeTransport = {
  write(message: StdoutMessage): Promise<void>
  writeBatch(messages: StdoutMessage[]): Promise<void>
  close(): void
  isConnectedStatus(): boolean
  setOnData(callback: (data: string) => void): void
  setOnClose(callback: (closeCode?: number) => void): void
  setOnConnect(callback: () => void): void
  connect(): void
  getLastSequenceNum(): number         // SSE 序列号（v2 only）
  reportState(state: SessionState): void  // v2 only
  flush(): Promise<void>               // v2 only
}
```

| 版本 | 读 | 写 | 特点 |
|------|----|----|------|
| v1 | WebSocket | WebSocket → Session Ingress | 全双工 |
| v2 | SSE 事件流 | CCRClient HTTP POST | 支持断点续传（sequence number） |

v2 的 SSE 序列号允许断线重连后从上次断点继续，不丢消息。

---

## 5. UDS Inbox——本地 Socket 通信

Feature flag: `UDS_INBOX`

### 初始化

```typescript
// src/setup.ts
if (!isBareMode() || messagingSocketPath !== undefined) {
  if (feature('UDS_INBOX')) {
    const m = await import('./utils/udsMessaging.js')
    await m.startUdsMessaging(
      messagingSocketPath ?? m.getDefaultUdsSocketPath(),
      { isExplicit: messagingSocketPath !== undefined },
    )
  }
}
```

### 地址解析

```typescript
// src/utils/peerAddress.ts
export function parseAddress(to: string): { scheme: 'uds'|'bridge'|'other'; target: string } {
  if (to.startsWith('uds:'))    return { scheme: 'uds', target: to.slice(4) }
  if (to.startsWith('bridge:')) return { scheme: 'bridge', target: to.slice(7) }
  if (to.startsWith('/'))       return { scheme: 'uds', target: to }  // Legacy 裸路径
  return { scheme: 'other', target: to }
}
```

三种寻址，统一 `SendMessageTool` 接口：

```
SendMessage({to: "researcher"})                    → 同 swarm 内
SendMessage({to: "uds:/tmp/cc-socks/1234.sock"})  → 本地其他会话
SendMessage({to: "bridge:session_abc123"})         → 远程会话
SendMessage({to: "*"})                             → 广播所有队友
```

---

## 6. 会话发现——PID 文件注册

定义在 `src/utils/concurrentSessions.ts`：

```typescript
export type SessionKind = 'interactive' | 'bg' | 'daemon' | 'daemon-worker'

export async function registerSession(): Promise<boolean> {
  const pidFile = join(getSessionsDir(), `${process.pid}.json`)

  await writeFile(pidFile, jsonStringify({
    pid: process.pid,
    sessionId: getSessionId(),
    cwd: getOriginalCwd(),
    startedAt: Date.now(),
    kind: envSessionKind() ?? 'interactive',
    entrypoint: process.env.CLAUDE_CODE_ENTRYPOINT,
    // UDS_INBOX: socket 路径
    ...(feature('UDS_INBOX') ? { messagingSocketPath: process.env.CLAUDE_CODE_MESSAGING_SOCKET } : {}),
    // BG_SESSIONS: 名称、日志、代理信息
    ...(feature('BG_SESSIONS') ? {
      name: process.env.CLAUDE_CODE_SESSION_NAME,
      logPath: process.env.CLAUDE_CODE_SESSION_LOG,
      agent: process.env.CLAUDE_CODE_AGENT,
    } : {}),
  }))
  return true
}
```

**`claude ps` 实现**——扫描 PID 文件 + 检查进程存活：

```typescript
export async function countConcurrentSessions(): Promise<number> {
  const files = await readdir(getSessionsDir())
  let count = 0
  for (const file of files) {
    if (!/^\d+\.json$/.test(file)) continue
    const pid = parseInt(file.slice(0, -5), 10)
    if (pid === process.pid || isProcessRunning(pid)) {
      count++
    } else {
      // Stale 文件——清理（WSL 上跳过，PID 不互通）
      void unlink(join(dir, file)).catch(() => {})
    }
  }
  return count
}
```

---

## 7. Daemon Mode——无头守护进程

Feature flag: `DAEMON`（需同时 `BRIDGE_MODE`）

### 入口

```typescript
// src/bridge/bridgeMain.ts
export async function runBridgeHeadless(): Promise<void> {
  // 无 TUI、无 readline
  // stdin/stdout 替换为 IPC（supervisor 的 AuthManager）
  // 致命错误 → 退出码映射给 supervisor
}
```

### 主循环——工作队列消费者

```typescript
export async function runBridgeLoop(config, environmentId, environmentSecret, api, spawner, ...) {
  const activeSessions = new Map<string, SessionHandle>()
  const capacityWake = createCapacityWake(loopSignal)

  while (Date.now() < deadline) {
    // 1. 心跳：保持现有工作项租约
    const heartbeatResult = await heartbeatActiveWorkItems()
    if (heartbeatResult === 'fatal') break       // 环境被删除
    if (heartbeatResult === 'auth_failed') {
      // JWT 过期 → 触发服务端重新分发
      await api.reconnectSession(environmentId, sessionId)
    }

    // 2. 容量检查：有空位才拉新工作
    if (activeSessions.size >= config.maxSessions) {
      await capacityWake.wait()  // 等到有会话完成
      continue
    }

    // 3. 轮询新工作
    const work = await api.pollForWork(environmentId, environmentSecret, signal)
    if (!work) { await sleep(pollInterval); continue }

    // 4. 解码 WorkSecret，提取 JWT
    const secret = JSON.parse(Buffer.from(work.secret, 'base64url').toString())

    // 5. 创建会话
    const handle = await spawner.spawn(work, secret)
    activeSessions.set(sessionId, handle)

    // 6. 安排 JWT 续期
    tokenRefresh?.schedule(sessionId, secret.session_ingress_token)

    // 7. 注册完成回调
    handle.onDone(onSessionDone(sessionId, startTime, handle))
  }
}
```

### 会话完成处理

```typescript
function onSessionDone(sessionId, startTime, handle) {
  return (rawStatus: SessionDoneStatus) => {
    activeSessions.delete(sessionId)
    tokenRefresh?.cancel(sessionId)

    if (config.spawnMode !== 'single-session') {
      // 多会话模式：归档并继续轮询
      void api.archiveSession(sessionId)
      capacityWake.wake()  // 唤醒容量等待
    } else {
      // 单会话模式：完成后退出
      controller.abort()
    }
  }
}
```

### 超时配置

```typescript
export type BridgeConfig = {
  spawnMode: 'single-session' | 'worktree' | 'same-dir'
  maxSessions: number
  // ...
}
const DEFAULT_SESSION_TIMEOUT_MS = 24 * 60 * 60 * 1000  // 24 小时
```

---

## 8. tmux Socket 隔离

定义在 `src/utils/tmuxSocket.ts`（428 行）：

```typescript
export function getClaudeSocketName(): string {
  if (!socketName) socketName = `${CLAUDE_SOCKET_PREFIX}-${process.pid}`
  return socketName  // 格式：claude-12345
}

// 路径：$TMPDIR/tmux-<UID>/claude-<PID>
// Windows：通过 WSL interop 运行 tmux
```

每个 Claude 进程有独立的 tmux socket，避免干扰用户自己的 tmux 会话。进程退出时自动清理。

---

## 9. 架构总览

```
┌──────────────┐     UDS Socket      ┌──────────────┐
│  Session A    │◄───────────────────►│  Session B    │
│  (Terminal)   │                     │  (Background) │
└──────┬────── ┘                     └──────────────┘
       │
       │  WebSocket / SSE
       │
┌──────▼──────┐     HTTP API
│  claude.ai   │◄──────────────┐
│  Mobile App  │               │
└──────┬──────┘               │
       │                      │
       │  Control Requests     │  Token Refresh
       │                      │
┌──────▼──────┐         ┌─────▼─────┐
│   Daemon     │         │ Supervisor │
│  (Headless)  │◄───────►│  (IPC)     │
│  多并发会话   │         │ AuthManager│
└──────────────┘         └───────────┘
```

---

## 设计哲学总结

1. **三层认证不是过度设计**：OAuth → Environment Secret → Session JWT，每层的泄露影响范围不同
2. **轮询而非推送**：工作队列用 HTTP poll 而非 WebSocket push，更好处理网络中断和重连
3. **代次防竞态**：JWT 刷新的 generation 计数器是一个优雅的并发模式
4. **环形缓冲区去重**：O(1) 查找 + 固定内存，比 Set + TTL 更适合高频消息流
5. **WSL 感知**：tmux socket 清理跳过 WSL（PID 不互通），说明他们认真测试了 Windows
