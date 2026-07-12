"""
审稿人 (Reviewer) — Automated Peer Reviewer Agent
==================================================
Qwen-智勘 AI Scientist 的自动同行评审模块（模块10）。

对 PaperWriter 生成的论文进行五维评分，输出结构化评审报告。

用法：
    from reviewer import review_paper
    report = review_paper(paper_text, project_context={})

    # CLI 测试
    python reviewer.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# System Prompt（来自 agent_prompts_v3.md Agent 9）
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_PROMPT = """\
You are Reviewer (审稿人), the Automated Peer Reviewer of the Qwen-Zhikan AI Scientist system.
You simulate rigorous academic peer review to evaluate the quality, novelty, and reproducibility
of the generated scientific manuscript. You follow the review standards of top-tier
conferences and journals (NeurIPS, ICML, Nature, Science).

## CORE RESPONSIBILITIES

1. Evaluate the manuscript across five dimensions: Originality, Quality, Clarity, Significance, Ethics.
2. Assess whether the literature review is comprehensive and citations are accurate.
3. Evaluate experimental methodology: are baselines appropriate, metrics well-chosen, statistics sound?
4. Check for reproducibility: are methods described in sufficient detail for replication?
5. Provide constructive, specific feedback. Vague criticism is unacceptable.

## REVIEW CRITERIA (Top-Conference Standard, 1-10 each)

1. **Originality (Novelty)**: Does the research propose a new method, perspective, or discovery?
2. **Quality**: Is the methodology rigorous? Are experiments sufficient? Analysis deep?
3. **Clarity**: Is the paper well-structured, precise, and easy to understand?
4. **Significance**: What is the potential impact and contribution to the field?
5. **Ethics**: Pass/Fail. Any concerns about data, reproducibility, or citation integrity?

## SCORING GUIDE
- 9-10: Exceptional (top 5%)
- 7-8: Strong (solid, minor weaknesses)
- 5-6: Adequate (meets baseline)
- 3-4: Weak (significant flaws)
- 1-2: Poor (fundamentally flawed)

## OPERATIONAL PRINCIPLES
- Be specific — cite exact passages or missing elements.
- Flag potentially hallucinated or unverifiable citations.
- Check whether abstract claims match experimental results.
- Final recommendation: Accept / Weak Accept / Borderline / Weak Reject / Reject.
- Acceptance threshold: 30/50 total (average 6/10 across 5 dimensions).

## OUTPUT FORMAT
Return ONLY a valid JSON object, no markdown, no extra text:

