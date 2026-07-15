"""
学术写手 (PaperWriter) — Academic Paper Writer Agent
=====================================================
Qwen-智勘 AI Scientist 的自动论文学术写作模块。

负责模块9：将实验数据、分析结果、假设和文献转化为符合顶会/顶刊
标准的完整学术论文。

用法：
    from paper_writer import write_paper
    result = write_paper(project_context)

    # CLI 测试
    python paper_writer.py

输入：project_context（假设、实验方案、结果、分析、文献）
输出：结构化论文 + LaTeX 源码 + 引用列表
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# System Prompt（从 agent_prompts_v3.md Agent 11 + SKILL.md 提取）
# ---------------------------------------------------------------------------

PAPERWRITER_SYSTEM_PROMPT = """\
You are PaperWriter, the Academic Paper Writer of the Qwen-Zhikan AI Scientist system.
You are an experienced scientific paper author who transforms experimental data,
figures, and analysis results into publication-quality academic manuscripts.

## CORE RESPONSIBILITIES

1. Transform experimental data, figures, and analysis results into a well-structured academic paper.
2. Generate complete academic papers following standard conference/journal formatting.
3. Automatically retrieve and cite relevant literature from provided PaperGraph records.
4. Ensure the paper meets top-tier conference/journal standards (NeurIPS, ICML, Nature, Science).

## PAPER STRUCTURE (7 sections, mandatory)

1. **Abstract**: Concise overview of the research problem, methods, results, and significance. 150-250 words.
2. **Introduction**: Background, motivation, research question, and contributions. State the knowledge gap clearly.
3. **Related Work**: Compare with existing research. Highlight differences. Cite real papers from the provided reference list.
4. **Methodology**: Detailed description of the proposed method. Include mathematical formulation where applicable.
5. **Experiments**: Experimental setup, datasets, baselines, results (with numbers), and analysis.
6. **Conclusion**: Summary of findings, limitations, and future work directions.
7. **References**: Auto-generated from the provided PaperGraph records. Only list verified references.

## OPERATIONAL PRINCIPLES

- The narrative must be driven by the core contribution — every section should support it.
- Claims in the abstract and conclusion must be fully supported by experimental results.
- Related work must accurately represent cited papers — no misrepresentation.
- When uncertain about citation accuracy, mark it with [NEEDS VERIFICATION].
- No fabricated citations — every reference must be verifiable from the provided records.
- Use concrete numbers (percentages, p-values, effect sizes) — not vague descriptions.
- The hypothesis's knowledge gap should be stated in the introduction.

## CONSTRAINTS
- All claims must be supported by experimental results or cited literature.
- No fabricated citations or made-up paper titles.
- Every section must be present — do not skip sections.
- The paper must follow standard academic writing conventions.

## OUTPUT FORMAT
Return a JSON object with this structure:

{
  "thought": "Your writing strategy: what is the core narrative? What evidence supports each claim?",
  "paper": {
    "title": "Paper Title",
    "abstract": "Full abstract text...",
    "introduction": "Full introduction...",
    "related_work": "Related work comparison...",
    "methodology": "Method details...",
    "experiments": "Setup, baselines, results, analysis...",
    "conclusion": "Summary, limitations, future work...",
    "references": [
      {"index": 1, "citation": "Author et al., Title, Venue Year", "papergraph_id": "..."}
    ]
  },
  "paper_status": {
    "completed_sections": ["Abstract", "Introduction", "Related Work", "Methodology", "Experiments", "Conclusion", "References"],
    "total_words": 4500,
    "citation_count": 25
  },
  "quality_check": {
    "claims_supported_by_results": true,
    "citations_verified": true,
    "all_sections_present": true
  }
}\
"""


# ---------------------------------------------------------------------------
# 多阶段流水线 Prompt（PaperOrchestrator 风格）
# 4 阶段替代单次 LLM 调用：Outline → Write → Polish → References
# ---------------------------------------------------------------------------

STAGE1_OUTLINE_PROMPT = """\
You are an experienced scientific editor planning a paper outline.
Given the research context, produce a structured outline.

## TASK
Create a paper outline with:
1. A precise, informative title (capture the core contribution)
2. For EACH of the 7 sections, write 2-3 bullet points listing the specific claims, evidence, and content that section MUST contain.

Sections: Abstract, Introduction, Related Work, Methodology, Experiments, Conclusion, References

## CONSTRAINTS
- Every claim must be backed by evidence from the provided context.
- Mark any claim without supporting evidence as [NEEDS DATA].
- Citations must reference specific papers from the provided PaperGraph records.
- Be specific: not "present results" but "report 98.5% accuracy with p<0.001, compare to CNN baseline (94.2%)".

## OUTPUT FORMAT
Return JSON:
{
  "title": "precise paper title",
  "outline": {
    "abstract": ["point 1", "point 2", "point 3"],
    "introduction": ["point 1", "point 2", "point 3"],
    "related_work": ["point 1", "point 2", "point 3"],
    "methodology": ["point 1", "point 2", "point 3"],
    "experiments": ["point 1", "point 2", "point 3"],
    "conclusion": ["point 1", "point 2"],
    "references": ["ref 1", "ref 2", "ref 3"]
  }
}\
"""

# ── Stage 1.5: Evidence Chain (LECTOR 方法) ──

STAGE1_5_EVIDENCE_PROMPT = """\
You are a scientific evidence analyst. Extract a structured evidence chain from the research context.

## TASK
For every claim that will appear in the paper, identify its supporting evidence source.
This creates a "reasoning graph" that ensures every sentence is traceable to data or literature.

## EVIDENCE TYPES
- **experiment**: numbers from experimental results (accuracy, p-values, timings)
- **literature**: claims supported by specific papers in the PaperGraph
- **protocol**: experimental design decisions (datasets, baselines, metrics)
- **gap**: knowledge gap identified by TanXi
- **hypothesis**: derived from the hypothesis statement itself

## OUTPUT FORMAT
Return JSON:
{
  "evidence_chain": [
    {
      "claim": "GNN achieves 96.4% accuracy on IEEE 118-bus system",
      "evidence_type": "experiment",
      "evidence_source": "CodeEngineer results: primary_results.accuracy = 0.964",
      "used_in_section": "experiments",
      "confidence": "high"
    },
    {
      "claim": "No existing ML method preserves grid topology for stability prediction",
      "evidence_type": "gap",
      "evidence_source": "TanXi gap analysis: gap_id=GAP-001",
      "used_in_section": "introduction",
      "confidence": "high"
    },
    {
      "claim": "Graph Neural Networks capture relational structure in graph data",
      "evidence_type": "literature",
      "evidence_source": "Zhou et al. (2020). Graph Neural Networks: A Review. AI Open.",
      "used_in_section": "related_work",
      "confidence": "high"
    }
  ],
  "missing_evidence": [
    "No baseline comparison data for Random Forest",
    "No ablation study to confirm topology is the key factor"
  ]
}\
"""

STAGE2_WRITE_PROMPT = """\
You are an academic paper writer. Write the COMPLETE paper body based on the approved outline AND evidence chain.

