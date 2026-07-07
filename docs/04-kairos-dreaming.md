# Topic 4: KAIROS — AI 在你睡觉时"做梦"

## TLDR

KAIROS 是一个**持久化助手模式**，其核心子系统 **autoDream** 实现了跨会话记忆整合。它在后台自动运行：每隔 24 小时、且积累 5+ 会话后，自动启动一个受限的"做梦"代理，扫描近期会话记录，将碎片化记忆整合写入持久化 memory 文件。还有手动 `/dream` 技能可随时触发。助手模式下日志写入按日期组织的 `logs/YYYY/MM/YYYY-MM-DD.md`，夜间 dream 进程负责蒸馏为主题文件 + MEMORY.md 索引。

---

## 1. autoDream 自动做梦——六道门的触发机制

核心实现在 `src/services/autoDream/autoDream.ts`。最精妙的设计是**六道门槛按成本从低到高排列**，绝大多数调用在前两道就返回了，几乎零开销：

| 门 | 检查 | 成本 |
|----|------|------|
| 1. Feature | `isAutoDreamEnabled()` | 读 GrowthBook 缓存 |
| 2. Location | 不在 KAIROS 模式、不在远程模式、autoMemory 启用 | 状态变量检查 |
| 3. Time | 距上次整合 ≥ 24 小时 | 1 次 `stat()` 读锁文件 mtime |
| 4. Throttle | 距上次会话扫描 ≥ 10 分钟 | 1 次时间比较 |
| 5. Sessions | 期间有 ≥ 5 个会话被触碰 | `listCandidates()` 扫描 |
| 6. Lock | PID 锁获取成功 | 锁文件 I/O |

全部通过后才会启动 forked 子代理执行梦境。

### 配置来源

```typescript
const DEFAULTS: AutoDreamConfig = {
  minHours: 24,
  minSessions: 5,
}

function getConfig(): AutoDreamConfig {
  const raw = getFeatureValue_CACHED_MAY_BE_STALE<Partial<AutoDreamConfig> | null>(
    'tengu_onyx_plover',  // GrowthBook 远程 feature flag
    null,
  )
  // 防御性验证：GB 缓存可能 stale 或类型错误
  return {
    minHours: typeof raw?.minHours === 'number' && Number.isFinite(raw.minHours)
      ? raw.minHours : DEFAULTS.minHours,
    minSessions: typeof raw?.minSessions === 'number' && Number.isFinite(raw.minSessions)
      ? raw.minSessions : DEFAULTS.minSessions,
  }
}
```

### 扫描节流——为什么需要 10 分钟间隔

当时间门通过但会话门失败时，锁文件 mtime 不会更新。如果不节流，时间门会在之后的每一轮对话都通过，导致反复执行昂贵的 `listSessionsTouchedSince()`：

```typescript
const SESSION_SCAN_INTERVAL_MS = 10 * 60 * 1000  // 10 分钟

const sinceScanMs = Date.now() - lastSessionScanAt
if (!force && sinceScanMs < SESSION_SCAN_INTERVAL_MS) {
  logForDebugging(
    `[autoDream] scan throttle — time-gate passed but last scan was ${Math.round(sinceScanMs / 1000)}s ago`,
  )
  return
}
lastSessionScanAt = Date.now()
```

### 进度追踪——工具使用监听器

做梦代理的每条消息都被拦截，追踪它修改了哪些文件：

```typescript
function makeDreamProgressWatcher(taskId, setAppState) {
  return msg => {
    if (msg.type !== 'assistant') return
    let text = ''
    let toolUseCount = 0
    const touchedPaths: string[] = []
    for (const block of msg.message.content) {
      if (block.type === 'text') text += block.text
      else if (block.type === 'tool_use') {
        toolUseCount++
        if (block.name === FILE_EDIT_TOOL_NAME || block.name === FILE_WRITE_TOOL_NAME) {
          const input = block.input as { file_path?: unknown }
          if (typeof input.file_path === 'string') touchedPaths.push(input.file_path)
        }
      }
    }
    addDreamTurn(taskId, { text: text.trim(), toolUseCount }, touchedPaths, setAppState)
  }
}
```

