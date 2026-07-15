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
# 多 Reviewer 人格（ARISE + GAR 方法）
# 3 种评审人格独立评分后取中位数，模拟顶会多评审员机制
# ---------------------------------------------------------------------------

# DeepReviewer 2.0 锚定注释要求（追加到每个 reviewer prompt 后面）
_ANCHORING_RULES = """
## CRITICAL: ANCHORED ANNOTATIONS (DeepReviewer 2.0 output contract)

Every weakness and strength you list MUST be anchored to specific locations in the paper:
- Format: "[Section X, paragraph Y] 'exact quoted text' → your assessment"
- Example: "[Experiments, paragraph 2] 'Our method achieves 95.8% accuracy' → The paper does not report confidence intervals for this number, making the claim unverifiable."
- At least 2 weaknesses and 1 strength must have anchored citations.
- Vague statements like "the paper needs improvement" without a specific anchor are UNACCEPTABLE.
- If you cannot find a specific anchor for a criticism, do NOT include that criticism.
"""

REVIEWER_STRICT_PROMPT = REVIEWER_SYSTEM_PROMPT + _ANCHORING_RULES + """

## YOUR PERSONA: THE STRICT REVIEWER

You are known as the toughest reviewer in your field. Your standards:
- Score 2-3 points LOWER than what a typical reviewer would give.
- Actively search for fundamental flaws: logical gaps, missing baselines, overclaimed results.
- Your default assumption is "reject until proven otherwise."
- You believe most papers overstate their contributions.
- If there is ANY ambiguity in the methodology, assume the worst and flag it.
- You have seen too many papers with the same "novel" idea — prove it's truly new.
"""

REVIEWER_CONSTRUCTIVE_PROMPT = REVIEWER_SYSTEM_PROMPT + _ANCHORING_RULES + """

## YOUR PERSONA: THE CONSTRUCTIVE REVIEWER

You are the reviewer authors hope to get. Your approach:
- Score fairly and balanced — your scores most closely align with human expert consensus.
- For every weakness, provide a concrete, actionable suggestion for improvement.
- Acknowledge genuine contributions even in flawed papers.
- Your goal is to make the paper BETTER, not just to judge it.
- Distinguish between fatal flaws (must fix) and minor issues (nice to fix).
- If the paper has potential, give the authors a clear roadmap to acceptance.
"""

REVIEWER_DETAIL_PROMPT = REVIEWER_SYSTEM_PROMPT + _ANCHORING_RULES + """

## YOUR PERSONA: THE DETAIL-ORIENTED REVIEWER

You are obsessive about technical precision. Your focus:
- Scrutinize EVERY number: are decimal places consistent? Do percentages sum to 100%?
- Verify statistical tests: is the test appropriate for the data? Are assumptions checked?
- Check mathematical notation: are variables defined before use? Are equations dimensionally consistent?
- Audit reproducibility: could a PhD student replicate this from the text alone?
- Hunt for missing details: hyperparameters, random seeds, hardware specs, software versions.
- Score based on TECHNICAL RIGOR, not narrative polish. Beautiful writing with sloppy methods gets a low score from you.
"""


# ---------------------------------------------------------------------------
# 多 Reviewer 投票评审（主函数）
# ---------------------------------------------------------------------------