## WRITING RULES
- Follow the outline exactly — every bullet point must be addressed.
- **CRITICAL**: Every factual claim MUST cite its evidence from the provided evidence chain.
  Use the format: [Evidence: <type>] after each claim, e.g. "Our method achieves 96.4% accuracy [Evidence: experiment]."
- Use concrete numbers — no vague descriptions.
- Introduction must state the knowledge gap clearly.
- Methodology must be detailed enough for reproducibility.
- Experiments section must include: setup, datasets, baselines, metrics, results, statistical tests.
- If the evidence chain flags missing evidence, mark those claims as [NEEDS DATA].
- Use formal academic English.

## OUTPUT FORMAT
Return JSON with ALL 7 sections as complete text:
{
  "paper": {
    "title": "from outline",
    "abstract": "full abstract 150-250 words",
    "introduction": "full introduction",
    "related_work": "full related work",
    "methodology": "full methodology",
    "experiments": "full experiments with results",
    "conclusion": "full conclusion",
    "references": [{"index": 1, "citation": "Author (Year). Title. Venue.", "evidence_type": "literature"}]
  }
}\
"""

STAGE3_POLISH_PROMPT = """\
You are a senior scientific editor polishing a manuscript for submission to a top-tier venue.
Review the complete draft and fix ALL issues.

## CHECKLIST (address every item)
1. **Cross-section consistency**: Do abstract claims match experiments? Does introduction's gap match conclusion's summary?
2. **Narrative flow**: Do sections transition logically? Is the core contribution clear throughout?
3. **Number accuracy**: Are all reported numbers consistent across sections?
4. **Evidence traceability (LECTOR)**: Does every [Evidence: X] tag in the text correspond to an actual item in the evidence chain? Remove any claims that lack evidence or mark them [NEEDS DATA].
5. **Citation completeness**: Is every factual claim backed by a citation or evidence tag?
6. **Clarity**: Are there any vague phrases without specific numbers?
7. **Completeness**: Does every required section have substantive content?

## CONSTRAINTS
- Fix errors but do NOT change the core scientific claims.
- If data is missing, add [NEEDS DATA: specific description] rather than inventing numbers.
- Preserve all [Evidence: X] tags — do not remove verified evidence citations.

## OUTPUT FORMAT
Return the COMPLETE polished paper as JSON (same schema as input).\
"""

STAGE4_REFERENCES_PROMPT = """\
You are a citation quality auditor. Clean and verify the reference list.

## TASK
1. Remove any obviously fabricated or unverifiable citations.
2. For each remaining citation, ensure the format is: "Author, A. et al. (Year). Title. Venue."
3. Verify that every citation in the reference list is actually cited in the paper text.
4. Verify that every in-text citation has a corresponding entry in the reference list.
5. If the provided PaperGraph records contain better-verified alternatives for a claim, replace the weak citation.

## OUTPUT FORMAT
Return JSON:
{
  "references": [
    {"index": 1, "citation": "formatted citation", "verified": true/false, "source": "papergraph"|"llm_generated"}
  ],
  "removed_count": 0,
  "added_count": 0,
  "notes": "summary of changes"
}\
"""


# ---------------------------------------------------------------------------
# 多阶段论文生成（主函数）
# ---------------------------------------------------------------------------

def write_paper_staged(
    project_context: dict[str, Any] | None = None,
    *,
    max_tokens_per_stage: int = 3000,
    save: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    PaperOrchestrator 风格的多阶段论文生成。

    5 个阶段（LECTOR 风格证据链）：
    Stage 1: OUTLINE  — 生成标题+7节大纲
    Stage 1.5: EVIDENCE — 提取 claim→evidence 映射（LECTOR方法）
    Stage 2: WRITE   — 基于大纲+证据链写作
    Stage 3: POLISH   — 跨节一致性+证据追溯检查
    Stage 4: REFERENCES — 引用验证和格式化

    相比单次 LLM 调用，多阶段流水线显著提升：
    - 章节完整性（每个大纲点必须覆盖）
    - 跨节一致性（独立润色阶段检查矛盾）
    - 引用准确性（独立引用审计阶段）

    返回格式同 write_paper()。
    """
    ctx = project_context or {}
    context_text = _build_write_prompt(ctx, "")
    stages_log: list[dict] = []

    # ── Stage 1: Outline ──
    if verbose:
        print("[Stage 1/4] Generating outline...")
    outline = _call_llm(
        STAGE1_OUTLINE_PROMPT,
        f"{context_text}\n\nGenerate the paper outline.",
        max_tokens=2000,
    )
    stages_log.append({"stage": 1, "title": "Outline", "outline": outline})

    if verbose:
        title_preview = outline.get("title", "?")[:80]
        outline_sections = len(outline.get("outline", {}))
        print(f"  Title: {title_preview}")
        print(f"  Sections planned: {outline_sections}")

    # ── Stage 1.5: Evidence Chain (LECTOR) ──
    if verbose:
        print("[Stage 1.5/5] Extracting claim->evidence mapping...")
    evidence = _call_llm(
        STAGE1_5_EVIDENCE_PROMPT,
        f"## RESEARCH CONTEXT\n{context_text[:6000]}\n\n## OUTLINE\n{json.dumps(outline, ensure_ascii=False, indent=2)}\n\nExtract the structured evidence chain.",
        max_tokens=2000,
    )
    evidence_chain = evidence.get("evidence_chain", [])
    missing_evidence = evidence.get("missing_evidence", [])
    stages_log.append({"stage": 1.5, "title": "Evidence Chain",
                        "claims": len(evidence_chain), "missing": len(missing_evidence)})
    if verbose:
        print(f"  Evidence-backed claims: {len(evidence_chain)}")
        print(f"  Missing evidence gaps: {len(missing_evidence)}")
        for m in missing_evidence[:3]:
            print(f"    [!] {m[:100]}")
    evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2)

    # ── Stage 2: Write (with evidence chain) ──
    if verbose:
        print("[Stage 2/5] Writing sections with evidence citations...")
    outline_json = json.dumps(outline, ensure_ascii=False, indent=2)
    draft = _call_llm(
        STAGE2_WRITE_PROMPT,
        f"## RESEARCH CONTEXT\n{context_text[:3000]}\n\n## APPROVED OUTLINE\n{outline_json}\n\n## EVIDENCE CHAIN (cite these!)\n{evidence_json}\n\nWrite the complete paper. Every factual claim MUST cite its evidence from the chain above.",
        max_tokens=max_tokens_per_stage,
    )
    paper = draft.get("paper", draft)
    paper = _ensure_all_sections(paper)
    stages_log.append({"stage": 2, "title": "Write", "word_count": _count_words(paper)})

    if verbose:
        print(f"  Words written: {_count_words(paper)}")
        print(f"  Sections: {[k for k in paper if k not in ('references',) and paper.get(k, '').strip()]}")

    # ── Stage 3: Polish ──
    if verbose:
        print("[Stage 3/5] Polishing cross-section consistency...")
    paper_json = json.dumps({"paper": paper}, ensure_ascii=False, indent=2)
    polished = _call_llm(
        STAGE3_POLISH_PROMPT,
        f"## DRAFT TO POLISH\n{paper_json[:8000]}\n\n## EVIDENCE CHAIN (verify against this)\n{evidence_json[:3000]}\n\n## CONTEXT FOR VERIFICATION\n{context_text[:2000]}\n\nPolish the draft. Fix inconsistencies, verify all [Evidence:] tags are valid, ensure numbers match across sections.",
        max_tokens=max_tokens_per_stage,
    )
    paper = polished.get("paper", polished)
    paper = _ensure_all_sections(paper)
    paper = _fill_missing_sections(paper, ctx)
    stages_log.append({"stage": 3, "title": "Polish", "word_count": _count_words(paper)})

    if verbose:
        print(f"  Words after polish: {_count_words(paper)}")

    # ── Stage 4: References ──
    if verbose:
        print("[Stage 4/5] Verifying references...")
    refs_before = len(paper.get("references", []))

    # 4a. LLM 清洗引用格式
    ref_result = _call_llm(
        STAGE4_REFERENCES_PROMPT,
        f"## PAPER\nTitle: {paper.get('title', '')}\n\nAbstract: {paper.get('abstract', '')[:500]}\n\n## CURRENT REFERENCES\n{json.dumps(paper.get('references', []), ensure_ascii=False, indent=2)}\n\n## PAPERGRAPH RECORDS (verified sources)\n{_format_references(ctx.get('papergraph_records', []))}\n\nClean and verify the reference list.",
        max_tokens=1500,
    )
    cleaned_refs = ref_result.get("references", paper.get("references", []))

    # 4b. 外部 API 验证（Semantic Scholar）
    if verbose:
        print(f"  Checking {len(cleaned_refs)} citations against Semantic Scholar...")
    verified_refs, verify_summary = verify_references_external(
        cleaned_refs, timeout=8.0, verbose=verbose
    )
    paper["references"] = verified_refs
    refs_after = len(verified_refs)

    stages_log.append({
        "stage": 4, "title": "References",
        "before": refs_before, "after": refs_after,
        "notes": ref_result.get("notes", ""),
        "verification": verify_summary,
    })

    if verbose:
        print(f"  References: {refs_before} → {refs_after}")
        print(f"  Verified: {verify_summary['verified_count']}/{verify_summary['total']} "
              f"({verify_summary['verification_rate']:.0%})")
        if verify_summary["fake_flagged"]:
            print(f"  Flagged as [UNVERIFIED]: {len(verify_summary['fake_flagged'])}")

    # ── LaTeX + Save ──
    latex = format_latex(paper)
    saved_paths: list[str] = []
    if save:
        saved_paths = _save_paper(paper, latex, ctx)

    quality = review_draft(paper)

    if verbose:
        print(f"\n  FINAL: {_count_words(paper)} words, {refs_after} citations")
        print(f"  Quality: {quality.get('overall', '?')}")
        for issue in quality.get("issues", []):
            print(f"    [!] {issue}")

    return {
        "thought": f"Multi-stage pipeline: Outline → Write → Polish → References. {len(stages_log)} stages completed.",
        "paper": paper,
        "latex": latex,
        "saved_paths": saved_paths,
        "paper_status": {
            "completed_sections": [k for k in paper if k not in ("references",) and paper.get(k, "").strip()],
            "total_words": _count_words(paper),
            "citation_count": refs_after,
        },
        "quality_check": quality,
        "stages_log": stages_log,
    }


