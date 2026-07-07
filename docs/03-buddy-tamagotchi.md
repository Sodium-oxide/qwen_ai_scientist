# Topic 3: BUDDY — 命令行里的电子宠物

## TLDR

BUDDY 是一个完整的 Tamagotchi 风格 AI 宠物系统，已在源码中完全实现。**18 种物种**（鸭子、美西螈、capybara、chonk 等），**5 种属性**（DEBUGGING、PATIENCE、CHAOS、WISDOM、SNARK），**5 级稀有度**（common 60% → legendary 1%），还有 1% 概率的闪光（shiny）变体。宠物基于用户 ID 确定性生成（无法作弊），彩蛋盐值 `friend-2026-401` 直接暗示 4 月 1 日上线。代码中明确写了**预告窗口为 2026 年 4 月 1-7 日**，之后永久开放。

---

## 1. 物种清单与编码手法

定义在 `src/buddy/types.ts`，所有物种名用十六进制字符编码——这不是混淆，而是为了避免触发模型的 `excluded-strings.txt` 检查（比如 "dragon" 是某个模型的内部代号）：

```typescript
const c = String.fromCharCode
export const duck     = c(0x64,0x75,0x63,0x6b) as 'duck'
export const dragon   = c(0x64,0x72,0x61,0x67,0x6f,0x6e) as 'dragon'
export const axolotl  = c(0x61,0x78,0x6f,0x6c,0x6f,0x74,0x6c) as 'axolotl'
export const chonk    = c(0x63,0x68,0x6f,0x6e,0x6b) as 'chonk'
// ... 共 18 种
```

**完整 18 种物种：** duck, goose, blob, cat, dragon, octopus, owl, penguin, turtle, snail, ghost, axolotl, capybara, cactus, robot, rabbit, mushroom, chonk

---

## 2. 稀有度与属性系统

### 稀有度权重

```typescript
export const RARITY_WEIGHTS = {
  common: 60,     // ★
  uncommon: 25,   // ★★
  rare: 10,       // ★★★
  epic: 4,        // ★★★★
  legendary: 1,   // ★★★★★
} as const
```

总权重 100，用加权随机选取。legendary 只有 1% 概率。

### 稀有度对应颜色

```typescript
export const RARITY_COLORS = {
  common: 'inactive',
  uncommon: 'success',
  rare: 'permission',
  epic: 'autoAccept',
  legendary: 'warning',
} as const
```

### 外观随机池

```typescript
export const EYES = ['·', '✦', '×', '◉', '@', '°'] as const
export const HATS = ['none','crown','tophat','propeller','halo','wizard','beanie','tinyduck'] as const
```

注意 `tinyduck`——帽子上站一只迷你鸭子。**只有 uncommon 及以上才有帽子**，common 锁定 `none`：

```typescript
hat: rarity === 'common' ? 'none' : pick(rng, HATS),
```

### 属性（Stats）生成算法

```typescript
const STAT_NAMES = ['DEBUGGING', 'PATIENCE', 'CHAOS', 'WISDOM', 'SNARK'] as const

const RARITY_FLOOR: Record<Rarity, number> = {
  common: 5, uncommon: 15, rare: 25, epic: 35, legendary: 50,
}

function rollStats(rng: () => number, rarity: Rarity): Record<StatName, number> {
  const floor = RARITY_FLOOR[rarity]
  const peak = pick(rng, STAT_NAMES)           // 随机选一个峰值属性
  let dump = pick(rng, STAT_NAMES)
  while (dump === peak) dump = pick(rng, STAT_NAMES)  // 随机选一个短板，不能和峰值相同

  const stats = {} as Record<StatName, number>
  for (const name of STAT_NAMES) {
    if (name === peak) {
      stats[name] = Math.min(100, floor + 50 + Math.floor(rng() * 30))  // 峰值：50-100
    } else if (name === dump) {
      stats[name] = Math.max(1, floor - 10 + Math.floor(rng() * 15))    // 短板：1-25
    } else {
      stats[name] = floor + Math.floor(rng() * 40)                      // 其余：散落
    }
  }
  return stats
}
```