注释特别标注：`filesTouched` 是**不完整反映**——漏掉 bash 中的写入操作。当作"至少改了这些"而非"只改了这些"。

---

## 2. PID 锁——带竞态安全的跨进程锁

定义在 `src/services/autoDream/consolidationLock.ts`。

### 核心不变量

锁文件 `.consolidate-lock` 的 **mtime = 上次整合完成时间**。文件内容 = 当前持有者的 PID。

```typescript
const LOCK_FILE = '.consolidate-lock'
const HOLDER_STALE_MS = 60 * 60 * 1000  // PID 1 小时后判定为 stale
```

### 读取上次整合时间——每轮对话的固定开销

```typescript
export async function readLastConsolidatedAt(): Promise<number> {
  try {
    const s = await stat(lockPath())
    return s.mtimeMs  // mtime 就是整合时间戳
  } catch {
    return 0  // 没有锁文件 = 从未整合
  }
}
```

只需一次 `stat()` 调用。

### 锁获取——处理竞态的完整逻辑

```typescript
export async function tryAcquireConsolidationLock(): Promise<number | null> {
  const path = lockPath()

  // 并行读取锁状态
  let mtimeMs: number | undefined
  let holderPid: number | undefined
  try {
    const [s, raw] = await Promise.all([stat(path), readFile(path, 'utf8')])
    mtimeMs = s.mtimeMs
    const parsed = parseInt(raw.trim(), 10)
    holderPid = Number.isFinite(parsed) ? parsed : undefined
  } catch { /* ENOENT — 无锁 */ }

  // Stale 检查：锁是否新鲜 AND 持有者是否存活？
  if (mtimeMs !== undefined && Date.now() - mtimeMs < HOLDER_STALE_MS) {
    if (holderPid !== undefined && isProcessRunning(holderPid)) {
      return null  // 被阻塞
    }
    // 死 PID 或无法解析 → 回收
  }

  // 获取：写入我们的 PID
  await mkdir(getAutoMemPath(), { recursive: true })
  await writeFile(path, String(process.pid))

  // 验证竞态胜出
  let verify: string
  try { verify = await readFile(path, 'utf8') }
  catch { return null }  // 竞态失败
  if (parseInt(verify.trim(), 10) !== process.pid) return null  // 竞态失败

  return mtimeMs ?? 0  // 返回 prior mtime 用于失败回滚
}
```

**竞态解决方案**：两个进程可能同时检测到 stale 锁，同时写入 PID。最后写入的赢；输家重新读取并退出。函数返回 prior mtime 以便失败时回滚。

### 失败回滚

```typescript
export async function rollbackConsolidationLock(priorMtime: number): Promise<void> {
  const path = lockPath()
  try {
    if (priorMtime === 0) {
      await unlink(path)  // 恢复"无文件"状态
      return
    }
    await writeFile(path, '')  // 清空 PID
    const t = priorMtime / 1000  // utimes 需要秒而非毫秒
    await utimes(path, t, t)    // 回滚 mtime
  } catch (e) {
    logForDebugging(`[autoDream] rollback failed — next trigger delayed to minHours`)
  }
}
```

做梦代理崩溃后，把 mtime 倒回去，这样时间门下次还能通过。

---

## 3. 整合提示词——做梦代理的完整指令

定义在 `src/services/autoDream/consolidationPrompt.ts`。这是发给做梦代理的**完整提示词**：

```
# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files. Synthesize
what you've learned recently into durable, well-organized memories so that future
sessions can orient quickly.

Memory directory: `{memoryRoot}`
Session transcripts: `{transcriptDir}` (large JSONL files — grep narrowly, don't
read whole files)

---

## Phase 1 — Orient
- `ls` the memory directory to see what already exists
- Read `MEMORY.md` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` or `sessions/` subdirectories exist (assistant-mode layout), review
  recent entries there