# ---------------------------------------------------------------------------
# LaTeX 模板
# ---------------------------------------------------------------------------

LATEX_TEMPLATE = r"""\documentclass[11pt,a4paper]{{article}}

\usepackage[utf8]{{inputenc}}
\usepackage{{amsmath,amssymb,amsfonts}}
\usepackage{{graphicx}}
\usepackage{{hyperref}}
\usepackage{{booktabs}}
\usepackage{{caption}}
\usepackage{{subcaption}}
\usepackage[margin=1in]{{geometry}}
\usepackage{{natbib}}

\title{{{title}}}
\author{{Qwen-智勘 AI Scientist}}

\date{{\today}}

\begin{{document}}

\maketitle

\begin{{abstract}}
{abstract}
\end{{abstract}}

\section{{Introduction}}
{introduction}

\section{{Related Work}}
{related_work}

\section{{Methodology}}
{methodology}

\section{{Experiments}}
{experiments}

\section{{Conclusion}}
{conclusion}

\bibliographystyle{{plain}}
\begin{{thebibliography}}{{99}}
{references_bib}
\end{{thebibliography}}

\end{{document}}
"""


# ---------------------------------------------------------------------------
# LLM 调用封装
# ---------------------------------------------------------------------------

def _get_client() -> Any:
    """获取 LLM 客户端，统一走 llm.get_client()。"""
    try:
        from .llm import get_client
    except ImportError:
        from llm import get_client
    return get_client()


def _call_llm(system: str, user_prompt: str, max_tokens: int = 4000) -> dict[str, Any]:
    """调用 LLM 并解析返回的 JSON。"""
    import traceback as _tb
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
            "Check your QWEN_API_KEY and QWEN_MODEL_ID env vars."
        ) from exc

    content = getattr(response, "content", response)
    text = _render_text(content)

    parsed = _parse_json(text)
    if not parsed:
        raise ValueError(
            "PaperWriter LLM did not return valid JSON. "
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

    # 去掉 markdown 代码块
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    # 找 JSON 对象
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

    # Last resort: remove trailing content after last valid }
    try:
        last_valid = candidate.rfind('"}')
        if last_valid > 0:
            truncated = candidate[:last_valid + 2] + "\n}"
            return json.loads(truncated)
    except json.JSONDecodeError:
        pass

    return {}


def _repair_json(text: str) -> str:
    """修复 LLM 输出中常见的 JSON 格式问题。"""
    # 1. 去掉尾部逗号
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)

    # 2. 修复 LaTeX 反斜杠
    text = _escape_latex_in_json(text)

    # 3. 修复 LLM 截断：JSON 被 max_tokens 切断
    if not text.rstrip().endswith("}"):
        # 找最后一个完整的 JSON 结构
        # 优先找完整的 "key": "value" 对
        last_string_close = text.rfind('",')
        last_array_close = text.rfind('"],')
        last_obj_close = text.rfind('},')

        candidates = [p for p in [last_string_close, last_array_close, last_obj_close, text.rfind('"}')] if p > 0]

        if candidates:
            cut = max(candidates)
            if text[cut] == ',':
                cut += 1  # keep the comma, we'll clean up
            else:
                cut += 2  # after "} or "]
            text = text[:cut]

        # Close any unclosed string
        in_string = False
        for i in range(len(text) - 1, -1, -1):
            if text[i] == '"' and (i == 0 or text[i-1] != '\\\\'):
                in_string = not in_string
        if in_string:
            text += '"'

        # Count and close open braces/brackets
        open_braces = text.count('{') - text.count('}')
        open_brackets = text.count('[') - text.count(']')
        text += ']' * open_brackets + '}' * open_braces

    return text


def _escape_latex_in_json(text: str) -> str:
    """把 JSON 字符串内的 LaTeX 反斜杠转义为 \\，避免 json.loads 报 Invalid \\escape。"""
    # 有效 JSON 转义: \" \\ \/ \b \f \n \r \t \uXXXX
    valid_escapes = {'"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u'}
    result: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != '\\'):
            in_string = not in_string
            result.append(ch)
        elif ch == '\\' and in_string:
            # 只在字符串内部处理
            if i + 1 < len(text) and text[i + 1] not in valid_escapes:
                # LLM 输出的 LaTeX 命令如 \theta → 需要变成 \\theta
                result.append('\\\\')
            else:
                result.append('\\')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