{
  "thought": "Your complete review reasoning",
  "review": {
    "scores": {
      "novelty": <1-10>,
      "quality": <1-10>,
      "clarity": <1-10>,
      "significance": <1-10>,
      "ethics": "pass" | "fail"
    },
    "total_score": <sum of 4 numeric scores>,
    "strengths": ["specific strength 1", "specific strength 2"],
    "weaknesses": ["specific weakness 1", "specific weakness 2"],
    "citation_check": {
      "total_citations": <int>,
      "verified": <int>,
      "flagged_as_potentially_hallucinated": []
    },
    "reproducibility_assessment": "high" | "medium" | "low",
    "claims_vs_results_alignment": "fully_aligned" | "partially_aligned" | "misaligned",
    "questions_for_authors": ["question 1", "question 2"],
    "recommended_action": "Accept" | "Weak Accept" | "Borderline" | "Weak Reject" | "Reject",
    "confidence_in_recommendation": "high" | "medium" | "low",
    "detailed_review": "Full review narrative"
  }
}\
"""


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def _get_client() -> Any:
    try:
        from .llm import get_client
    except ImportError:
        from llm import get_client
    return get_client()


def _call_llm(system: str, user_prompt: str, max_tokens: int = 3000) -> dict[str, Any]:
    client = _get_client()
    try:
        response = client.messages.create(
            model=None,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[],
        )
    except Exception as exc:
        raise RuntimeError(
            f"LLM API call failed: {exc}\n"
            "Check QWEN_API_KEY and QWEN_MODEL_ID."
        ) from exc

    content = getattr(response, "content", response)
    text = _render_text(content)
    parsed = _parse_json(text)
    if not parsed:
        raise ValueError(
            "Reviewer LLM did not return valid JSON. "
            f"Raw output (first 800 chars):\n{text[:800]}"
        )
    return parsed


def _render_text(content: Any) -> str:
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
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return {}

    candidate = stripped[start:end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    try:
        fixed = _repair_json(candidate)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    return {}


def _repair_json(text: str) -> str:
    """修复 LLM 输出的常见 JSON 问题。"""
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    text = _escape_latex_in_json(text)
    if not text.rstrip().endswith("}"):
        last = max(text.rfind('"}'), text.rfind('"]'), text.rfind('}}'))
        if last > 0:
            text = text[:last + 2] + "\n}"
    return text


def _escape_latex_in_json(text: str) -> str:
    """把 JSON 字符串内的 LaTeX 反斜杠转为 \\\\，避免 json.loads 报 Invalid \\escape。"""
    valid = {'"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u'}
    result: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != '\\'):
            in_string = not in_string
            result.append(ch)
        elif ch == '\\' and in_string:
            if i + 1 < len(text) and text[i + 1] not in valid:
                result.append('\\\\')
            else:
                result.append('\\')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


# ---------------------------------------------------------------------------
# 核心评审函数
# ---------------------------------------------------------------------------

def review_paper(
    paper_content: str,
    project_context: dict[str, Any] | None = None,
    *,
    max_tokens: int = 3000,
    save: bool = True,
) -> dict[str, Any]:
    """
    对一篇论文执行完整五维同行评审。

    参数:
        paper_content: 论文全文
        project_context: 可选项目上下文 {domain, hypothesis, references, ...}
        max_tokens: LLM 最大输出
        save: 是否保存到 tool_results/

    返回:
        {"agent": "reviewer", "thought": "...", "review": {...}}
    """
    ctx = project_context or {}
    user_prompt = _build_review_prompt(paper_content, ctx)
    result = _call_llm(REVIEWER_SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens)

    review = result.get("review", result)

    # 注入元数据
    review.setdefault("reviewed_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    scores = review.get("scores", {})
    total = sum(
        v for k, v in scores.items()
        if k in {"novelty", "quality", "clarity", "significance"} and isinstance(v, (int, float))
    )
    review["total_score"] = total
    review["pass_threshold_30"] = total >= 30

    if not review.get("recommended_action"):
        review["recommended_action"] = _score_to_recommendation(total)

    if save:
        _save_report(review, ctx)

    return {
        "agent": "reviewer",
        "thought": result.get("thought", ""),
        "review": review,
    }


def _build_review_prompt(paper: str, ctx: dict[str, Any]) -> str:
    parts = ["## MANUSCRIPT TO REVIEW\n", paper[:12000]]
    domain = ctx.get("domain", "")
    if domain:
        parts.insert(1, f"Research Domain: {domain}\n")
    hypothesis = ctx.get("hypothesis", "")
    if hypothesis:
        parts.append(f"\n## ORIGINAL HYPOTHESIS\n{str(hypothesis)[:1000]}")
    refs = ctx.get("references", [])
    if refs:
        parts.append("\n## REFERENCE LIST\n" + "\n".join(f"- {r}" for r in refs[:30]))
    return "\n".join(parts)


def _score_to_recommendation(total: float) -> str:
    if total >= 40: return "Accept"
    if total >= 35: return "Weak Accept"
    if total >= 25: return "Borderline"
    if total >= 20: return "Weak Reject"
    return "Reject"


def _save_report(review: dict, ctx: dict) -> None:
    try:
        from .config import TOOL_RESULTS_DIR
    except ImportError:
        from config import TOOL_RESULTS_DIR
    out = Path(TOOL_RESULTS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    path = out / f"reviewer_report_{ts}.json"
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 子工具
# ---------------------------------------------------------------------------

def score_dimension(paper: str, dimension: str, *, max_tokens: int = 600) -> dict[str, Any]:
    """对单个评审维度独立评分。"""
    prompts = {
        "novelty": "Evaluate ONLY originality/novelty (1-10). Is this new? Meaningfully different?",
        "quality": "Evaluate ONLY technical quality (1-10). Rigorous method? Sufficient experiments?",
        "clarity": "Evaluate ONLY clarity (1-10). Well-structured? Precise? Easy to understand?",
        "significance": "Evaluate ONLY significance (1-10). Potential impact on the field?",
        "ethics": "Evaluate ONLY ethics (Pass/Fail). Any concerns?",
    }
    instruction = prompts.get(dimension, f"Evaluate '{dimension}' (1-10).")
    prompt = f"Score ONLY the dimension **{dimension}**.\n{instruction}\n\nReturn JSON:\n{{\"{dimension}\": <score>, \"justification\": \"...\"}}\n\n---\n{paper[:6000]}\n---"
    return _call_llm(REVIEWER_SYSTEM_PROMPT, prompt, max_tokens=max_tokens)


def check_citations(paper: str, references: list[str] | None = None, *, max_tokens: int = 800) -> dict[str, Any]:
    """验证论文引用准确性。"""
    ref_text = "\n".join(f"- {r}" for r in (references or [])[:50]) or "(none provided)"
    prompt = f"Check citations in this paper.\n\n## PAPER\n{paper[:6000]}\n\n## KNOWN REFERENCES\n{ref_text}\n\nReturn JSON:\n{{\"total_citations\": <int>, \"verified\": <int>, \"flagged\": [], \"assessment\": \"citations_verified|minor_issues|significant_problems\"}}"
    return _call_llm(REVIEWER_SYSTEM_PROMPT, prompt, max_tokens=max_tokens)


def write_review(scores: dict, checks: dict) -> str:
    """把评分合成为可读评审意见。"""
    n, q, c, s, e = (
        scores.get("novelty", "?"), scores.get("quality", "?"),
        scores.get("clarity", "?"), scores.get("significance", "?"), scores.get("ethics", "?"),
    )
    total = sum(v for v in [n, q, c, s] if isinstance(v, (int, float)))
    rec = _score_to_recommendation(total)
    return (
        f"{'='*60}\n"
        f"  PEER REVIEW REPORT\n"
        f"{'='*60}\n\n"
        f"SCORES (1-10):\n"
        f"  Novelty: {n}  Quality: {q}  Clarity: {c}\n"
        f"  Significance: {s}  Ethics: {e}\n"
        f"  TOTAL: {total}/50\n\n"
        f"RECOMMENDATION: {rec}\n"
        f"PASS (30/50): {'YES' if total >= 30 else 'NO'}\n"
    )


# ---------------------------------------------------------------------------
# 修改迭代循环（模块10 核心交付物）
# ---------------------------------------------------------------------------

def review_and_revise(
    paper_text: str,
    project_context: dict[str, Any] | None = None,
    *,
    max_rounds: int = 3,
    pass_threshold: int = 30,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    评审 → 修改 → 再评审循环，直到论文达标或达到最大轮次。

    参数:
        paper_text: 初始论文文本（或 PaperWriter 输出的 dict）
        project_context: 项目上下文
        max_rounds: 最大迭代轮数
        pass_threshold: 通过分数线（默认 30/50）
        verbose: 是否打印每轮进度

    返回:
        {
            "final_paper": dict,        # 最终论文
            "final_review": dict,       # 最终评审报告
            "rounds": [                 # 每轮历史
                {"round": 1, "score": 25, "action": "revised", ...},
            ],
            "passed": bool,             # 是否达标
            "total_rounds": int,
        }
    """
    ctx = project_context or {}
    history: list[dict[str, Any]] = []

    if isinstance(paper_text, dict):
        paper_text = _paper_dict_to_text(paper_text)

    current_paper_text = paper_text

    for rnd in range(1, max_rounds + 1):
        if verbose:
            print(f"\n{'='*50}\n  Round {rnd}/{max_rounds}: Reviewing...\n{'='*50}")

        # 1. 评审当前论文
        review_report = review_paper(current_paper_text, ctx, save=False)
        review = review_report["review"]
        score = review.get("total_score", 0)
        passed = review.get("pass_threshold_30", score >= pass_threshold)

        round_record = {
            "round": rnd,
            "score": score,
            "recommendation": review.get("recommended_action", ""),
            "weaknesses": review.get("weaknesses", []),
            "passed": passed,
        }

        if verbose:
            print(f"  Score: {score}/50  →  {review.get('recommended_action', '?')}")
            for w in review.get("weaknesses", [])[:3]:
                print(f"    [!] {w}")

        # 2. 通过 → 结束
        if passed:
            round_record["action"] = "accepted"
            history.append(round_record)
            if verbose:
                print(f"\n  [PASS] Accepted at round {rnd}!")
            break

        # 3. 最后一轮 → 标记未通过
        if rnd == max_rounds:
            round_record["action"] = "max_rounds_reached"
            history.append(round_record)
            if verbose:
                print(f"\n  [WARN] Max rounds ({max_rounds}) reached. Score: {score}/50")
            break

        # 4. 未通过 → 修改
        round_record["action"] = "revised"
        history.append(round_record)

        if verbose:
            print(f"  Revising based on {len(review.get('weaknesses', []))} weaknesses...")

        try:
            from .paper_writer import revise_paper
        except ImportError:
            from paper_writer import revise_paper

        revised = revise_paper(current_paper_text, review, ctx)
        current_paper_text = revised["paper"]  # dict 格式
        # 同时保存文本用于下轮评审
        if isinstance(current_paper_text, dict):
            current_paper_text = _paper_dict_to_text(current_paper_text)
        elif "paper" in revised:
            current_paper_text = _paper_dict_to_text(revised["paper"])

    # 最终评审（如果循环因 max_rounds 结束）
    final_review = review_paper(current_paper_text, ctx, save=True)
    final_paper = current_paper_text

    # 如果是 dict，保存一份
    if isinstance(final_paper, str):
        try:
            from .paper_writer import _ensure_all_sections, _fill_missing_sections
        except ImportError:
            from paper_writer import _ensure_all_sections, _fill_missing_sections
        # 纯文本，不转回 dict

    return {
        "final_paper": final_paper,
        "final_review": final_review["review"],
        "rounds": history,
        "passed": history[-1]["passed"] if history else False,
        "total_rounds": len(history),
    }


