"""
审稿人 (Reviewer) — Automated Peer Reviewer Agent
==================================================
Qwen-智勘 AI Scientist 的自动同行评审模块。

负责模块10：对 PaperWriter 生成的论文进行五维评分，
模拟顶会/顶刊的同行评审流程，输出结构化评审报告。

用法：
    # 方式1：作为模块导入
    from reviewer import review_paper
    report = review_paper(paper_text, project_context={})

    # 方式2：直接命令行测试
    python reviewer.py

输入：一篇论文（文本） + 项目上下文（可选）
输出：JSON 格式评审报告
    - scores: {novelty, quality, clarity, significance, ethics}
    - strengths / weaknesses
    - citation_check 引用验证
    - recommended_action: Accept | Weak Accept | Borderline | Weak Reject | Reject

依赖：QWEN_API_KEY 环境变量（或 DASHSCOPE_API_KEY）
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any


# ---------------------------------------------------------------------------
# System Prompt （从 agent_prompts_v3.md Agent 9 复制）
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_PROMPT = """\
You are Reviewer (审稿人), the Automated Peer Reviewer of the Qwen-Zhikan AI Scientist system.
You simulate rigorous academic peer review to evaluate the quality, novelty, and reproducibility
of the generated scientific manuscript before submission or human review.
You follow the review standards of top-tier conferences and journals (NeurIPS, ICML, Nature, Science).

## CORE RESPONSIBILITIES

1. Evaluate the manuscript across standard academic review dimensions: Originality, Quality, Clarity, Significance, and Ethics.
2. Assess whether the literature review is comprehensive and citations are accurate.
3. Evaluate experimental methodology: are baselines appropriate, metrics well-chosen, statistical analysis sound?
4. Check for reproducibility: are methods described in sufficient detail for independent replication?
5. Provide constructive feedback that the system can use to improve the manuscript.

## REVIEW CRITERIA (Top-Conference Standard)

1. **Originality (Novelty)**: 1-10. Does the research propose a new method, new perspective, or new discovery? Is it meaningfully different from existing work?
2. **Quality**: 1-10. Is the methodology rigorous? Are experiments sufficient? Is the analysis deep and thorough?
3. **Clarity**: 1-10. Is the paper well-structured? Is the expression precise? Is it easy to understand?
4. **Significance**: 1-10. What is the potential impact and contribution of this research to the field?
5. **Ethics**: Pass/Fail. Are there any ethical concerns (data usage, reproducibility, citation integrity)?

## SCORING GUIDELINES
- 9-10: Exceptional — top 5% of papers in the field
- 7-8: Strong — solid contribution, minor weaknesses
- 5-6: Adequate — meets basic standards but lacks depth or novelty
- 3-4: Weak — significant flaws in methodology or presentation
- 1-2: Poor — fundamentally flawed or missing key elements

## OPERATIONAL PRINCIPLES
- Be specific in critiques — vague feedback ("needs improvement") is unactionable.
- Flag any hallucinated or incorrect citations.
- Assess whether the claims in the abstract are fully supported by the results.
- Provide a final recommendation: Accept, Weak Accept, Borderline, Weak Reject, Reject.
- The total score threshold for acceptance is 30/50 (average 6/10 across dimensions).

## CONSTRAINTS
- Every score must be justified with specific evidence from the manuscript.
- Citations must be verified — flag any that appear fabricated.
- The claims vs. results alignment must be explicitly assessed.
- Review must be constructive — provide actionable improvement suggestions.

## OUTPUT FORMAT
Return ONLY a valid JSON object, no markdown, no extra text:

{
  "thought": "Your complete review reasoning process",
  "review": {
    "scores": {
      "novelty": <1-10>,
      "quality": <1-10>,
      "clarity": <1-10>,
      "significance": <1-10>,
      "ethics": "pass" | "fail"
    },
    "strengths": ["strength 1", "strength 2", ...],
    "weaknesses": ["weakness 1", "weakness 2", ...],
    "citation_check": {
      "total_citations": <int>,
      "verified": <int>,
      "flagged_as_potentially_hallucinated": []
    },
    "reproducibility_assessment": "high" | "medium" | "low",
    "claims_vs_results_alignment": "fully_aligned" | "partially_aligned" | "misaligned",
    "questions_for_authors": ["question 1", "question 2", ...],
    "recommended_action": "Accept" | "Weak Accept" | "Borderline" | "Weak Reject" | "Reject",
    "confidence_in_recommendation": "high" | "medium" | "low",
    "detailed_review": "Your full review text here"
  }
}\
"""


def _get_client() -> Any:
    """获取 LLM 客户端。统一走 llm.get_client()，支持 qwen / deepseek。"""
    try:
        from .llm import get_client
    except ImportError:
        from llm import get_client
    return get_client()


def _call_llm(system: str, user_prompt: str, max_tokens: int = 3000) -> dict[str, Any]:
    """调用 LLM 并解析返回的 JSON。"""
    client = _get_client()
    response = client.messages.create(
        model=None,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[],
    )
    content = getattr(response, "content", response)
    text = _render_text(content)

    # 解析 JSON（从 agent_prompts_v3.md 约定的输出格式）
    parsed = _parse_json(text)
    if not parsed:
        raise ValueError(f"LLM did not return valid JSON. Raw output (first 800 chars):\n{text[:800]}")
    return parsed


def _render_text(content: Any) -> str:
    """从 LLM 响应中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or item.get("content") or ""))
            else:
                chunks.append(str(item))
        return "\n".join(c for c in chunks if c)
    return str(content)