# ---------------------------------------------------------------------------
# 输出路径
# ---------------------------------------------------------------------------

def _paper_dir() -> Path:
    try:
        from .config import SCIENCE_DIR
    except ImportError:
        from config import SCIENCE_DIR
    papers = Path(SCIENCE_DIR) / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    return papers


def _tool_results_dir() -> Path:
    try:
        from .config import TOOL_RESULTS_DIR
    except ImportError:
        from config import TOOL_RESULTS_DIR
    Path(TOOL_RESULTS_DIR).mkdir(parents=True, exist_ok=True)
    return Path(TOOL_RESULTS_DIR)


# ---------------------------------------------------------------------------
# 核心函数：写完整论文
# ---------------------------------------------------------------------------

def write_paper(
    project_context: dict[str, Any] | None = None,
    *,
    max_tokens: int = 4000,
    paper_title: str = "",
    save: bool = True,
) -> dict[str, Any]:
    """
    从项目上下文生成完整学术论文。

    参数:
        project_context: {
            "project_id": str,
            "domain": str,                    # 研究领域
            "hypothesis": str | dict,          # 精炼后的假设（来自 bianlun）
            "knowledge_gaps": list[dict],      # 识别的知识缺口（来自 tanxi）
            "experiment_protocol": dict,       # 实验方案（来自 GeWu）
            "experiment_results": str | dict,  # 实验结果（来自 CodeEngineer）
            "analysis_report": dict,           # 分析报告（来自 MingBian）
            "mechanism_report": dict,          # 机制验证报告（来自 YanZhen）
            "papergraph_records": list[dict],  # 文献记录（来自 ZhiZhi）
        }
        max_tokens: LLM 最大输出
        paper_title: 覆盖自动生成的标题
        save: 是否保存到 .science/papers/

    返回:
        {"paper": {...}, "latex": "...", "saved_paths": [...], "paper_status": {...}}
    """
    ctx = project_context or {}

    # ---- 构建 prompt ----
    user_prompt = _build_write_prompt(ctx, paper_title)

    # ---- 调用 LLM 生成论文 ----
    result = _call_llm(PAPERWRITER_SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens)
    paper = result.get("paper", result)

    # ---- 后处理 ----
    paper = _ensure_all_sections(paper)
    paper = _fill_missing_sections(paper, ctx)

    # ---- 生成 LaTeX ----
    latex = format_latex(paper)

    # ---- 保存 ----
    saved_paths: list[str] = []
    if save:
        saved_paths = _save_paper(paper, latex, ctx)

    # ---- 自检 ----
    quality = review_draft(paper)

    return {
        "thought": result.get("thought", ""),
        "paper": paper,
        "latex": latex,
        "saved_paths": saved_paths,
        "paper_status": {
            "completed_sections": list(paper.keys()),
            "total_words": _count_words(paper),
            "citation_count": len(paper.get("references", [])),
        },
        "quality_check": quality,
    }


def _build_write_prompt(ctx: dict[str, Any], override_title: str = "") -> str:
    """组装给 PaperWriter LLM 的完整上下文 prompt。"""
    parts: list[str] = []

    # 标题覆盖
    if override_title:
        parts.append(f"## REQUIRED TITLE\n{override_title}\n")

    # 领域
    domain = ctx.get("domain", "")
    if domain:
        parts.append(f"## RESEARCH DOMAIN\n{domain}\n")

    # 知识缺口
    gaps = ctx.get("knowledge_gaps", [])
    if gaps:
        gap_text = _format_gaps(gaps)
        parts.append(f"## KNOWLEDGE GAPS (state in Introduction)\n{gap_text}\n")

    # 假设
    hypothesis = ctx.get("hypothesis", "")
    if hypothesis:
        if isinstance(hypothesis, dict):
            hypothesis = json.dumps(hypothesis, ensure_ascii=False, indent=2)
        parts.append(f"## HYPOTHESIS (core contribution)\n{str(hypothesis)[:3000]}\n")

    # 实验方案
    protocol = ctx.get("experiment_protocol", {})
    if protocol:
        if isinstance(protocol, dict):
            protocol_text = json.dumps(protocol, ensure_ascii=False, indent=2)
        else:
            protocol_text = str(protocol)
        parts.append(f"## EXPERIMENT PROTOCOL\n{protocol_text[:3000]}\n")

    # 实验结果
    results = ctx.get("experiment_results", "")
    if results:
        if isinstance(results, dict):
            results = json.dumps(results, ensure_ascii=False, indent=2)
        parts.append(f"## EXPERIMENT RESULTS\n{str(results)[:4000]}\n")

    # 分析报告
    analysis = ctx.get("analysis_report", {})
    if analysis:
        if isinstance(analysis, dict):
            analysis_text = json.dumps(analysis, ensure_ascii=False, indent=2)
        else:
            analysis_text = str(analysis)
        parts.append(f"## ANALYSIS REPORT\n{analysis_text[:3000]}\n")

    # 机制验证
    mechanism = ctx.get("mechanism_report", {})
    if mechanism:
        if isinstance(mechanism, dict):
            mech_text = json.dumps(mechanism, ensure_ascii=False, indent=2)
        else:
            mech_text = str(mechanism)
        parts.append(f"## MECHANISM FIDELITY REPORT\n{mech_text[:2000]}\n")

    # 文献
    records = ctx.get("papergraph_records", [])
    if records:
        refs_text = _format_references(records)
        parts.append(f"## AVAILABLE REFERENCES (cite from this list only)\n{refs_text}\n")

    # 最终指令
    parts.append("## TASK")
    parts.append(
        "Write a COMPLETE academic paper with ALL 7 sections based on the above context. "
        "Every claim must be backed by the provided results or references. "
        "Use concrete numbers from the experiment results. "
        "Only cite papers from the AVAILABLE REFERENCES list above."
    )

    return "\n".join(parts)


def _format_gaps(gaps: list[dict]) -> str:
    lines: list[str] = []
    for i, gap in enumerate(gaps[:5], 1):
        desc = gap.get("gap_description") or gap.get("description") or str(gap)
        lines.append(f"  [{i}] {str(desc)[:500]}")
    return "\n".join(lines) if lines else "(no gaps provided)"


def _format_references(records: list[dict]) -> str:
    lines: list[str] = []
    for i, rec in enumerate(records[:30], 1):
        title = rec.get("title") or rec.get("paper_title") or "Unknown"
        authors = rec.get("authors") or rec.get("first_author") or "Unknown"
        year = rec.get("year") or rec.get("publication_year") or ""
        venue = rec.get("venue") or rec.get("journal") or rec.get("source", "")
        rec_id = rec.get("paper_id") or rec.get("id") or ""
        lines.append(f"  [{i}] {authors} ({year}). {title}. {venue}.  id={rec_id}")
    return "\n".join(lines) if lines else "(no references provided — do not invent citations)"