也就是说：每只宠物必有一个突出属性（至少 50 分），一个明显短板，其余三项分散。一个 legendary 的短板可能还比 common 的峰值高。

---

## 3. 确定性生成——为什么无法作弊

核心在 `src/buddy/companion.ts`。

### Mulberry32 伪随机数生成器

```typescript
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
```

小巧的 seeded PRNG，代码注释写的是 "good enough for picking ducks"（足够用来选鸭子了）。

### 种子计算

```typescript
const SALT = 'friend-2026-401'  // 401 = April 1st 彩蛋！

function roll(userId: string): Roll {
  const key = userId + SALT
  const value = rollFrom(mulberry32(hashString(key)))
  return value
}
```

`hashString` 优先用 Bun.hash（性能好），fallback 到 FNV-1a 哈希。种子 = `userId + 'friend-2026-401'` 的哈希值。

### Bones vs Soul 分离——防作弊核心

```typescript
// Bones（外观/属性）= 确定性，每次从 userId 重新计算
// Soul（名字/性格）= 仅生成一次，持久化存储

export type CompanionBones = {
  rarity: Rarity; species: Species; eye: Eye; hat: Hat;
  shiny: boolean; stats: Record<StatName, number>
}

export type StoredCompanion = CompanionSoul & { hatchedAt: number }
// 只有 Soul 和 hatchedAt 写入 config，Bones 不存！
```

```typescript
export function getCompanion(): Companion | undefined {
  const stored = getGlobalConfig().companion
  if (!stored) return undefined
  const { bones } = roll(companionUserId())
  return { ...stored, ...bones }  // bones 覆盖任何 stale 字段
}
```

**关键设计**：编辑 `~/.claude/config.json` 中的 companion 字段只能改名字和性格（Soul），稀有度/物种/属性（Bones）全部从 userId 实时重算。即使手动把 rarity 写成 "legendary"，下次读取也会被覆盖。而且 `shiny: rng() < 0.01`（1% 闪光概率）也是从 userId 确定性推导的。

---

## 4. ASCII 艺术精灵系统

定义在 `src/buddy/sprites.ts`。每个物种 3 帧动画，每帧 5 行 × 12 字符宽，`{E}` 是眼睛占位符：

```typescript
// ===== DUCK（鸭子）=====
[
  ['            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--´    '],  // 休息
  ['            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--´~   '],  // 尾巴摆
  ['            ', '    __      ', '  <({E} )___  ', '   (  .__>  ', '    `--´    '],  // 嘴张开
]

// ===== CAT（猫）=====
[
  ['            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")   '],
  ['            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")~  '],  // 尾巴摆
  ['            ', '   /\\-/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")   '],  // 眯眼
]

// ===== DRAGON（龙）=====
[
  ['            ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (   ~~   ) ', '  `-vvvv-´  '],
  ['            ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (        ) ', '  `-vvvv-´  '],
  ['   ~    ~   ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (   ~~   ) ', '  `-vvvv-´  '],  // 冒泡
]