## Phase 2 — Gather recent signal
Look for new information worth persisting. Sources in rough priority order:
1. **Daily logs** (`logs/YYYY/MM/YYYY-MM-DD.md`) if present
2. **Existing memories that drifted** — facts that contradict something you see now
3. **Transcript search** — grep the JSONL transcripts for narrow terms:
   `grep -rn "<narrow term>" {transcriptDir}/ --include="*.jsonl" | tail -50`

Don't exhaustively read transcripts. Look only for things you already suspect matter.

## Phase 3 — Consolidate
For each thing worth remembering, write or update a memory file. Focus on:
- Merging new signal into existing topic files rather than creating near-duplicates
- Converting relative dates ("yesterday") to absolute dates
- Deleting contradicted facts at the source

## Phase 4 — Prune and index
Update `MEMORY.md` so it stays under 200 lines AND under ~25KB. It's an **index**:
- Each entry one line under ~150 characters: `- [Title](file.md) — one-line hook`
- Never write memory content directly into it
- Remove pointers to stale/wrong/superseded memories
- Resolve contradictions between files
```

做梦代理还收到额外注入的工具约束和会话列表：

```
**Tool constraints for this run:** Bash is restricted to read-only commands
(`ls`, `find`, `grep`, `cat`, `stat`, `wc`, `head`, `tail`, and similar).
Anything that writes, redirects to a file, or modifies state will be denied.

Sessions since last consolidation (7):
- session_abc123
- session_def456
- ...
```

**关键限制**：做梦代理只能用 `Read`、`Edit`、`Write` 工具改 memory 文件，Bash 被限制为只读。这防止它在整合记忆时误改代码。

---

## 4. DreamTask 可视化

定义在 `src/tasks/DreamTask/DreamTask.ts`，做梦过程作为后台任务可视化：

```typescript
export type DreamTaskState = TaskStateBase & {
  type: 'dream'
  phase: DreamPhase                    // 'starting' | 'updating'
  sessionsReviewing: number            // 正在审查的会话数
  filesTouched: string[]               // 已修改的 memory 文件（不完整）
  turns: DreamTurn[]                   // 最近 30 轮对话
  abortController?: AbortController    // 用户可主动终止
  priorMtime: number                   // kill 时回滚用
}
```

Phase 只有两个状态：`starting`（还没改任何文件）和 `updating`（已开始写入 memory 文件）。不追踪 4 阶段提示词中的 orient/gather/consolidate/prune——那是代理自己的事。

用户可以主动 kill：

```typescript
DreamTask.kill(taskId, setAppState)
  → abortController.abort()
  → status = 'killed'
  → rollbackConsolidationLock(priorMtime)  // 回滚锁，下次还能触发