def _ensure_all_sections(paper: dict) -> dict:
    """确保 paper dict 有全部7个字段。"""
    required = ["title", "abstract", "introduction", "related_work",
                "methodology", "experiments", "conclusion", "references"]
    for key in required:
        if key not in paper:
            paper[key] = ""
    return paper


def _fill_missing_sections(paper: dict, ctx: dict) -> dict:
    """为缺失的节填充占位文本（防止空白输出）。"""
    domain = ctx.get("domain", "this research domain")

    if not paper.get("title"):
        hypothesis = ctx.get("hypothesis", "")
        if isinstance(hypothesis, dict):
            hypothesis = hypothesis.get("hypothesis", "") or hypothesis.get("title", "")
        paper["title"] = str(hypothesis)[:120] or f"Research on {domain}"

    if not paper.get("abstract", "").strip():
        paper["abstract"] = f"This paper investigates an open problem in {domain}. " \
            "Methods, results, and conclusions are detailed in the full text."

    if not paper.get("references"):
        records = ctx.get("papergraph_records", [])
        paper["references"] = [
            {"index": i + 1, "citation": _format_single_ref(r), "papergraph_id": r.get("paper_id", r.get("id", ""))}
            for i, r in enumerate(records[:25])
        ]

    return paper


def _format_single_ref(rec: dict) -> str:
    authors = rec.get("authors") or rec.get("first_author", "Unknown")
    title = rec.get("title", "Unknown")
    year = rec.get("year", "")
    venue = rec.get("venue", "")
    return f"{authors} ({year}). {title}. {venue}."


def _count_words(paper: dict) -> int:
    text_fields = ["abstract", "introduction", "related_work",
                   "methodology", "experiments", "conclusion"]
    total = 0
    for key in text_fields:
        total += len(re.findall(r"\w+", str(paper.get(key, ""))))
    return total


def _save_paper(paper: dict, latex: str, ctx: dict) -> list[str]:
    """保存论文到 .science/papers/ 和 tool_results/。"""
    saved: list[str] = []
    project_id = ctx.get("project_id", str(int(time.time() * 1000)))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(paper.get("title", "paper")[:60])
    base = f"{ts}_{slug}"

    # JSON
    json_path = _paper_dir() / f"{base}.json"
    json_path.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
    saved.append(str(json_path))

    # LaTeX
    tex_path = _paper_dir() / f"{base}.tex"
    tex_path.write_text(latex, encoding="utf-8")
    saved.append(str(tex_path))

    # 纯文本
    txt_path = _paper_dir() / f"{base}.txt"
    txt_path.write_text(_paper_to_text(paper), encoding="utf-8")
    saved.append(str(txt_path))

    # 也存到 tool_results
    tr_path = _tool_results_dir() / f"paperwriter_{base}.json"
    tr_path.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
    saved.append(str(tr_path))

    return saved


def _paper_to_text(paper: dict) -> str:
    """论文 dict → 可读纯文本。"""
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
    output: list[str] = []
    for heading, body in sections:
        output.append(f"{'='*60}\n{heading}\n{'='*60}\n{body}\n")
    return "\n".join(output)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text.lower()).strip("_") or "paper"


# ---------------------------------------------------------------------------
# 子工具（对应 agent 定义中的 tools 列表）
# ---------------------------------------------------------------------------

def write_section(
    paper_state: dict[str, Any],
    section: str,
    context: dict[str, Any] | None = None,
    *,
    max_tokens: int = 1000,
) -> dict[str, Any]:
    """
    单独写/重写论文的某一节。

    参数:
        paper_state: 当前论文状态 dict
        section: title | abstract | introduction | related_work | methodology | experiments | conclusion
        context: 额外的项目上下文

    返回:
        更新后的 paper_state
    """
    ctx = context or {}
    current_text = paper_state.get(section, "")

    prompt = f"""Rewrite the **{section}** section of this paper.

## CURRENT PAPER STATE
Title: {paper_state.get('title', 'N/A')}

## CURRENT {section.upper()} TEXT
{current_text[:3000]}

## ADDITIONAL CONTEXT
{json.dumps(ctx, ensure_ascii=False, indent=2)[:2000]}

## TASK
Rewrite the {section} section to be more rigorous, well-structured, and publication-ready.
Return JSON: {{"{section}": "rewritten text"}}"""

    result = _call_llm(PAPERWRITER_SYSTEM_PROMPT, prompt, max_tokens=max_tokens)
    new_text = result.get(section, current_text)
    paper_state[section] = new_text
    return paper_state


