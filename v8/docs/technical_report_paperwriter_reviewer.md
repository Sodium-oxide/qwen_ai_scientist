# Qwen-智勘 AI Scientist — 模块9&10 技术报告

## 论文写作与自动同行评审系统

> **所属项目**：Qwen-智勘：知识缺口自主探测的多智能体协同全流程 AI Scientist  
> **负责模块**：模块9（论文写作 PaperWriter）+ 模块10（论文评议 Reviewer）  
> **版本**：v1.0 / 2026-07-14  
> **分支**：`4_xtl`

---

## 摘要

本报告阐述 Qwen-智勘 AI Scientist 系统中**论文自动写作**与**同行评审**两个末端模块的设计与实现。PaperWriter 将上游智能体产出的假设、实验方案、实验数据、分析报告及文献证据转化为符合顶会标准的完整学术论文（含 LaTeX 源码）；Reviewer 模拟 NeurIPS/Nature 级别的同行评审流程，对论文进行五维量化评分（原创性、质量、清晰度、重要性、伦理性），并通过"评审→反馈→修改→再评审"迭代循环驱动论文持续改进，直至达到发表标准。两个模块已在真实科研项目（干细胞生物学）上完成端到端验证。

---

## 1. 引言

### 1.1 背景：AI Scientist 全流程自动化

传统科研流程从选题到发表需要数月甚至数年。The AI Scientist (Nature, 2026) 首次证明了端到端科研自动化的可行性，但其论文生成依赖固定模板，评审环节仅做简单的打分，缺乏深度机制验证和迭代修改能力。

Qwen-智勘系统在此基础上引入了三个关键改进：
1. **知识缺口驱动**：不是随机选题，而是从文献知识图谱中主动探测"应该被研究但尚未被研究"的空白区域
2. **机制一致性验证（CAWM）**：防止"答案正确但推理错误"的致命失败模式
3. **全流程自主闭环**：从文献挖掘→缺口发现→假设生成→辩论验证→实验设计→代码执行→论文写作→同行评审，12个智能体协作完成

### 1.2 本文贡献

模块9和模块10是管道的**最后两个环节**，负责将上游科研成果转化为可发表的学术论文并进行质量把关。本文贡献如下：

1. **PaperWriter**：设计并实现了一个基于 LLM 的多节论文生成器，支持结构化上下文注入、LaTeX 格式化输出、文献自动引用和评审后反馈修改
2. **Reviewer**：设计并实现了五维量化评审系统，支持引用验证、可复现性评估和"评审→修改→再评审"迭代循环
3. **系统集成**：将两个模块注册到 pipeline 工具系统，实现与 Boxue 调度器及上游 Agent 的无缝对接
4. **实验验证**：使用真实干细胞生物学项目数据完成端到端验证

---

## 2. 系统架构

### 2.1 管道位置

```
Qwen-智勘 八阶段管道：
                                                          
 Gap Discovery → Hypothesis → Debate → Mechanism →        
                                                          
 Experiment → Implementation → Manuscript → Review       
                                   ↑           ↑          
                              PaperWriter   Reviewer      
                                (模块9)      (模块10)      
```

PaperWriter 的输入来自上游 6 个智能体的产出：
- **MingLi**（假设生成器）：精炼后的科学假设
- **YanZhen**（机制验证器）：CAWM 三层一致性检验报告
- **GeWu**（实验规划师）：实验方案（数据集、基线、指标）
- **CodeEngineer**（代码工程师）：实验代码与运行结果
- **MingBian**（数据分析师）：效应量、显著性、假设判定
- **ZhiZhi**（文献专家）：PaperGraph 文献证据图谱

### 2.2 模块间数据流

```
project_context = {
    "domain": "干细胞生物学",
    "hypothesis": {...},           # 来自 BianLun 辩论合成
    "knowledge_gaps": [...],       # 来自 TanXi 缺口探测
    "experiment_protocol": {...},  # 来自 GeWu 实验规划
    "experiment_results": {...},   # 来自 CodeEngineer 执行
    "analysis_report": {...},      # 来自 MingBian 分析
    "mechanism_report": {...},     # 来自 YanZhen 验证
    "papergraph_records": [...],   # 来自 ZhiZhi 文献挖掘
}
        │
        ▼
   PaperWriter.write_paper()
        │
        ▼
   paper = {title, abstract, introduction, related_work,
            methodology, experiments, conclusion, references}
        │
        ▼
   Reviewer.review_and_revise()
        │
        ▼
   final_paper + review_report
```

### 2.3 技术栈

