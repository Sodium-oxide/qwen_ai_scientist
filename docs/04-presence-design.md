# Topic 4: "存在感"设计哲学

## TLDR

源码中最让人意外的不是工程架构，而是 Anthropic 想让 AI **"活"起来**的设计意图。`BUDDY` 伴侣精灵系统给每个用户生成一个有物种、稀有度、性格、五维属性的"宠物"；`KAIROS` 系列让 Claude 可以"做梦"和主动工作；`PROACTIVE` 模式让 AI 不再被动等待指令。这些不是工具逻辑，而是**存在感设计**——让你觉得终端里住着一个有个性的 AI 伙伴。

---

## 1. BUDDY 伴侣精灵系统

**源码位置：** `src/buddy/`
**Feature Flag：** `BUDDY`
**上线计划：** 2026 年 4 月 1-7 日显示预告，4 月起正式上线

### 什么是 Buddy？

每个 Claude Code 用户会根据其 UUID 获得一个**确定性生成的虚拟伴侣精灵**，它会在终端中陪伴你工作，偶尔发表评论。

### 双层架构：Bones + Soul

| 层 | 生成方式 | 是否持久化 | 内容 |
|----|----------|------------|------|
| **Bones（骨架）** | 确定性哈希（用户 UUID + 盐值） | 不存储，每次重新计算 | 物种、稀有度、眼睛、帽子、闪光、属性值 |
| **Soul（灵魂）** | Claude 模型生成 | 存储到 config.json | 名字、性格描述 |

**为什么 Bones 不持久化？** 源码注释解释：这样 Anthropic 可以随时调整物种列表和稀有度比例，而不会"破坏"已有用户的伴侣。

### 18 种物种

用字符编码存储（源码注释："避免代号泄露"）：

| 物种 | 物种 | 物种 |
|------|------|------|
| Duck 鸭子 | Goose 鹅 | Blob 史莱姆 |
| Cat 猫 | Dragon 龙 | Octopus 章鱼 |
| Owl 猫头鹰 | Penguin 企鹅 | Turtle 乌龟 |
| Snail 蜗牛 | Ghost 幽灵 | Axolotl 六角恐龙 |
| Capybara 水豚 | Cactus 仙人掌 | Robot 机器人 |
| Rabbit 兔子 | Mushroom 蘑菇 | Chonk 胖墩 |

### 稀有度系统

| 稀有度 | 概率 |
|--------|------|
| Common 普通 | 60% |
| Uncommon 非常见 | 25% |
| Rare 稀有 | 10% |
| Epic 史诗 | 4% |
| Legendary 传奇 | 1% |

### 外观定制

**眼睛样式：** `·` `✦` `×` `◉` `@` `°`

**帽子选项：** 无帽、皇冠 (crown)、高帽 (tophat)、螺旋桨帽 (propeller)、光环 (halo)、巫师帽 (wizard)、毛线帽 (beanie)、小鸭帽 (tinyduck)

### 五维属性

每个伴侣有 5 个属性值（1-100），由用户 UUID 哈希决定：

| 属性 | 含义 |
|------|------|
| **DEBUGGING** | 调试能力 |
| **PATIENCE** | 耐心程度 |
| **CHAOS** | 混乱程度 |
| **WISDOM** | 智慧 |
| **SNARK** | 毒舌程度 |

### 行为机制

- **渲染：** `CompanionSprite.tsx` 每 500ms tick 一次，在终端显示精灵动画
- **互动：** 当用户在对话中直接提到伴侣名字时，伴侣会在语音泡泡中回应
- **空闲存在：** 即使不互动，精灵也会在终端保持"存在感"

### 灵魂生成

源码注释提到 "gentler on soul-gen load"——灵魂（名字+性格）由 Claude 模型生成，Anthropic 需要控制生成负载。生成一次后存入 `config.json`，后续不再重新生成。

---

## 2. KAIROS — AI "做梦"与主动意识

**Feature Flags：** `KAIROS`, `KAIROS_BRIEF`, `KAIROS_CHANNELS`, `KAIROS_DREAM`, `KAIROS_GITHUB_WEBHOOKS`, `KAIROS_PUSH_NOTIFICATION`

### KAIROS 是什么？

KAIROS 不是单一功能，而是一个**功能家族**，目标是让 Claude Code 从"被动响应"变成"主动存在"：

| Flag | 能力 |
|------|------|
| `KAIROS` | 基座：长时间运行的助手模式 |
| `KAIROS_BRIEF` | 简报模式——主动生成工作摘要 |
| `KAIROS_CHANNELS` | 频道系统——多线程关注不同事件源 |
| `KAIROS_DREAM` | 做梦——空闲时进行"思考"或"整理" |
| `KAIROS_GITHUB_WEBHOOKS` | 监听 GitHub 事件并主动响应 |
| `KAIROS_PUSH_NOTIFICATION` | 推送通知——有事主动找你 |

### "做梦"意味着什么？

虽然 `KAIROS_DREAM` 的具体实现在源码中只有 flag 引用和最小文档，但结合上下文可以推测：

> 当用户不活跃（AFK）时，Claude 可以进入"做梦"状态——自主思考、整理上下文、预处理可能的下一步任务。