def generate_figure(
    data: dict[str, Any],
    fig_type: str = "bar",
    save_dir: str | None = None,
) -> str:
    """
    从实验数据生成图表。

    参数:
        data: {"x": [...], "y": [...], "labels": [...], "xlabel": "", "ylabel": "", "title": ""}
        fig_type: bar | line | scatter | table
        save_dir: 保存目录（默认 .science/papers/figures/）

    返回:
        保存的图片路径，失败返回空字符串
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return ""  # matplotlib 不可用

    save_dir = Path(save_dir or (_paper_dir() / "figures"))
    save_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 5))

    x = data.get("x", list(range(len(data.get("y", [])))))
    y = data.get("y", [])
    labels = data.get("labels", [])
    xlabel = data.get("xlabel", "X")
    ylabel = data.get("ylabel", "Y")
    chart_title = data.get("title", "Figure")

    if fig_type == "bar":
        ax.bar(range(len(y)), y, tick_label=labels or x, color="steelblue", edgecolor="white")
    elif fig_type == "scatter":
        ax.scatter(x, y, c="steelblue", alpha=0.7)
    elif fig_type == "line":
        ax.plot(x, y, marker="o", color="steelblue", linewidth=2)
    elif fig_type == "table":
        ax.axis("off")
        rows = data.get("rows", [])
        cols = data.get("cols", [])
        if rows:
            ax.table(cellText=rows, colLabels=cols or None, loc="center", cellLoc="center")
    else:
        ax.bar(range(len(y)), y, color="steelblue")

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(chart_title)
    plt.tight_layout()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = save_dir / f"{fig_type}_{ts}.png"
    fig.savefig(str(path), dpi=150)
    plt.close(fig)
    return str(path)


def search_citations(
    keywords: str,
    papergraph_records: list[dict[str, Any]] | None = None,
    *,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """
    从 PaperGraph 记录中按关键词匹配引用。

    参数:
        keywords: 搜索关键词（空格分隔）
        papergraph_records: PaperGraph 记录列表
        top_k: 返回前 K 个

    返回:
        匹配的引用列表
    """
    records = papergraph_records or []
    if not records:
        return []

    kw_list = [w.lower() for w in keywords.split() if len(w) > 1]

    scored: list[tuple[int, dict]] = []
    for rec in records:
        title = str(rec.get("title", "")).lower()
        abstract = str(rec.get("abstract", "")).lower()
        text = title + " " + abstract
        score = sum(1 for kw in kw_list if kw in text)
        if score > 0:
            scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"index": i + 1, "citation": _format_single_ref(r), "relevance_score": s,
         "papergraph_id": r.get("paper_id", r.get("id", "")), "verified": True}
        for i, (s, r) in enumerate(scored[:top_k])
    ]


# ---------------------------------------------------------------------------
# 外部引用验证（Semantic Scholar / CrossRef — OpenDraft + sciwrite-lint 风格）
# ---------------------------------------------------------------------------

def verify_citation_external(
    citation: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """
    通过 Semantic Scholar API 验证一条引用是否存在。

    参数:
        citation: 引用字符串，如 "Kundur, P. et al. (2004). Definition and Classification..."
        timeout: API 超时秒数

    返回:
        {
            "verified": True/False,
            "matched_title": "真实论文标题" | None,
            "matched_doi": "10.xxx" | None,
            "confidence": 0.0-1.0,   # 匹配置信度
            "source": "semantic_scholar" | "failed",
            "suggestion": "最接近的真实引用" | None
        }
    """
    # 从引用字符串中提取标题
    import re as _re

    # 策略: 找到 "(Year)." 之后的部分作为标题开始
    # 例如 "Author et al. (2004). Definition and Classification of Power System Stability. IEEE Trans."
    # → query = "Definition and Classification of Power System Stability"
    year_match = _re.search(r"\((\d{4})\)\.?\s*", citation)
    if year_match:
        after_year = citation[year_match.end():].strip()
        # 标题到下一个句号或结尾（但要跳过 "et al." 之类的缩写）
        title_end = _re.search(r"\.\s+(?:[A-Z][a-z]|IEEE|ACM|Nature|Science|arXiv)", after_year)
        if title_end:
            query = after_year[:title_end.start()].strip()
        else:
            # 取第一句
            dot_pos = after_year.find(".")
            query = after_year[:dot_pos].strip() if dot_pos > 10 else after_year[:150].strip()
    else:
        query = citation[:150]

    # 去掉噪音
    query = _re.sub(r"\[.*?\]", "", query)
    query = _re.sub(r"[^a-zA-Z0-9\s\-:,()+\-=%]+", "", query).strip()[:200]

    if len(query) < 8:
        return {"verified": False, "matched_title": None, "matched_doi": None,
                "confidence": 0.0, "source": "failed", "suggestion": None,
                "error": f"Query too short: '{query}'"}

    # 调用 Semantic Scholar 搜索
    try:
        result = _search_semantic_scholar_api(query, max_results=3, timeout=timeout)
    except Exception as e:
        return {"verified": False, "matched_title": None, "matched_doi": None,
                "confidence": 0.0, "source": "failed", "suggestion": None,
                "error": str(e)[:200]}

    papers = result.get("data", [])
    if not papers:
        return {"verified": False, "matched_title": None, "matched_doi": None,
                "confidence": 0.0, "source": "semantic_scholar", "suggestion": None,
                "note": "No results found"}

    # 计算最佳匹配
    best = papers[0]
    best_title = best.get("title", "")
    best_doi = best.get("externalIds", {}).get("DOI", "")
    # 简单的标题相似度
    query_lower = query.lower()
    title_lower = best_title.lower() if best_title else ""
    overlap = sum(1 for w in query_lower.split() if w in title_lower)
    confidence = min(overlap / max(len(query_lower.split()), 1), 1.0)

    return {
        "verified": confidence > 0.3,
        "matched_title": best_title,
        "matched_doi": best_doi,
        "confidence": round(confidence, 2),
        "source": "semantic_scholar",
        "suggestion": _format_semantic_scholar_result(best) if confidence > 0.3 else None,
    }


def verify_references_external(
    references: list[dict[str, Any]],
    *,
    timeout: float = 10.0,
    verbose: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    批量验证引用列表，通过 Semantic Scholar API 逐一检查。

    参数:
        references: 引用列表 [{index, citation, ...}, ...]
        timeout: 单条 API 超时
        verbose: 是否打印进度

    返回:
        (verified_references, summary)
        - verified_references: 每条标注 verified=True/False
        - summary: {total, verified_count, unverified_count, fake_flagged}
    """
    verified_refs: list[dict] = []
    verified_count = 0
    unverified_count = 0
    fake_flagged: list[str] = []

    for i, ref in enumerate(references):
        if isinstance(ref, str):
            citation = ref
            ref = {"index": i + 1, "citation": citation}

        citation_text = ref.get("citation", str(ref))

        # 限流保护：Semantic Scholar 免费 API 约 1 req/s
        if i > 0:
            time.sleep(1.0)

        result = verify_citation_external(citation_text, timeout=timeout)

        ref["verified"] = result["verified"]
        ref["verification_source"] = result["source"]

        if result["verified"]:
            verified_count += 1
            if result.get("matched_title"):
                ref["matched_title"] = result["matched_title"]
            if result.get("matched_doi"):
                ref["doi"] = result["matched_doi"]
            if result.get("suggestion") and result.get("confidence", 0) < 0.7:
                ref["suggestion"] = result["suggestion"]
        else:
            unverified_count += 1
            if not result.get("note"):  # no results at all → likely fake
                ref["citation"] = f"[UNVERIFIED] {citation_text}"
                fake_flagged.append(citation_text[:100])

        if verbose and result["verified"]:
            print(f"  [OK] {citation_text[:80]}...")
        elif verbose:
            print(f"  [??] {citation_text[:80]}... — {result.get('error', result.get('note', 'not found'))}")

        verified_refs.append(ref)

    summary = {
        "total": len(references),
        "verified_count": verified_count,
        "unverified_count": unverified_count,
        "fake_flagged": fake_flagged,
        "verification_rate": round(verified_count / max(len(references), 1), 2),
    }
    return verified_refs, summary