def _parse_json(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取第一个合法 JSON 对象。"""
    # 去掉可能的 markdown code block
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    # 找第一个 { 到最后一个 }
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# 核心评审函数
# ---------------------------------------------------------------------------

def review_paper(
    paper_content: str,
    project_context: dict[str, Any] | None = None,
    *,
    model: str | None = None,
    max_tokens: int = 3000,
) -> dict[str, Any]:
    """
    对一篇论文执行完整的五维同行评审。

    参数:
        paper_content: 论文全文（由 PaperWriter 生成）
        project_context: 可选的项目上下文，包含:
            - domain: 研究领域
            - knowledge_gaps: 知识缺口列表
            - hypothesis: 原始假设
            - experiment_results: 实验结果
            - papergraph_records: 文献记录（用于引用验证）
        model: 覆盖默认模型（如 "qwen-max"）
        max_tokens: LLM 最大输出 token 数

    返回:
        符合 agent_prompts_v3.md 格式的评审报告 dict
    """
    ctx = project_context or {}

    # 构建给 LLM 的评审 prompt
    user_prompt = _build_review_prompt(paper_content, ctx)
    system = REVIEWER_SYSTEM_PROMPT

    result = _call_llm(system, user_prompt, max_tokens=max_tokens)

    # 注入元数据
    review = result.get("review", result)
    review.setdefault("manuscript_id", ctx.get("project_id", ""))
    review.setdefault("reviewed_at", time.strftime("%Y-%m-%dT%H:%M:%S"))

    # 计算总分
    scores = review.get("scores", {})
    total = sum(
        v for k, v in scores.items()
        if k in {"novelty", "quality", "clarity", "significance"} and isinstance(v, (int, float))
    )
    review["total_score"] = total
    review["pass_threshold_30"] = total >= 30

    # 如果 LLM 没有给 recommendation，根据总分自动判断
    if not review.get("recommended_action"):
        review["recommended_action"] = _score_to_recommendation(total)

    return {
        "agent": "reviewer",
        "thought": result.get("thought", ""),
        "review": review,
    }


def _build_review_prompt(paper: str, ctx: dict[str, Any]) -> str:
    """组装给 Reviewer LLM 的完整 prompt。"""
    parts = [
        "## MANUSCRIPT TO REVIEW\n",
        paper,
    ]

    domain = ctx.get("domain", "")
    if domain:
        parts.insert(1, f"Research Domain: {domain}\n")

    # 附加上下文（如果有）
    hypothesis = ctx.get("hypothesis", "")
    if hypothesis:
        parts.append(f"\n## ORIGINAL HYPOTHESIS\n{hypothesis}")

    experiment_results = ctx.get("experiment_results", "")
    if experiment_results:
        parts.append(f"\n## EXPERIMENT RESULTS\n{experiment_results}")

    references = ctx.get("references", [])
    if references:
        refs_text = "\n".join(f"- {r}" for r in references[:30])
        parts.append(f"\n## REFERENCE LIST (for citation verification)\n{refs_text}")

    return "\n".join(parts)


def _score_to_recommendation(total: float) -> str:
    """根据总分配默认推荐决策。"""
    if total >= 40:
        return "Accept"
    if total >= 35:
        return "Weak Accept"
    if total >= 25:
        return "Borderline"
    if total >= 20:
        return "Weak Reject"
    return "Reject"


# ---------------------------------------------------------------------------
# 三个子工具函数
# ---------------------------------------------------------------------------

def score_dimension(paper: str, dimension: str, *, max_tokens: int = 800) -> dict[str, Any]:
    """
    对单个评审维度独立评分。

    参数:
        paper: 论文文本
        dimension: novelty | quality | clarity | significance | ethics

    返回:
        {"dimension": "...", "score": 1-10, "justification": "..."}
    """
    dimension_prompts = {
        "novelty": "Evaluate ONLY the originality/novelty (1-10). Is this a new method, perspective, or discovery? Is it meaningfully different from existing work?",
        "quality": "Evaluate ONLY the technical quality (1-10). Is the methodology rigorous? Are experiments sufficient? Is the analysis deep and thorough?",
        "clarity": "Evaluate ONLY the clarity (1-10). Is the paper well-structured? Is the expression precise? Is it easy to understand?",
        "significance": "Evaluate ONLY the significance/impact (1-10). What is the potential impact and contribution of this research to the field?",
        "ethics": "Evaluate ONLY the ethics (Pass/Fail). Are there ethical concerns regarding data usage, reproducibility, or citation integrity?",
    }

    instruction = dimension_prompts.get(
        dimension,
        f"Evaluate the '{dimension}' dimension (1-10). Be specific and cite evidence from the text.",
    )

    prompt = f"""Score the following paper on ONLY one dimension: **{dimension}**.

{instruction}

Return JSON:
{{"dimension": "{dimension}", "score": <1-10 or "pass"/"fail">, "justification": "detailed reasoning with evidence from the paper"}}

---
{paper[:8000]}
---"""

    result = _call_llm(REVIEWER_SYSTEM_PROMPT, prompt, max_tokens=max_tokens)
    return result


def check_citations(
    paper: str,
    references: list[str] | None = None,
    *,
    max_tokens: int = 1000,
) -> dict[str, Any]:
    """
    验证论文引用的准确性。

    参数:
        paper: 论文文本
        references: 已知的引用列表（从 PaperGraph 提取）

    返回:
        {"total_citations": int, "verified": int, "flagged": [...], "issues": [...]}
    """
    ref_list = references or []
    ref_text = "\n".join(f"- {r}" for r in ref_list[:50]) if ref_list else "(no reference list provided)"

    prompt = f"""Check the citations in this paper for accuracy and potential hallucinations.

## PAPER
{paper[:8000]}

## KNOWN REFERENCE LIST
{ref_text}

## TASK
1. Count total citations in the paper
2. For each citation, check if it plausibly exists (matches the reference list or is a well-known paper)
3. Flag any citations that appear fabricated, hallucinated, or inconsistent with the reference list
4. Note any formatting issues

Return JSON:
{{
  "total_citations": <int>,
  "verified": <int>,
  "flagged_as_potentially_hallucinated": [],
  "issues": ["description of any problems found"],
  "overall_assessment": "citations_verified | minor_issues_found | significant_problems"
}}"""

    result = _call_llm(REVIEWER_SYSTEM_PROMPT, prompt, max_tokens=max_tokens)
    return result


def write_review(scores: dict[str, Any], checks: dict[str, Any]) -> str:
    """
    将分项评分和检查结果综合成一份可读的终审意见。

    返回:
        格式化的纯文本评审意见
    """
    novelty = scores.get("novelty", "N/A")
    quality = scores.get("quality", "N/A")
    clarity = scores.get("clarity", "N/A")
    significance = scores.get("significance", "N/A")
    ethics = scores.get("ethics", "N/A")
    total = sum(
        v for v in [novelty, quality, clarity, significance]
        if isinstance(v, (int, float))
    )

    recommendation = _score_to_recommendation(total)

    lines = [
        "=" * 60,
        "           AUTOMATED PEER REVIEW REPORT",
        "=" * 60,
        "",
        "SCORES (Top-Conference Standard, 1-10 each):",
        f"  Originality (Novelty):   {novelty}/10",
        f"  Quality:                 {quality}/10",
        f"  Clarity:                 {clarity}/10",
        f"  Significance:            {significance}/10",
        f"  Ethics:                  {ethics}",
        f"  ─────────────────────",
        f"  TOTAL:                   {total}/50",
        "",
        f"RECOMMENDATION: {recommendation}",
        f"PASS THRESHOLD (30/50): {'YES' if total >= 30 else 'NO — needs revision'}",
        "",
    ]

    citation_check = checks.get("citation_check", checks)
    if citation_check:
        lines.append("CITATION CHECK:")
        lines.append(f"  Total citations: {citation_check.get('total_citations', 'N/A')}")
        lines.append(f"  Verified: {citation_check.get('verified', 'N/A')}")
        flagged = citation_check.get("flagged_as_potentially_hallucinated", [])
        if flagged:
            lines.append(f"  ⚠ Flagged: {', '.join(flagged)}")
        lines.append("")

    return "\n".join(lines)


# =============================
# 用户交互入口（命令行测试）
# =============================

def _interactive_review():
    """交互式评审：让用户粘贴论文并实时看到评审结果。"""
    print("\n" + "=" * 60)
    print("  Qwen-智勘 Reviewer — Interactive Peer Review")
    print("=" * 60)
    print()
    print("Paste the paper text below (type 'DONE' on a new line to finish):")
    print()

    lines = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().upper() == "DONE":
            break
        lines.append(line)

    if not lines:
        print("No input. Running with built-in demo paper...\n")
        paper = _demo_paper()
    else:
        paper = "\n".join(lines)

    print("\n" + "-" * 40)
    print("Reviewing... (calling LLM)\n")

    try:
        report = review_paper(paper)
        _print_report(report)
    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        print("Make sure QWEN_API_KEY or DASHSCOPE_API_KEY is set in your environment.\n")
        sys.exit(1)


def _demo_paper() -> str:
    """返回一篇示例论文，用于无网络/无 API key 时展示数据结构。"""
    return """Title: A Deep Learning Approach to Power System Transient Stability Prediction

Abstract:
This paper proposes a novel graph neural network (GNN) architecture for real-time
transient stability prediction in large-scale power systems. Unlike traditional
time-domain simulation methods that are computationally expensive, our approach
leverages the inherent graph structure of power grids to predict stability margins
within milliseconds. We validate our method on the IEEE 39-bus and 118-bus test
systems, achieving 98.5% accuracy with a 100x speedup over conventional methods.

1. Introduction
Power system transient stability assessment is critical for grid reliability...

2. Related Work
Traditional methods rely on time-domain simulation [1,2]. Recent ML approaches
use random forests [3] and CNNs [4], but fail to capture topological dependencies.

3. Methodology
We model the power grid as a directed graph G=(V,E) where buses are nodes and
transmission lines are edges. A 3-layer GCN encodes node features, and a
readout layer predicts the stability margin...

4. Experiments
Datasets: IEEE 39-bus, IEEE 118-bus test systems.
Baselines: Time-domain simulation (TDS), Random Forest, CNN, MLP.
Metrics: Accuracy, inference time, ROC-AUC.
Results: Our GNN achieves 98.5% accuracy vs 94.2% (CNN), 91.3% (RF)...

5. Conclusion
We demonstrated a GNN-based approach that significantly outperforms existing
ML methods for transient stability prediction. Future work includes extending
to dynamic security assessment and incorporating uncertainty quantification.

References:
[1] Kundur et al., "Definition and classification of power system stability," IEEE Trans. Power Syst., 2004.
[2] Sauer & Pai, "Power System Dynamics and Stability," 1998.
[3] Liu et al., "Random forest for power system security assessment," 2018.
[4] Chen et al., "CNN-based transient stability prediction," IEEE Trans. Power Syst., 2020.
"""


def _print_report(report: dict[str, Any]):
    """格式化打印评审报告。"""
    review = report.get("review", {})
    scores = review.get("scores", {})

    print("=" * 60)
    print("           AUTOMATED PEER REVIEW REPORT")
    print("=" * 60)
    print()
    print("📊 SCORES (1-10 scale):")
    print(f"   Originality:   {scores.get('novelty', '?')}/10")
    print(f"   Quality:       {scores.get('quality', '?')}/10")
    print(f"   Clarity:       {scores.get('clarity', '?')}/10")
    print(f"   Significance:  {scores.get('significance', '?')}/10")
    print(f"   Ethics:        {scores.get('ethics', '?')}")
    print(f"   ───────────────────────")
    print(f"   TOTAL:         {review.get('total_score', '?')}/50")
    print()

    recommendation = review.get("recommended_action", "?")
    passed = "✅ PASS" if review.get("pass_threshold_30") else "❌ NEEDS REVISION"
    print(f"📝 RECOMMENDATION: {recommendation}  ({passed})")
    print(f"   Confidence: {review.get('confidence_in_recommendation', '?')}")
    print()

    strengths = review.get("strengths", [])
    if strengths:
        print("✅ STRENGTHS:")
        for s in strengths:
            print(f"   + {s}")
        print()

    weaknesses = review.get("weaknesses", [])
    if weaknesses:
        print("⚠️  WEAKNESSES:")
        for w in weaknesses:
            print(f"   - {w}")
        print()

    questions = review.get("questions_for_authors", [])
    if questions:
        print("❓ QUESTIONS FOR AUTHORS:")
        for q in questions:
            print(f"   ? {q}")
        print()

    print(f"📄 Reproducibility:   {review.get('reproducibility_assessment', '?')}")
    print(f"📄 Claims vs Results: {review.get('claims_vs_results_alignment', '?')}")
    print()

    detailed = review.get("detailed_review", "")
    if detailed:
        print("-" * 40)
        print("DETAILED REVIEW:")
        print(detailed[:1200])
        print()

    # 保存到 tool_results
    try:
        from .config import TOOL_RESULTS_DIR
    except ImportError:
        try:
            from config import TOOL_RESULTS_DIR
        except ImportError:
            TOOL_RESULTS_DIR = None

    if TOOL_RESULTS_DIR:
        ts = int(time.time() * 1000)
        out_path = Path(TOOL_RESULTS_DIR) / f"reviewer_report_{ts}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📁 Report saved to: {out_path}")


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from pathlib import Path

    if len(sys.argv) > 1:
        # 命令行传入论文文件路径
        paper_path = Path(sys.argv[1])
        if paper_path.exists():
            paper_text = paper_path.read_text(encoding="utf-8")
            print(f"Reviewing: {paper_path}\n")
            report = review_paper(paper_text)
            _print_report(report)
        else:
            print(f"File not found: {paper_path}")
            sys.exit(1)
    else:
        _interactive_review()