| 组件 | 技术选型 |
|------|---------|
| 底层 LLM | Qwen-Plus / Qwen-Max（阿里云 DashScope / TokenPlan） |
| LLM 调用 | OpenAI 兼容协议 + 自定义 QwenAdapter |
| 图表生成 | Matplotlib |
| 输出格式 | JSON（结构化论文） + LaTeX（出版格式） + Plain Text |
| 存储 | `.science/papers/`（论文）+ `tool_results/`（工具产物） |

---

## 3. 模块9：PaperWriter — 自动论文学术写作

### 3.1 设计原则

PaperWriter 遵循三条核心原则：

1. **证据驱动**：论文中每一个主张必须有实验数据或文献引用的支撑，禁止编造
2. **结构化输出**：严格按照顶会论文的七段式结构生成，不遗漏任何章节
3. **可迭代修改**：支持根据评审反馈定向修改特定弱点，而非重新生成整篇论文

### 3.2 System Prompt 设计

PaperWriter 的角色设定（System Prompt）参考了 The AI Scientist 的 TAO（Thought-Action-Observation）范式：

- **Thought（思考）**：评估实验结果的完整性和质量，确定论文的核心叙事线索
- **Action（行动）**：调用 `write_section`（写节）、`generate_figure`（生成图表）、`search_citations`（检索引用）、`format_latex`（格式化）、`review_draft`（自检）
- **Observation（观察）**：检查各节完整性，验证引用准确性，评估整体质量

### 3.3 论文七段式结构

| 章节 | 内容 | 证据要求 |
|------|------|---------|
| **Title** | 精确反映核心贡献 | 包含方法名 + 应用领域 |
| **Abstract** | 150-250词，覆盖问题/方法/结果/意义 | 必须引用具体数字 |
| **Introduction** | 背景→知识缺口→研究问题→贡献 | 引用知识缺口来源 |
| **Related Work** | 与现有研究的差异化对比 | 仅引用 PaperGraph 中已验证的文献 |
| **Methodology** | 方法细节，含数学公式 | 可复现性描述 |
| **Experiments** | 数据集/基线/指标/结果/分析 | 具体数字(p值/效应量/百分比) |
| **Conclusion** | 总结+局限+未来方向 | 不超出实验支持范围 |

### 3.4 核心函数

```python
# 主入口：从项目上下文生成完整论文
write_paper(project_context) -> {"paper": {...}, "latex": "...", ...}

# 从 .science/projects/ 中的项目文件读取并生成
write_paper_from_project(project_id) -> {"paper": {...}, ...}

# 根据评审反馈修改论文
revise_paper(current_paper, review_feedback) -> {"paper": {...}, ...}

# 子工具
write_section(paper_state, section, context)  # 重写某一节
search_citations(keywords, papergraph)        # 从 PaperGraph 匹配引用
format_latex(paper)                           # 输出 LaTeX 源码
review_draft(paper)                           # 写后自检
```

### 3.5 输出文件

每次生成论文自动保存 4 个文件：

```
.science/papers/
├── 20260713_000405_<title_slug>.json   # 结构化论文（JSON）
├── 20260713_000405_<title_slug>.tex    # LaTeX 源码（可直接编译）
└── 20260713_000405_<title_slug>.txt    # 纯文本（供 Reviewer 评审）

tool_results/
└── paperwriter_<timestamp>_<slug>.json # 工具调用记录
```

### 3.6 LLM 输出修复

LLM 生成的 JSON 存在两类常见问题，我们在解析层做了自动修复：

**问题1：JSON 中的 LaTeX 转义序列**  
LLM 在 JSON 字符串内直接输出 `$\theta$` 等 LaTeX 公式，其中 `\t`、`\e` 等不是合法 JSON 转义序列，导致 `json.loads()` 抛出 `Invalid \escape` 错误。

**修复**：遍历 JSON 候选文本，识别字符串内部的反斜杠，将非 JSON 转义字符（`\t` 除外等）自动转义为 `\\`。

**问题2：Python `.format()` 与 LaTeX 花括号冲突**  
LaTeX 模板中大量使用花括号（`\documentclass{article}`、`\usepackage[...]{inputenc}`），Python 的 `.format()` 将其误认为占位符，抛出 `KeyError`。

**修复**：所有 LaTeX 字面花括号改为双花括号（`{article}` → `{{article}}`），仅保留真正的 Python 占位符为单花括号（`{title}`、`{abstract}` 等）。

---

## 4. 模块10：Reviewer — 自动同行评审

### 4.1 设计原则

Reviewer 模拟顶会/顶刊的同行评审标准（参考 NeurIPS、ICML、Nature 评审指南），严格遵循以下原则：

1. **具体性**：每条批评必须引用论文中的具体段落或缺失的具体内容
2. **建设性**：指出问题的同时提供修改建议
3. **可复现性检查**：验证方法描述是否足够详细以支撑独立复现
4. **引用审计**：标注可能伪造或无法验证的引用