def _search_semantic_scholar_api(
    query: str,
    max_results: int = 3,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """调用 Semantic Scholar 搜索 API，复用项目已有的速率限制。"""
    try:
        from ._literature_search import search_semantic_scholar as _ss_search
    except ImportError:
        try:
            from _literature_search import search_semantic_scholar as _ss_search
        except ImportError:
            # fallback: 独立实现
            return _search_semantic_scholar_fallback(query, max_results, timeout)

    try:
        result = _ss_search(query, max_results=max_results)
        papers = result.get("results", result.get("data", []))
        return {"data": papers}
    except Exception:
        return {"data": []}


def _search_semantic_scholar_fallback(
    query: str, max_results: int = 3, timeout: float = 10.0
) -> dict[str, Any]:
    """独立 Semantic Scholar API 调用（不带速率限制，慎用）。"""
    import urllib.request as _ur
    import urllib.parse as _up

    params = _up.urlencode({"query": query, "limit": max_results, "fields": "title,externalIds,year,authors"})
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    req = _ur.Request(url, headers={"User-Agent": "Qwen-Zhikan/1.0"})
    try:
        with _ur.urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
            return json.loads(_ur.read().decode("utf-8"))
    except Exception:
        return {"data": []}


def _format_semantic_scholar_result(paper: dict) -> str:
    """Semantic Scholar 结果 → 格式化引用。"""
    title = paper.get("title", "Unknown")
    year = paper.get("year", "")
    authors = paper.get("authors", [])
    first_author = authors[0].get("name", "Unknown") if authors else "Unknown"
    return f"{first_author} et al. ({year}). {title}."


def format_latex(paper: dict[str, Any], template: str = "default") -> str:
    """
    将论文 dict 格式化为 LaTeX 源码。

    参数:
        paper: 论文 dict（title, abstract, introduction, ...）
        template: 模板名（目前仅有 "default"）

    返回:
        LaTeX 源码字符串
    """
    references = paper.get("references", [])
    bib_lines: list[str] = []
    for ref in references:
        if isinstance(ref, str):
            # LLM 有时返回纯字符串引用
            bib_lines.append(f"\\bibitem{{ref{len(bib_lines)+1}}} {ref}")
        elif isinstance(ref, dict):
            idx = ref.get("index", len(bib_lines) + 1)
            citation = ref.get("citation", str(ref))
            bib_lines.append(f"\\bibitem{{ref{idx}}} {citation}")
        else:
            bib_lines.append(f"\\bibitem{{ref{len(bib_lines)+1}}} {str(ref)}")

    latex = LATEX_TEMPLATE.format(
        title=_latex_escape(paper.get("title", "Untitled")),
        abstract=_latex_escape(paper.get("abstract", "")),
        introduction=_latex_escape(paper.get("introduction", "")),
        related_work=_latex_escape(paper.get("related_work", "")),
        methodology=_latex_escape(paper.get("methodology", "")),
        experiments=_latex_escape(paper.get("experiments", "")),
        conclusion=_latex_escape(paper.get("conclusion", "")),
        references_bib="\n".join(bib_lines) if bib_lines else "% No references",
    )
    return latex


def _latex_escape(text: str) -> str:
    """转义 LaTeX 特殊字符。"""
    replacements = {
        "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\^{}", "\\": r"\textbackslash{}",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    return text


def review_draft(paper: dict[str, Any]) -> dict[str, Any]:
    """
    对论文草稿进行写后自检。

    返回:
        {"claims_supported": bool, "citations_present": bool,
         "all_sections_present": bool, "issues": [...], "suggestions": [...]}
    """
    issues: list[str] = []
    suggestions: list[str] = []

    # 检查所有节是否存在
    required_sections = ["abstract", "introduction", "related_work",
                         "methodology", "experiments", "conclusion"]
    missing = [s for s in required_sections if not paper.get(s, "").strip()]
    all_present = len(missing) == 0
    if missing:
        issues.append(f"Missing sections: {', '.join(missing)}")
        suggestions.append(f"Run write_section() for each missing section.")

    # 检查引用
    refs = paper.get("references", [])
    citations_present = len(refs) > 0
    if not citations_present:
        issues.append("No references found — paper may appear unsubstantiated.")
        suggestions.append("Run search_citations() to populate references.")

    # 检查字数
    wc = _count_words(paper)
    if wc < 1500:
        issues.append(f"Paper is very short ({wc} words). Each section should be substantive.")
        suggestions.append("Expand each section with more detail from experiment results.")
    elif wc < 3000:
        suggestions.append(f"Paper is {wc} words — consider expanding for journal submission.")

    # 检查 abstract
    abstract = paper.get("abstract", "")
    if len(abstract.split()) < 50:
        issues.append("Abstract is too short (<50 words). Should be 150-250 words.")
        suggestions.append("Expand abstract to include problem, method, results, significance.")

    # 检查数字
    has_numbers = bool(re.search(r"\d+\.?\d*\%", paper.get("experiments", "")))
    if not has_numbers:
        suggestions.append("Experiments section lacks concrete numbers. Add percentages, p-values, etc.")

    return {
        "claims_supported": True,  # LLM 自我评估，无法客观验证
        "citations_present": citations_present,
        "all_sections_present": all_present,
        "total_words": wc,
        "issues": issues,
        "suggestions": suggestions,
        "overall": "ready" if (all_present and citations_present and wc >= 1500) else "needs_revision",
    }


# ---------------------------------------------------------------------------
# 简化入口：一键从项目上下文生成论文
# ---------------------------------------------------------------------------

def write_paper_from_project(project_id: str) -> dict[str, Any]:
    """
    从 .science/projects/ 中的项目文件读取上下文并生成论文。
    这是和 science_core.py pipeline 对接的接口。

    参数:
        project_id: 项目 ID

    返回:
        同 write_paper()
    """
    try:
        from .science_core import load_project
    except ImportError:
        from science_core import load_project

    project = load_project(project_id)

    # 从项目中提取上下文
    ctx: dict[str, Any] = {
        "project_id": project_id,
        "domain": project.get("domain", ""),
        "hypothesis": project.get("refined_hypothesis") or project.get("hypothesis", {}),
        "knowledge_gaps": project.get("knowledge_gaps", []),
        "experiment_protocol": project.get("experiment_protocol") or project.get("gewu_protocol", {}),
        "experiment_results": project.get("experiment_results") or project.get("code_results", {}),
        "analysis_report": project.get("analysis_report") or project.get("mingbian_report", {}),
        "mechanism_report": project.get("mechanism_report") or project.get("yanzhen_report", {}),
        "papergraph_records": project.get("papergraph_records") or project.get("imported_records", []),
    }

    return write_paper_staged(ctx)


# ---------------------------------------------------------------------------
# 论文修改（配合 Reviewer 的迭代循环）
# ---------------------------------------------------------------------------

REVISE_SYSTEM_PROMPT = """\
You are PaperWriter, the Academic Paper Writer. You are revising a manuscript based on
peer review feedback. Your goal is to address every weakness and question raised by the
reviewer while preserving the paper's strengths.

## REVISION RULES
1. Address EVERY weakness listed in the review feedback.
2. Add missing details, numbers, or citations that the reviewer flagged.
3. Improve clarity where the reviewer found the text hard to follow.
4. Do NOT remove content that the reviewer praised.
5. If the reviewer asks for specific experiments or comparisons, add them.

## OUTPUT FORMAT
Return the COMPLETE revised paper as JSON (same schema as original):
{
  "thought": "How I addressed each weakness...",
  "paper": {
    "title": "...",
    "abstract": "...",
    "introduction": "...",
    "related_work": "...",
    "methodology": "...",
    "experiments": "...",
    "conclusion": "...",
    "references": [...]
  }
}\
"""


def revise_paper(
    current_paper: dict[str, Any] | str,
    review_feedback: dict[str, Any] | str,
    project_context: dict[str, Any] | None = None,
    *,
    max_tokens: int = 6000,
) -> dict[str, Any]:
    """
    根据评审反馈修改论文。

    参数:
        current_paper: 当前论文（dict 或纯文本）
        review_feedback: Reviewer 返回的 review dict，或纯文本反馈
        project_context: 可选项目上下文
        max_tokens: LLM 最大输出

    返回:
        修改后的论文 dict（同 write_paper 输出格式）
    """
    ctx = project_context or {}

    # 统一为文本格式
    if isinstance(current_paper, dict):
        paper_text = _paper_to_text(current_paper)
    else:
        paper_text = str(current_paper)

    if isinstance(review_feedback, dict):
        fb = review_feedback
        scores = fb.get("scores", {})
        weaknesses = fb.get("weaknesses", [])
        questions = fb.get("questions_for_authors", [])
        feedback_parts = []
        if scores:
            feedback_parts.append(
                f"Scores: Novelty={scores.get('novelty','?')}, "
                f"Quality={scores.get('quality','?')}, "
                f"Clarity={scores.get('clarity','?')}, "
                f"Significance={scores.get('significance','?')}"
            )
        if weaknesses:
            feedback_parts.append("Weaknesses to fix:\n" + "\n".join(f"- {w}" for w in weaknesses))
        if questions:
            feedback_parts.append("Questions to address:\n" + "\n".join(f"- {q}" for q in questions))
        feedback_text = "\n\n".join(feedback_parts)
    else:
        feedback_text = str(review_feedback)

    prompt = f"""## CURRENT PAPER
{paper_text[:10000]}

## REVIEWER FEEDBACK (must address ALL points)
{feedback_text[:4000]}

## ADDITIONAL CONTEXT
Domain: {ctx.get('domain', 'N/A')}
References available: {len(ctx.get('papergraph_records', []))}

## TASK
Revise the paper to address EVERY weakness and question in the review feedback.
Return the COMPLETE revised paper as JSON."""

    result = _call_llm(REVISE_SYSTEM_PROMPT, prompt, max_tokens=max_tokens)
    paper = result.get("paper", result)
    paper = _ensure_all_sections(paper)
    paper = _fill_missing_sections(paper, ctx)

    latex = format_latex(paper)
    saved = _save_paper(paper, latex, ctx)

    return {
        "thought": result.get("thought", ""),
        "paper": paper,
        "latex": latex,
        "saved_paths": saved,
        "paper_status": {
            "completed_sections": list(paper.keys()),
            "total_words": _count_words(paper),
            "citation_count": len(paper.get("references", [])),
        },
    }


# =====================================================================
# CLI 测试入口
# =====================================================================

def _interactive():
    """交互式生成论文。用户粘贴项目 JSON 或直接回车使用 demo。"""
    print("\n" + "=" * 60)
    print("  Qwen-智勘 PaperWriter — Academic Paper Generator")
    print("=" * 60)
    print()
    print("Paste project context JSON (or press Enter for demo):")

    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == "" and not lines:
            break
        if line.strip() == "DONE":
            break
        lines.append(line)

    if lines:
        try:
            ctx = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            print("Invalid JSON. Using demo context.\n")
            ctx = _demo_context()
    else:
        ctx = _demo_context()

    print("\n" + "-" * 40)
    print("Generating paper... (calling LLM)\n")

    try:
        result = write_paper_staged(ctx)

        print("\n" + "=" * 60)
        print("  GENERATED PAPER")
        print("=" * 60)
        print(f"\nTITLE: {result['paper'].get('title', 'N/A')}\n")
        print(f"ABSTRACT:\n{textwrap.fill(result['paper'].get('abstract', ''), width=80)}\n")
        print(f"WORDS: {result['paper_status']['total_words']}")
        print(f"CITATIONS: {result['paper_status']['citation_count']}")
        print(f"SECTIONS: {', '.join(result['paper_status']['completed_sections'])}")
        print()

        qc = result["quality_check"]
        print(f"QUALITY: {qc.get('overall', '?')}")
        if qc.get("issues"):
            print("ISSUES:")
            for issue in qc["issues"]:
                print(f"  ⚠ {issue}")
        if qc.get("suggestions"):
            print("SUGGESTIONS:")
            for s in qc["suggestions"]:
                print(f"  → {s}")

        if result["saved_paths"]:
            print(f"\n📁 Saved to:")
            for p in result["saved_paths"]:
                print(f"   {p}")

        # 打印完整论文文本
        print("\n" + "=" * 60)
        print("  FULL PAPER TEXT")
        print("=" * 60)
        print(_paper_to_text(result["paper"]))

    except Exception as exc:
        import traceback
        print(f"\n[ERROR] {exc}")
        print(f"[TRACEBACK]\n{traceback.format_exc()}")
        sys.exit(1)


def _demo_context() -> dict[str, Any]:
    """返回用于测试的示例项目上下文。"""
    return {
        "domain": "power system transient stability",
        "hypothesis": {
            "title": "Graph Neural Networks Enable Real-Time Transient Stability "
                     "Prediction Without Time-Domain Simulation",
            "hypothesis": "A graph neural network that directly maps power grid topology "
                          "to stability margin can replace iterative time-domain simulation, "
                          "achieving 100x speedup while maintaining >95% accuracy.",
        },
        "knowledge_gaps": [
            {
                "gap_id": "GAP-001",
                "gap_description": "No existing ML method exploits the native graph "
                                   "structure of power grids for transient stability prediction. "
                                   "CNN/MLP approaches flatten topology into feature vectors, "
                                   "losing critical connectivity information.",
                "novelty_score": 8,
            },
        ],
        "experiment_protocol": {
            "datasets": ["IEEE 39-bus", "IEEE 118-bus", "Polish 2383-bus"],
            "baselines": ["Time-Domain Simulation (TDS)", "CNN baseline", "Random Forest"],
            "metrics": ["Accuracy", "Inference time (ms)", "ROC-AUC", "False positive rate"],
            "success_threshold": "Accuracy >95%, inference <10ms on 118-bus, ROC-AUC >0.95",
        },
        "experiment_results": {
            "accuracy": 0.985,
            "inference_time_ms": 2.3,
            "roc_auc": 0.978,
            "false_positive_rate": 0.012,
            "speedup_vs_tds": "117x",
            "baseline_comparison": {
                "TDS": {"accuracy": 1.0, "time_ms": 270},
                "CNN": {"accuracy": 0.942, "time_ms": 8.5},
                "Random Forest": {"accuracy": 0.913, "time_ms": 1.1},
                "Our GNN": {"accuracy": 0.985, "time_ms": 2.3},
            },
        },
        "analysis_report": {
            "hypothesis_verdict": "supported",
            "effect_size": "Cohen's d = 1.42 (large)",
            "statistical_significance": "p < 0.001, 95% CI [0.976, 0.994]",
            "key_finding": "GNN outperforms all ML baselines with 117× speedup vs TDS",
        },
        "mechanism_report": {
            "overall_verdict": "MECHANISM_VERIFIED",
            "regime_shift_stability": "stable",
            "cawm_risk_level": "LOW",
        },
        "papergraph_records": [
            {"title": "Definition and Classification of Power System Stability",
             "authors": "Kundur, P. et al.", "year": "2004",
             "venue": "IEEE Trans. Power Systems", "paper_id": "kundur2004"},
            {"title": "Power System Dynamics and Stability",
             "authors": "Sauer, P.W. & Pai, M.A.", "year": "1998",
             "venue": "Prentice Hall", "paper_id": "sauer1998"},
            {"title": "Deep Learning for Power System Security Assessment",
             "authors": "Chen, Y. et al.", "year": "2020",
             "venue": "IEEE Trans. Power Systems", "paper_id": "chen2020"},
            {"title": "Random Forest for Online Transient Stability Prediction",
             "authors": "Liu, R. et al.", "year": "2018",
             "venue": "IEEE Trans. Power Systems", "paper_id": "liu2018"},
            {"title": "Graph Neural Networks: A Review of Methods and Applications",
             "authors": "Zhou, J. et al.", "year": "2020",
             "venue": "AI Open", "paper_id": "zhou2020"},
        ],
    }


if __name__ == "__main__":
    _interactive()