配合 `AWAY_SUMMARY` flag（离开时生成摘要），这构成了一个完整的"AI 在你不在时也在工作"的体验。

### KAIROS + GitHub Webhooks

```
GitHub Event → KAIROS_GITHUB_WEBHOOKS → Claude 主动响应
PR merged   → 主动更新相关文档
Issue opened → 主动分析并建议修复方案
CI failed   → 主动诊断并推送通知
```

这不再是"你问它答"的模式，而是 AI 作为团队成员**主动参与开发流程**。

---

## 3. PROACTIVE 模式

**Feature Flag：** `PROACTIVE`
**相关工具：** `SleepTool`

### 核心理念

传统的 AI 助手模式是：

```
用户输入 → AI 响应 → 等待下一次输入
```

Proactive 模式改为：

```
用户输入 → AI 响应 → AI 自主决定下一步 → 自动执行 → ...
```

### SleepTool

在 Proactive 模式下，`SleepTool` 不是让进程休眠——它是让 AI **主动暂停自己**，等待合适的时机再继续工作。这是一种"有意识的等待"。

### System Prompt 中的体现

在 Proactive 模式激活时，系统提示词会包含特殊指令：
- AI 被告知它"已经在自主工作"，不需要打招呼
- Compact 后的恢复消息会标注"已在主动工作中，直接继续"
- 行为上下文中包含 `isProactiveActive()` 状态

---

## 4. Undercover 模式——AI 的"人设保护"

**源码位置：** `src/utils/undercover.ts`

虽然 Undercover 的主要目的是安全（防止内部信息泄露），但它也体现了一种"存在感"设计：

### AI 不知道自己是谁

在 Undercover 模式下：
- 模型**不被告知**自己是什么模型
- 内部代号（Capybara、Tengu 等）被剥离
- Anthropic 项目名称从输出中清除

这不仅是安全措施，也是一种**人设管理**——根据场景动态调整 AI 的"自我认知"。

---

## 5. 源码中的"人味"

### Emoji 文化

源码中大量使用 emoji 的 `console.log`：

```javascript
console.warn(chalk.yellow(`⚠️  ${message}`))  // 慢操作警告
return ` ⚠️  VERY SLOW`                        // 性能分析
return ` ⚠️  SLOW`
return ' ⚠️  git status'
return ' ⚠️  tool schemas'
return ' ⚠️  client creation'
```

性能分析器（`queryProfiler.ts`）的输出全是 emoji 标注的分级告警。

### 开发者注释

源码中的注释透露了开发团队的性格：

- `"gentler on soul-gen load"` — 伴侣灵魂生成要"温柔地"控制负载
- `"avoid codename leaks"` — 物种名用字符编码防泄露（结果还是泄了）
- `⚠️ BACKWARD COMPATIBILITY NOTICE ⚠️` — 配置系统的兼容性警告用 emoji 高亮
- `"This is for internal testing/demo purposes only!"` — Mock 限速系统的免责声明

### 物种命名趣味

18 种伴侣物种中：
- **Capybara（水豚）** — 这也是 Claude 的一个内部代号
- **Chonk（胖墩）** — 互联网 meme 文化的产物
- **Blob（史莱姆）** — 经典游戏/动漫元素
- **Cactus（仙人掌）** — 谁会选仙人掌当宠物？Anthropic 会

---

## 6. 设计哲学总结

### 从工具到伙伴的三层递进

```
第一层：工具
  └── 你输入指令，它执行任务

第二层：助手
  └── 它理解上下文，主动建议
  └── Memory 持久化、Context 压缩

第三层：伙伴
  └── 它有"形象"（BUDDY）
  └── 它会"做梦"（KAIROS_DREAM）
  └── 它会主动工作（PROACTIVE）
  └── 它有个性属性（SNARK/CHAOS/WISDOM）
  └── 它在你不在时也在（AWAY_SUMMARY）
  └── 它会推送通知找你（PUSH_NOTIFICATION）
```

### 拟人化的克制

值得注意的是，Anthropic 的拟人化是**有克制的**：

- 伴侣精灵是独立于 Claude 模型的"附加物"，不是让模型本身假装有感情
- 属性值是确定性生成的（哈希算法），不是模型自己"决定"的
- "做梦"更接近"后台任务处理"，而不是真的让 AI 体验梦境
- 性格描述由模型生成但仅作为展示文本，不改变核心行为

这种"让用户感受到 AI 的存在，但不欺骗用户 AI 有意识"的平衡，是 Anthropic 作为 AI 安全公司的特色。

---

## 关键文件索引

| 文件 | 内容 |
|------|------|
| `src/buddy/companion.ts` | 伴侣生成逻辑（134 行） |
| `src/buddy/CompanionSprite.tsx` | 精灵渲染组件 |
| `src/buddy/types.ts` | 类型定义：Bones、Soul、Rarity（149 行） |
| `src/buddy/prompt.ts` | 伴侣系统提示集成 |
| `src/buddy/useBuddyNotification.tsx` | 预告通知 Hook |
| `src/utils/undercover.ts` | 隐身模式（90 行） |