def review_paper_panel(
    paper_content: str,
    project_context: dict[str, Any] | None = None,
    *,
    max_tokens: int = 3000,
    save: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    ARISE 风格的多 Reviewer 独立评审 + 投票共识。

    3 种评审人格独立评分后取中位数：
    1. Strict Reviewer  — 严格型，评分偏低，专注于找缺陷
    2. Constructive Reviewer — 建设型，评分最接近人类共识
    3. Detail Reviewer  — 细节型，专注于方法论和数字准确性

    参数:
        paper_content: 论文全文
        project_context: 项目上下文
        max_tokens: 每位 Reviewer 的 LLM 最大输出
        save: 是否保存报告
        verbose: 是否打印进度

    返回:
        {
            "agent": "reviewer_panel",
            "review": consensus_review,     # 综合评审报告
            "individual_reviews": [...],     # 3 位独立评审的原始报告
            "scores_summary": {              # 分数对比
                "strict": 22, "constructive": 28, "detail": 25,
                "median": 25, "mean": 25.0, "range": 6
            },
            "consensus_strengths": [...],    # 至少2人共同认可的优点
            "consensus_weaknesses": [...],    # 至少2人共同认可的缺点
        }
    """
    ctx = project_context or {}
    personas = [
        ("strict", "Strict Reviewer", REVIEWER_STRICT_PROMPT),
        ("constructive", "Constructive Reviewer", REVIEWER_CONSTRUCTIVE_PROMPT),
        ("detail", "Detail Reviewer", REVIEWER_DETAIL_PROMPT),
    ]

    individual_reviews: list[dict] = []

    # ── 独立评审 ──
    for i, (pid, pname, psystem) in enumerate(personas):
        if verbose:
            print(f"  [Reviewer {i+1}/3] {pname}...")

        user_prompt = _build_review_prompt(paper_content, ctx)
        try:
            result = _call_llm(psystem, user_prompt, max_tokens=max_tokens)
        except Exception as e:
            result = {"review": {"scores": {}, "strengths": [], "weaknesses": [],
                      "error": str(e)[:200]}}

        review = result.get("review", result)
        review["persona_id"] = pid
        review["persona_name"] = pname
        individual_reviews.append(review)

        if verbose:
            scores = review.get("scores", {})
            total = sum(v for k, v in scores.items()
                       if k in {"novelty", "quality", "clarity", "significance"}
                       and isinstance(v, (int, float)))
            print(f"    Score: {total}/40 ({scores.get('novelty','?')},{scores.get('quality','?')},{scores.get('clarity','?')},{scores.get('significance','?')})")

    # ── 计算中位数分数 ──
    def _total(r: dict) -> float:
        s = r.get("scores", {})
        return float(sum(v for k, v in s.items()
                    if k in {"novelty", "quality", "clarity", "significance"}
                    and isinstance(v, (int, float))))

    totals = sorted([_total(r) for r in individual_reviews])
    median_total = totals[1]  # 3个人的中位数 = 排序后第2个
    mean_total = sum(totals) / 3
    score_range = totals[2] - totals[0]

    # 取最接近中位数的评审作为"锚点"
    anchor_idx = min(range(3), key=lambda i: abs(_total(individual_reviews[i]) - median_total))
    anchor = individual_reviews[anchor_idx]

    # ── 提取共识（至少2人认可的优点/缺点） ──
    consensus_strong, consensus_weak = _extract_consensus(individual_reviews)

    # ── DeepReviewer 2.0 锚定验证 ──
    anchoring = _validate_anchoring(individual_reviews)
    if verbose and not anchoring["quality_pass"]:
        print(f"  [!] Anchoring check: {anchoring['per_reviewer']}")

    # ── 综合评审报告 ──
    consensus_review = {
        "scores": {
            "novelty": round(sum(r.get("scores", {}).get("novelty", 0) for r in individual_reviews) / 3),
            "quality": round(sum(r.get("scores", {}).get("quality", 0) for r in individual_reviews) / 3),
            "clarity": round(sum(r.get("scores", {}).get("clarity", 0) for r in individual_reviews) / 3),
            "significance": round(sum(r.get("scores", {}).get("significance", 0) for r in individual_reviews) / 3),
            "ethics": anchor.get("scores", {}).get("ethics", "pass"),
        },
        "total_score": int(median_total),
        "mean_score": round(mean_total, 1),
        "pass_threshold_30": median_total >= 30,
        "strengths": consensus_strong[:5],
        "weaknesses": consensus_weak[:5],
        "citation_check": anchor.get("citation_check", {}),
        "reproducibility_assessment": anchor.get("reproducibility_assessment", "medium"),
        "claims_vs_results_alignment": anchor.get("claims_vs_results_alignment", "partially_aligned"),
        "questions_for_authors": anchor.get("questions_for_authors", [])[:5],
        "recommended_action": _score_to_recommendation(median_total),
        "confidence_in_recommendation": "high" if score_range <= 6 else "medium" if score_range <= 12 else "low",
        "detailed_review": (
            f"PANEL CONSENSUS (3 independent reviewers, score range: {totals[0]}-{totals[2]}, median: {median_total})\n\n"
            f"Strict Reviewer ({totals[0]}): {individual_reviews[0].get('detailed_review', '')[:500]}\n\n"
            f"Constructive Reviewer ({totals[1]}): {individual_reviews[1].get('detailed_review', '')[:500]}\n\n"
            f"Detail Reviewer ({totals[2]}): {individual_reviews[2].get('detailed_review', '')[:500]}"
        ),
    }

    if save:
        _save_report(consensus_review, ctx)

    if verbose:
        print(f"  Panel consensus: median={median_total}/40 ({consensus_review['recommended_action']})")
        print(f"  Score range: {totals[0]}-{totals[2]}, agreement: {consensus_review['confidence_in_recommendation']}")

    return {
        "agent": "reviewer_panel",
        "thought": f"3-reviewer panel: Strict={totals[0]}, Constructive={totals[1]}, Detail={totals[2]}. Median consensus: {median_total}.",
        "review": consensus_review,
        "individual_reviews": individual_reviews,
        "scores_summary": {
            "strict": totals[0], "constructive": totals[1], "detail": totals[2],
            "median": median_total, "mean": round(mean_total, 1), "range": score_range,
        },
        "consensus_strengths": consensus_strong,
        "consensus_weaknesses": consensus_weak,
        "anchoring_quality": anchoring,
    }


def _validate_anchoring(reviews: list[dict]) -> dict[str, Any]:
    """DeepReviewer 2.0 输出契约：检查每份评审是否包含锚定注释。"""
    import re as _re
    anchor_pattern = _re.compile(r"\[(?:Section |Introduction|Related|Method|Experiment|Conclusion)[^\]]*\]", _re.IGNORECASE)
    results = {}
    for i, r in enumerate(reviews):
        pid = r.get("persona_id", f"reviewer_{i}")
        weaknesses = r.get("weaknesses", [])
        strengths = r.get("strengths", [])
        all_text = " ".join(str(w) for w in weaknesses + strengths)
        anchor_count = len(anchor_pattern.findall(all_text))
        results[pid] = {
            "anchored": anchor_count >= 2,
            "anchor_count": anchor_count,
        }
    total_anchored = sum(1 for v in results.values() if v["anchored"])
    return {
        "all_anchored": total_anchored >= 2,  # 至少2位评审有锚定
        "per_reviewer": results,
        "quality_pass": total_anchored >= 2,
    }


def _extract_consensus(reviews: list[dict]) -> tuple[list[str], list[str]]:
    """从3位评审中提取至少2人共同认可的优缺点（模糊匹配）。"""
    all_strong: list[str] = []
    all_weak: list[str] = []
    for r in reviews:
        for s in r.get("strengths", []):
            all_strong.append(s[:200])
        for w in r.get("weaknesses", []):
            all_weak.append(w[:200])

    def _consensus(items: list[str]) -> list[str]:
        if not items:
            return []
        clusters: list[list[str]] = []
        for item in items:
            matched = False
            for cluster in clusters:
                if any(_overlap(item, existing) > 0.35 for existing in cluster):
                    cluster.append(item)
                    matched = True
                    break
            if not matched:
                clusters.append([item])
        result = []
        for cluster in clusters:
            if len(cluster) >= 2:
                cluster.sort(key=len, reverse=True)
                result.append(cluster[0])
        return result[:5]

    return _consensus(all_strong), _consensus(all_weak)


def _overlap(a: str, b: str) -> float:
    """两个字符串的词汇重叠率（小写比较）。"""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


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
        review_report = review_paper_panel(current_paper_text, ctx, save=False, verbose=verbose)
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
    final_review = review_paper_panel(current_paper_text, ctx, save=True, verbose=False)
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