```

---

## 5. 助手模式日志架构

KAIROS 模式下的记忆系统完全不同于普通模式。

### 普通模式

新记忆直接写入 `MEMORY.md` 和主题文件。

### 助手模式（KAIROS）

```typescript
// src/memdir/memdir.ts — KAIROS 门控
function buildAssistantDailyLogPrompt(): string {
  return `# auto memory

You have a persistent, file-based memory system found at: \`${memoryDir}\`

This session is long-lived. As you work, record anything worth remembering by
**appending** to today's daily log file:

\`${logPathPattern}\`

Substitute today's date for YYYY-MM-DD. When the date rolls over mid-session,
start appending to the new day's file.

Write each entry as a short timestamped bullet. Create the file (and parent
directories) on first write if it does not exist. Do not rewrite or reorganize
the log — it is append-only. A separate nightly process distills these logs
into MEMORY.md and topic files.

## What to log
- User corrections and preferences ("use bun, not npm")
- Facts about the user, their role, or their goals
- Project context not derivable from code (deadlines, incidents, decisions)
- Pointers to external systems (dashboards, Linear projects, Slack channels)
- Anything the user explicitly asks you to remember`
}
```

### 日志路径计算

```typescript
// src/memdir/paths.ts
export function getAutoMemDailyLogPath(date: Date = new Date()): string {
  const yyyy = date.getFullYear().toString()
  const mm = (date.getMonth() + 1).toString().padStart(2, '0')
  const dd = date.getDate().toString().padStart(2, '0')
  return join(getAutoMemPath(), 'logs', yyyy, mm, `${yyyy}-${mm}-${dd}.md`)
}
```

结果目录结构：

```
<autoMemPath>/
├── logs/
│   └── 2026/
│       └── 03/
│           ├── 2026-03-29.md   ← 每日追加日志
│           ├── 2026-03-30.md
│           └── 2026-03-31.md
├── user_role.md                ← dream 蒸馏的主题文件
├── feedback_testing.md
└── MEMORY.md                   ← 蒸馏后的索引（≤200 行，≤25KB）
```

**工作流**：
1. 助手会话是持久化长会话
2. 新记忆追加写入每日日志（append-only）
3. **绝不直接编辑 MEMORY.md**
4. 夜间 /dream 或 autoDream 蒸馏日志 → 主题文件 + MEMORY.md 索引
5. MEMORY.md 被截断保护在 200 行 / 25KB 以内

### MEMORY.md 截断保护

```typescript
export const MAX_ENTRYPOINT_LINES = 200
export const MAX_ENTRYPOINT_BYTES = 25_000

export function truncateEntrypointContent(raw: string): EntrypointTruncation {
  // 先按行截断（自然边界）
  // 再按字节截断（在最后一个换行处切割，不切断行）
  // 超出时追加 WARNING
}
```

---

## 6. Memory 路径安全验证

```typescript
// src/memdir/paths.ts — 安全关键
function validateMemoryPath(raw: string | undefined, expandTilde: boolean): string | undefined {
  // 拒绝：相对路径（../foo）
  // 拒绝：根路径或近根路径（/, /a）
  // 拒绝：Windows 驱动器根（C:\）
  // 拒绝：UNC 路径（\\server\share）
  // 拒绝：null 字节
  if (!isAbsolute(normalized) || normalized.length < 3 || normalized.includes('\0'))
    return undefined
  return (normalized + sep).normalize('NFC')
}
```

防止恶意 settings.json 把 memory 目录指向危险位置。

---

## 7. 后台初始化

```typescript
// src/utils/backgroundHousekeeping.ts
export function startBackgroundHousekeeping(): void {
  void initMagicDocs()          // 文档索引
  void initSkillImprovement()   // 技能改进建议
  if (feature('EXTRACT_MEMORIES')) {
    extractMemoriesModule!.initExtractMemories()
  }
  initAutoDream()               // ← 无条件调用，但内部有运行时门控
  // ...
}
```

`initAutoDream()` 无条件调用——它内部的六道门负责判断是否真正执行。延迟 10 分钟启动，避免阻塞用户交互。

---

## 8. /dream 手动技能 vs autoDream

| 维度 | autoDream（自动） | /dream（手动） |
|------|-------------------|----------------|
| 触发 | 后台定时（6 道门） | 用户主动调用 |
| 执行环境 | forked 子代理 | 主循环 |
| Bash 权限 | 只读（ls, grep, cat...） | 完整权限 |
| Feature gate | 无（运行时 GB 门控） | `KAIROS` \|\| `KAIROS_DREAM` |
| 锁机制 | 共享 `.consolidate-lock` | 共享 `.consolidate-lock` |
| 打戳时机 | fork 成功后 | prompt 构建时（乐观锁） |

---

## 设计哲学总结

autoDream 的设计体现了几个原则：

1. **零成本抽象**：六道门按成本排序，99% 的调用在前两道就返回
2. **优雅降级**：fork 崩溃后回滚 mtime，不影响下次触发
3. **安全第一**：做梦代理 Bash 只读，不能误改代码
4. **防竞态**：PID 锁 + 写后验证，两个进程同时做梦不会冲突
5. **可观测**：DreamTask 可视化让用户看到做梦进度，可随时 kill