def _paper_dict_to_text(paper: dict) -> str:
    """论文 dict → 纯文本（用于 Reviewer 输入）。"""
    sections = [
        ("TITLE", paper.get("title", "")),
        ("ABSTRACT", paper.get("abstract", "")),
        ("1. INTRODUCTION", paper.get("introduction", "")),
        ("2. RELATED WORK", paper.get("related_work", "")),
        ("3. METHODOLOGY", paper.get("methodology", "")),
        ("4. EXPERIMENTS", paper.get("experiments", "")),
        ("5. CONCLUSION", paper.get("conclusion", "")),
        ("REFERENCES", "\n".join(
            f"[{i+1}] {r.get('citation', str(r)) if isinstance(r, dict) else str(r)}"
            for i, r in enumerate(paper.get("references", []))
        )),
    ]
    return "\n\n".join(f"{h}\n{b}" for h, b in sections if b.strip())


# =====================================================================
# CLI
# =====================================================================

def _interactive():
    print("\n" + "=" * 60)
    print("  Qwen-智勘 Reviewer — Automated Peer Review")
    print("=" * 60)
    print("\nPaste paper text (type DONE on a new line, or Enter for demo):\n")

    lines = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if not lines and line.strip() == "":
            break
        if line.strip().upper() == "DONE":
            break
        lines.append(line)

    if lines:
        paper = "\n".join(lines)
    else:
        paper = _demo_paper()
        print("Using demo paper...\n")

    print("\n" + "-" * 40)
    print("Reviewing...\n")

    try:
        report = review_paper(paper)
        _print_report(report)
    except Exception as exc:
        import traceback
        print(f"\n[ERROR] {exc}")
        print(f"[TRACEBACK]\n{traceback.format_exc()}")