// ===== ROBOT（机器人）=====
[
  ['            ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ ==== ]  ', '  `------´  '],
  ['            ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ -==- ]  ', '  `------´  '],
  ['     *      ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ ==== ]  ', '  `------´  '],  // 头顶火花
]
```

### 帽子渲染

帽子替换第一行的空行：

```typescript
const HAT_LINES: Record<Hat, string> = {
  none: '',
  crown:    '   \\^^^/    ',
  tophat:   '   [___]    ',
  propeller:'    -+-     ',
  halo:     '   (   )    ',
  wizard:   '    /^\\     ',
  beanie:   '   (___)    ',
  tinyduck: '    ,>      ',  // 一只迷你鸭子！
}
```

### 单行表情（窄终端 fallback）

当终端宽度 < 100 列时，折叠为单行：

```typescript
export function renderFace(bones: CompanionBones): string {
  switch (bones.species) {
    case duck:     return `(${eye}>`
    case cat:      return `=${eye}ω${eye}=`
    case dragon:   return `<${eye}~${eye}>`
    case octopus:  return `~(${eye}${eye})~`
    case axolotl:  return `}${eye}.${eye}{`
    case capybara: return `(${eye}oo${eye})`
    case chonk:    return `(${eye}.${eye})`
    // ...
  }
}
```

---

## 5. 动画系统详解

定义在 `src/buddy/CompanionSprite.tsx`。

### 动画常量

```typescript
const TICK_MS = 500          // 每帧 500ms
const BUBBLE_SHOW = 20       // 语音泡泡显示 20 tick ≈ 10 秒
const FADE_WINDOW = 6        // 最后 6 tick ≈ 3 秒渐隐
const PET_BURST_MS = 2500    // /buddy pet 爱心动画 2.5 秒
```

### 待机序列

```typescript
// 大部分时间是休息帧（0），偶尔小动作（1,2），罕见眨眼（-1）
const IDLE_SEQUENCE = [0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0]
```

`-1` 表示眨眼——用 `-` 替换眼睛字符：

```typescript
const body = renderSprite(companion, spriteFrame).map(line =>
  blink ? line.replaceAll(companion.eye, '-') : line
)
```

### 爱心动画

```typescript
const H = figures.heart
const PET_HEARTS = [
  `   ${H}    ${H}   `,      // 2 颗心
  `  ${H}  ${H}   ${H}  `,   // 3 颗心
  ` ${H}   ${H}  ${H}   `,   // 扩散
  `${H}  ${H}      ${H} `,   // 远散
  '·    ·   ·  '              // 消散为点
]
```

### 状态驱动动画切换

```typescript
if (reaction || petting) {
  // 兴奋：快速循环所有帧
  spriteFrame = tick % frameCount
} else {
  // 安静：用 IDLE_SEQUENCE 控制帧
  const step = IDLE_SEQUENCE[tick % IDLE_SEQUENCE.length]!
  if (step === -1) { spriteFrame = 0; blink = true }
  else { spriteFrame = step % frameCount }
}
```

有反应或被撸时，动画加速循环全部帧；安静时走 15 帧的 IDLE_SEQUENCE。

### 语音泡泡

```typescript
function SpeechBubble({ text, color, fading, tail }) {
  // 圆角边框，最大宽度 34 字符，自动换行
  // fading 时边框和文字变 dim
  // tail 决定尾巴方向：'right'（内联在精灵旁边）或 'down'（全屏浮动在上方）
}
```

- 全屏模式：泡泡通过 `FullscreenLayout` 的 `bottomFloat` 槽渲染，不遮挡主内容
- 内联模式：泡泡在精灵左侧，尾巴指向右边

---

## 6. 上线时间窗口——故意跨时区分散

```typescript
// src/buddy/useBuddyNotification.tsx

// Local date, not UTC — 24h rolling wave across timezones. Sustained Twitter
// buzz instead of a single UTC-midnight spike, gentler on soul-gen load.
// Teaser window: April 1-7, 2026 only. Command stays live forever after.
export function isBuddyTeaserWindow(): boolean {
  const d = new Date()
  return d.getFullYear() === 2026 && d.getMonth() === 3 && d.getDate() <= 7
}

