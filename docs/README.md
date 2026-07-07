# Claude Code 源码深度解析

> 基于 2026-03-31 通过 npm source map 泄露的完整源码快照，~1,900 个 TypeScript 文件，512,000+ 行代码。

**一句话总结：** Claude Code 本质是个高度可扩展的 Agent 操作系统，泄露让大家看到 Anthropic 想把它做成"会陪你的 AI 伙伴"，而不是单纯的代码助手。

---

## Topic 清单

| # | Topic | 关键词 |
|---|-------|--------|
| 1 | [Agent 架构极致模块化](./01-agent-architecture.md) | Coordinator Mode、Sub-Agent、UDS Inbox、Context 压缩、Memory 持久化、Hooks |
| 2 | [功能开关驱动开发](./02-feature-flags.md) | 80+ 编译时 flag、bun:bundle 死代码消除、未发布功能、内部实验 |
| 3 | [BUDDY — 命令行里的电子宠物](./03-buddy-tamagotchi.md) | 18 种物种、5 级稀有度、Tamagotchi 风格、4/1 愚人节彩蛋 |
| 4 | [KAIROS — AI 在你睡觉时"做梦"](./04-kairos-dreaming.md) | autoDream、跨会话记忆整合、/dream 技能、持久化助手模式 |
| 5 | [ULTRAPLAN / Coordinator Mode](./05-ultraplan-coordinator.md) | 30 分钟远程规划、多代理并行、XML 任务通知、Agent Swarm |
| 6 | [UDS Inbox / Bridge / Daemon](./06-uds-bridge-daemon.md) | Unix Socket 互联、IDE 桥接、手机远程控制、守护进程 |
| 7 | [90 个 Build-Time Feature Flags](./07-feature-flags-full.md) | 完整清单、按功能分类、编译时死代码消除 |
| 8 | [478 个环境变量](./08-environment-variables.md) | CLAUDE_CODE_*（196 个）、ANTHROPIC_*、OTEL_*、内部调试开关 |
| 9 | [内部斜杠命令](./09-internal-commands.md) | 84+ 命令、30+ 内部命令、Feature-flag 门控、隐藏命令 |
| 10 | [USER_TYPE=ant 员工专属功能](./10-ant-employee-features.md) | 95+ 处门控、专属工具、Undercover 模式、系统提示词差异 |
| 🔍 | [**深挖发现集**](./findings.md) | 54 条逐文件发现：系统提示词工程、安全防御、查询管道、MCP、彩蛋… |

---

## 背景

这次不是"社区逆向"，而是官方原汁原味的完整源码，直接把之前社区猜的 90% 都验证了，还多了很多隐藏彩蛋。源码通过 npm 包中暴露的 `.map` 文件被发现，引用了 Anthropic R2 存储桶中未混淆的 TypeScript 源码。

## 技术栈

| 类别 | 技术 |
|------|------|
| 运行时 | Bun |
| 语言 | TypeScript (strict) |
| 终端 UI | React + Ink |
| CLI 解析 | Commander.js |
| Schema 校验 | Zod v4 |
| 代码搜索 | ripgrep |
| 协议 | MCP SDK, LSP |
| API | Anthropic SDK |
| 遥测 | OpenTelemetry + gRPC |
| 功能开关 | GrowthBook + bun:bundle |
| 认证 | OAuth 2.0, JWT, macOS Keychain |