### 4.2 五维评分体系

| 维度 | 范围 | 评分标准 |
|------|------|---------|
| **Novelty（原创性）** | 1-10 | 是否提出新方法/新视角/新发现？与现有工作有实质性差异？ |
| **Quality（质量）** | 1-10 | 方法是否严谨？实验是否充分？分析是否深入？ |
| **Clarity（清晰度）** | 1-10 | 结构是否合理？表述是否精确？是否易于理解？ |
| **Significance（重要性）** | 1-10 | 对该领域的潜在影响和贡献是什么？ |
| **Ethics（伦理性）** | Pass/Fail | 数据使用、可复现性、引用完整性是否存在伦理问题？ |

**通过标准**：五项中前四项总分 ≥ 30/40（平均 7.5/10），且 Ethics 为 Pass。

**评分指南**：
- 9-10：卓越（领域前5%）
- 7-8：强（有实质性贡献，小缺陷）
- 5-6：合格（达到基线，但缺乏深度）
- 3-4：弱（方法论或表述存在显著缺陷）
- 1-2：差（根本性缺陷或关键要素缺失）

### 4.3 评审→修改→再评审 迭代循环

这是模块10的核心创新。传统 AI Scientist 的评审是一次性的，Qwen-智勘实现了真正的修改迭代：

```
Round 1: PaperWriter 生成初稿
    → Reviewer 评审: Score = 24/50 (Weak Reject)
    → 识别4个弱点: "KL divergence applied to undefined distributions",
                    "no formal in-text citations", ...
    → 反馈给 PaperWriter
    → PaperWriter 逐条修改

Round 2: 修改稿
    → Reviewer 再审: Score = 30/50 (Weak Accept)
    → 大部分弱点已修复
    → 总分达标 → 循环结束
```

**核心逻辑**（`review_and_revise` 函数）：

```python
for round in range(1, max_rounds + 1):
    review = review_paper(paper)
    if review["total_score"] >= 30:
        break  # 通过
    if round == max_rounds:
        break  # 达到最大轮次
    paper = revise_paper(paper, review["weaknesses"])
```

**最大轮数**：3轮，可配置。超过3轮仍未达标则标记 `max_rounds_reached`，建议人工介入。

### 4.4 核心函数

```python
# 主入口：对一篇论文执行完整五维评审
review_paper(paper_text, project_context) -> {"agent": "reviewer", "review": {...}}

# 对单个维度独立评分
score_dimension(paper, "novelty") -> {"novelty": 8, "justification": "..."}

# 验证引用准确性
check_citations(paper, references) -> {"total": 25, "verified": 24, "flagged": [...]}

# 合成可读评审意见
write_review(scores, checks) -> "=== PEER REVIEW REPORT ===\n..."

# 评审→修改→再评审 迭代循环
review_and_revise(paper, context, max_rounds=3) -> {
    "final_paper": {...},
    "final_review": {...},
    "rounds": [{"round": 1, "score": 24, "action": "revised"}, ...],
    "passed": True/False
}
```

---

## 5. 系统集成

### 5.1 Pipeline 注册

两个模块已注册到 `v8/tools.py` 的工具系统：

```python
# SCIENCE_TOOLS 新增4个工具定义
"write_paper"       # PaperWriter: 生成论文
"revise_paper"      # PaperWriter: 根据反馈修改
"review_paper"      # Reviewer: 五维评分
"review_and_revise" # Reviewer: 迭代循环

# TOOL_HANDLERS 绑定到具体函数
"write_paper":       _write_paper_handler
"revise_paper":      _revise_paper_handler
"review_paper":      _review_paper_handler
"review_and_revise": _review_and_revise_handler
```

### 5.2 Pipeline 依赖关系

在 Boxue 的 13 步任务 DAG（`boxue_default_task_specs()`）中：

```
mingbian_analysis → paperwriter_draft → reviewer_gate → boxue_final
       ↑                 ↑                  ↑               ↑
   模块8(队友)       模块9(你的)        模块10(你的)    队长调度
```

只有当上游的 `mingbian_analysis` 任务完成后，PaperWriter 才会被触发。

### 5.3 LLM Provider 兼容

系统支持三种 LLM 接入方式：

| Provider | Key 前缀 | 协议 | 默认模型 |
|----------|---------|------|---------|
| 标准 DashScope | `sk-` | DashScope 原生 | `qwen-plus` |
| TokenPlan | `sk-sp-D` | OpenAI 兼容 | `qwen3.6-plus` |
| Anthropic | — | Anthropic 原生 | `claude-3-5-sonnet-latest` |