export function isBuddyLive(): boolean {
  const d = new Date()
  return d.getFullYear() > 2026 || (d.getFullYear() === 2026 && d.getMonth() >= 3)
}
```

注释说得很清楚：故意用本地时区而不是 UTC，让全球用户在不同时间点发现这个彩蛋，制造"24 小时滚动热度"而不是 UTC 午夜的单次爆发。还特意提到要对 "soul-gen load"（由 LLM 生成宠物名字/性格的负载）更温和。

### 预告通知

```typescript
export function useBuddyNotification() {
  useEffect(() => {
    if (!feature('BUDDY')) return
    const config = getGlobalConfig()
    if (config.companion || !isBuddyTeaserWindow()) return  // 已有宠物就不提示

    addNotification({
      key: "buddy-teaser",
      jsx: <RainbowText text="/buddy" />,   // 彩虹色 /buddy 文字！
      priority: "immediate",
      timeoutMs: 15000,  // 显示 15 秒
    })
  }, [])
}
```

只在 4 月 1-7 日、且用户还没有宠物时显示。彩虹色渲染：

```typescript
function RainbowText({ text }: { text: string }): React.ReactNode {
  return <>
    {[...text].map((ch, i) => (
      <Text key={i} color={getRainbowColor(i)}>{ch}</Text>
    ))}
  </>
}
```

---

## 7. LLM 如何认识你的宠物

定义在 `src/buddy/prompt.ts`，系统提示词注入：

```typescript
export function companionIntroText(name: string, species: string): string {
  return `# Companion

A small ${species} named ${name} sits beside the user's input box and
occasionally comments in a speech bubble. You're not ${name} — it's a
separate watcher.

When the user addresses ${name} directly (by name), its bubble will answer.
Your job in that moment is to stay out of the way: respond in ONE line or
less, or just answer any part of the message meant for you. Don't explain
that you're not ${name} — they know. Don't narrate what ${name} might say
— the bubble handles that.`
}
```

Claude 被告知：你不是宠物，宠物是独立的观察者。用户叫宠物名字时你别抢话。不要解释"我不是它"——用户知道。

### 首次介绍附件

```typescript
export function getCompanionIntroAttachment(messages: Message[]): Attachment[] {
  // 检查是否已经介绍过这只宠物
  for (const msg of messages ?? []) {
    if (msg.attachment?.type === 'companion_intro' && msg.attachment.name === companion.name)
      return []  // 已介绍过
  }
  // 首次：发送介绍附件
  return [{ type: 'companion_intro', name: companion.name, species: companion.species }]
}
```

---

## 8. 持久化与配置

```typescript
// src/utils/config.ts
companion?: import('../buddy/types.js').StoredCompanion  // Soul + hatchedAt
companionMuted?: boolean  // 静音宠物

// src/state/AppStateStore.ts
footerSelection: FooterItem | null    // 可选 'companion'
companionReaction?: string            // 最新反应文字
companionPetAt?: number               // 上次 /buddy pet 时间戳
```

只存 Soul 不存 Bones，这是整个防作弊设计的根基。config 中的 companion 长这样：

```json
{
  "companion": {
    "name": "Quackers",
    "personality": "A chaotic duck who loves debugging and hates waiting",
    "hatchedAt": 1711929600000
  }
}
```

即使手动编辑这个 JSON，也只能改名字和性格——物种、稀有度、属性、帽子、眼睛全部从你的 userId 实时算出来。

---

## 9. 终端布局集成

```typescript
// 宠物占据的终端列数计算
export function companionReservedColumns(terminalColumns: number, speaking: boolean): number {
  if (!feature('BUDDY')) return 0
  if (!companion || companionMuted) return 0
  if (terminalColumns < MIN_COLS_FOR_FULL_SPRITE) return 0  // < 100 列不占空间
  const nameWidth = stringWidth(companion.name)
  const bubble = speaking && !isFullscreenActive() ? BUBBLE_WIDTH : 0  // 说话时额外 36 列
  return spriteColWidth(nameWidth) + SPRITE_PADDING_X + bubble
}
```

Footer 栏按 Enter 直接执行 `/buddy`：

```typescript
case 'companion':
  if (feature('BUDDY')) {
    selectFooterItem(null)
    void onSubmit('/buddy')
  }
```

---

## 设计哲学总结

BUDDY 不是随便做的彩蛋，而是一个完整的产品特性：

1. **防作弊**：Bones/Soul 分离 + userId 确定性生成，比大多数游戏的 gacha 系统更严谨
2. **发布策略**：跨时区滚动预告 + 负载分散，显然预期会有大量用户同时使用
3. **终端适配**：100 列以上全精灵 + 泡泡，窄终端自动折叠为表情，甚至计算预留列数不影响输入框
4. **LLM 集成**：系统提示词让 Claude 知道宠物的存在但不抢戏
5. **物种名编码**：十六进制避免模型内部代号检查——说明他们连 build 产物中的字符串都在审计