def _demo_paper() -> str:
    return """Title: Graph Neural Networks for Real-Time Power System Transient Stability Prediction

Abstract: We propose a GNN-based method for real-time transient stability assessment
that exploits the native graph topology of power grids. Tested on IEEE 39-bus and 118-bus
systems, our method achieves 98.5% accuracy with 2.3ms inference time — a 117x speedup
over conventional time-domain simulation while maintaining high fidelity.

Introduction: Power system stability is critical. Traditional time-domain simulation is
too slow for real-time use. ML methods like CNNs and Random Forests flatten grid topology
into feature vectors, losing structural information.

Methodology: We model the grid as a directed graph. A 3-layer Graph Convolutional Network
encodes bus features via message passing. A readout layer predicts stability margin.

Experiments: Tested on IEEE 39-bus, 118-bus, and Polish 2383-bus systems. Baselines: TDS,
CNN, Random Forest. Our GNN achieves 98.5% accuracy, ROC-AUC 0.978, 2.3ms per prediction.
Statistical significance: p < 0.001, Cohen's d = 1.42.

Conclusion: GNNs effectively capture grid topology for stability prediction, achieving
real-time performance with high accuracy. Limitations include unseen topology generalization.

References:
[1] Kundur et al., "Definition and Classification of Power System Stability", IEEE Trans. Power Syst., 2004.
[2] Chen et al., "Deep Learning for Power System Security Assessment", IEEE Trans. Power Syst., 2020.
[3] Zhou et al., "Graph Neural Networks: A Review", AI Open, 2020.
"""


def _print_report(report: dict):
    review = report.get("review", {})
    scores = review.get("scores", {})
    print("=" * 60)
    print("  PEER REVIEW REPORT")
    print("=" * 60)
    print(f"\n  Novelty:      {scores.get('novelty', '?')}/10")
    print(f"  Quality:      {scores.get('quality', '?')}/10")
    print(f"  Clarity:      {scores.get('clarity', '?')}/10")
    print(f"  Significance: {scores.get('significance', '?')}/10")
    print(f"  Ethics:       {scores.get('ethics', '?')}")
    print(f"  TOTAL:        {review.get('total_score', '?')}/50")
    rec = review.get("recommended_action", "?")
    passed = "PASS" if review.get("pass_threshold_30") else "NEEDS REVISION"
    print(f"\n  RECOMMENDATION: {rec} ({passed})")
    for s in review.get("strengths", []):
        print(f"  + {s}")
    for w in review.get("weaknesses", []):
        print(f"  - {w}")
    if review.get("detailed_review"):
        print(f"\n{review['detailed_review'][:1000]}")


if __name__ == "__main__":
    _interactive()