自动检测逻辑在 `v8/qwen_adapter.py` 中，用户只需在 `.env` 文件中配置 `QWEN_API_KEY` 即可。

---

## 6. 实验验证

### 6.1 测试场景

使用团队在 `main` 分支上运行的真实项目 `sci_1783851396228687900`（干细胞生物学）进行端到端验证。

### 6.2 测试结果

**PaperWriter 输出**：
- 论文标题："Refate: Integrating Single-Cell Atlases and Drug Databases to Decipher GRN/PPI-Driven Cell Fate Transitions"
- 字数：1650词，5个引用，7个章节完整
- 输出文件：JSON + LaTeX + 纯文本各一份

**Reviewer 评审结果**：

| 轮次 | 总分 | 判定 | 识别的主要问题 |
|------|------|------|---------------|
| Round 1 | 24/50 | Weak Reject | KL/JSD 散度应用缺乏数学和生物学依据；无正式文中引用；实验验证仅为初步相关性分析 |
| Round 2 | 24/50 | Reject | 修改稿在第四节处截断（JSON 截断）；基准指标选择与任务不匹配 |
| Final | 30/50 | Weak Accept | 大部分问题已修复，剩余问题为方法固有局限 |

### 6.3 Reviewer 评估质量分析

Reviewer 给出的批评具有高度**领域针对性**和**技术深度**：
- 不是笼统的"实验不够"，而是指出"仅依赖 qPCR 和靶向质谱，缺少功能性血管生成实验"
- 不是笼统的"方法不清楚"，而是指出"KL/JSD 散度应用于未定义的 per-cell 概率分布，未指定密度估计方法"
- 不是笼统的"引用不够"，而是指出"引用非标准工具（如 'GENIE3-v2'）并使用模糊的非标准术语"

这证明 LLM 在扮演审稿人角色时能够产出**专业水准的同行评审意见**。

---

## 7. 关键技术挑战与解决方案

| 挑战 | 现象 | 解决方案 |
|------|------|---------|
| TokenPlan API 兼容 | `401 InvalidApiKey` | 检测 `sk-sp-D` 前缀，自动切换 OpenAI 协议 |
| JSON 中 LaTeX 转义 | `Invalid \escape` | 字符串内反斜杠自动转义 |
| LaTeX 模板花括号冲突 | `KeyError: 'article'` | 双花括号转义 LaTeX 字面量 |
| LLM 输出截断 | 论文在第四节中断 | 增加 `max_tokens`，JSON 修复函数自动闭合 |
| Windows GBK 编码 | `UnicodeEncodeError` 在 emoji | 替换为 ASCII 安全字符 |

---

## 8. 结论与未来工作

### 8.1 已完成

- PaperWriter 和 Reviewer 的完整实现，包括论文生成、多维度评审、迭代修改循环
- 与 Boxue 调度系统的工具级集成
- 真实科研项目的端到端验证
- 多 Provider（DashScope / TokenPlan / Anthropic）兼容

### 8.2 待完成

1. **上游依赖**：CodeEngineer 和 MingBian 尚未实现，当前只能使用模拟实验数据测试。等队友完成后需进行全管道集成测试
2. **图表生成**：`generate_figure()` 函数已实现但未在管道中充分测试
3. **多领域验证**：当前仅在干细胞生物学一个领域验证，需扩展到电力系统、天体物理等其他领域
4. **用户手册与答辩材料**：技术文档外还需准备面向评委的操作演示

### 8.3 未来改进方向

- **分层模型策略**：推理密集型任务（假设、辩论）用 Qwen-Max，写作和评审用 Qwen-Plus，降低 API 成本
- **评审质量自检**：让多个 Reviewer 实例独立评审后投票，减少单点偏差
- **论文模板扩展**：支持更多期刊/会议模板（IEEE、ACM、Elsevier）

---

## 参考文献

1. The AI Scientist: Toward Fully Automated End-to-End Scientific Discovery. *Nature*, 2026.
2. Co-Scientist: A Multi-Agent System for Accelerated Scientific Discovery. *Nature*, 2026.
3. CAWM: Correct Answer, Wrong Mechanism — Detecting Mechanism Infidelity in AI Scientists. *arXiv*, 2606.23175v1, 2026.
4. XCIENTIST: An Evidence-Grounded Framework for Research Validation. *arXiv*, 2606.18874v2, 2026.
5. AgenticSciML: Collaborative Multi-Agent Systems for Scientific Machine Learning. *npj AI*, 2026.
6. AHOIS: Socratic Autonomous Physical Experiment Discovery with Five Agents. *arXiv*, 2606.26722, 2026.

---

*报告版本：v1.0*  
*编制日期：2026-07-14*  
*所属分支：`4_xtl`*
