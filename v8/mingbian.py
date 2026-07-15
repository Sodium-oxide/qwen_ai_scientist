"""
MingBian — 数据分析师（模块8）
==============================
在角色三未交付前，提供最小可用实现以闭合管道。

对 CodeEngineer 产出的实验结果进行统计分析和假设验证判定。
"""

from __future__ import annotations

import json
from typing import Any


MINGBIAN_SYSTEM_PROMPT = """\
You are MingBian, the Data Analyst of the Qwen-Zhikan AI Scientist system.
You analyze experimental results, assess whether the hypothesis is supported,
and provide statistical rigor.

## TASK
Given experimental results, success criteria, baselines, and the original hypothesis,
produce a structured analysis report.

## RULES
1. Compare results against each success criterion.
2. Report effect sizes and confidence intervals, not just p-values.
3. If results are simulated (marked [SIMULATED]), note this as a limitation.
4. Distinguish: supported / refuted / inconclusive.
5. Document failed experiments as negative knowledge.

## OUTPUT FORMAT
Return JSON:
{
  "hypothesis_verdict": "supported",
  "key_findings": ["finding 1", "finding 2"],
  "statistical_analysis": {
    "significance_level": "p < 0.01",
    "effect_size": "Cohen's d = 0.8",
    "confidence_interval": "95% CI [...]"
  },
  "baseline_ranking": [{"name": "...", "accuracy": 0.95, "rank": 1}],
  "limitations": ["..."],
  "iteration_recommendations": ["..."],
  "failed_experiments_documented": []
}\
"""


def _call_llm(system: str, prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
    try:
        from .llm import get_client
    except ImportError:
        from llm import get_client
    client = get_client()

    response = client.messages.create(
        model=None, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}], tools=[],
    )
    content = getattr(response, "content", response)
    text = _render_text(content)
    parsed = _parse_json(text)
    if not parsed:
        raise ValueError(f"MingBian LLM returned invalid JSON: {text[:500]}")
    return parsed


def _render_text(content: Any) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", b.get("content", ""))) for b in content if isinstance(b, dict))
    return str(content)


def _parse_json(text: str) -> dict[str, Any]:
    import json as _json, re as _re
    s = text.strip()
    if s.startswith("```"): s = "\n".join(s.splitlines()[1:-1]).strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try: return _json.loads(s[start:end+1])
        except _json.JSONDecodeError: pass
    return {}


def _heuristic_analysis(results: dict, criteria: list, baselines: list,
                        hypothesis: dict) -> dict:
    """不依赖 LLM 的规则分析 fallback。"""
    verdict = "supported"
    findings = []
    limitations = ["[SIMULATED] Results are LLM-generated, not from real experiments"]

    primary = results.get("primary_results", {})
    baseline_comp = results.get("baseline_comparison", {})
    stats = results.get("statistical_tests", {})

    # 检查基线对比
    our_method = None
    best_baseline = None
    for name, vals in baseline_comp.items():
        if "our" in name.lower() or "gnn" in name.lower() or "proposed" in name.lower():
            our_method = (name, vals)
        elif best_baseline is None or vals.get("accuracy", 0) > best_baseline[1].get("accuracy", 0):
            best_baseline = (name, vals)

    if our_method and best_baseline:
        our_acc = our_method[1].get("accuracy", 0)
        best_acc = best_baseline[1].get("accuracy", 0)
        if our_acc > best_acc:
            findings.append(
                f"Our method ({our_acc:.1%}) outperforms best baseline "
                f"{best_baseline[0]} ({best_acc:.1%})"
            )
        else:
            findings.append(f"Our method does not outperform {best_baseline[0]}")
            verdict = "refuted"

    if stats.get("p_value", 0) < 0.05:
        findings.append(f"Results are statistically significant (p={stats['p_value']})")
    else:
        limitations.append(f"Results not statistically significant (p={stats.get('p_value', 'N/A')})")

    if results.get("simulated"):
        limitations.append("Results are simulated — real experimental validation required")

    # 排序
    ranking = [
        {"name": name, "accuracy": vals.get("accuracy", 0),
         "time_ms": vals.get("time_ms", float("inf")), "rank": i+1}
        for i, (name, vals) in enumerate(
            sorted(baseline_comp.items(), key=lambda x: x[1].get("accuracy", 0), reverse=True)
        )
    ]

    return {
        "hypothesis_verdict": verdict,
        "key_findings": findings,
        "statistical_analysis": {
            "significance_level": f"p = {stats.get('p_value', 'N/A')}",
            "effect_size": stats.get("effect_size", "N/A"),
            "confidence_interval": stats.get("confidence_interval_95", "N/A"),
        },
        "baseline_ranking": ranking[:5],
        "limitations": limitations,
        "iteration_recommendations": [
            "Run real experiments to validate simulated results",
            "Add error bars and standard deviations across multiple seeds",
        ],
        "failed_experiments_documented": [],
        "note": "[SIMULATED] — analyzed by heuristic rules, pending real data",
    }


# ── 公开 API ──

def analyze_results(
    project_id: str,
    experiment_results: dict | None = None,
    success_criteria: list | None = None,
    baselines: list | None = None,
    hypothesis: dict | None = None,
) -> dict:
    """分析实验结果，输出结构化报告。"""
    results = experiment_results or {}
    criteria = success_criteria or []
    bls = baselines or []
    hyp = hypothesis or {}

    # 尝试从项目加载
    try:
        from ._project import load_project
        proj = load_project(project_id)
        results = results or proj.get("experiment_results", {})
        hyp = hyp or proj.get("hypothesis") or proj.get("refined_hypothesis", {})
        bls = bls or (proj.get("experiment_protocol") or {}).get("baselines", [])
    except Exception:
        pass

    # 尝试 LLM 分析
    try:
        prompt = (
            f"## EXPERIMENT RESULTS\n{json.dumps(results, ensure_ascii=False)[:3000]}\n\n"
            f"## SUCCESS CRITERIA\n{json.dumps(criteria, ensure_ascii=False)[:1000]}\n\n"
            f"## BASELINES\n{json.dumps(bls, ensure_ascii=False)[:1000]}\n\n"
            f"## HYPOTHESIS\n{json.dumps(hyp, ensure_ascii=False)[:1000]}\n\n"
            "Analyze the results and return the structured report."
        )
        return _call_llm(MINGBIAN_SYSTEM_PROMPT, prompt, max_tokens=2000)
    except Exception:
        return _heuristic_analysis(results, criteria, bls, hyp)


def diagnose_inconclusive(results: dict | None = None,
                          experiment_design: dict | None = None) -> str:
    """诊断 inconclusive 原因。"""
    if not results:
        return "insufficient_data"
    primary = results.get("primary_results", {})
    if not primary:
        return "insufficient_data"
    p_val = results.get("statistical_tests", {}).get("p_value", 0.5)
    if p_val > 0.05:
        return "insufficient_data"
    return "genuine_ambiguity"


def update_method_memory(experiment_outcome: str = "",
                         patterns: list | None = None) -> None:
    """记录成功/失败模式。"""
    try:
        from ._project import TOOL_RESULTS_DIR
    except Exception:
        return
    from pathlib import Path
    mem = Path(__file__).parent / ".science" / "method_memory.jsonl"
    mem.parent.mkdir(parents=True, exist_ok=True)
    import time as _time
    with open(mem, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": _time.time(),
            "outcome": experiment_outcome,
            "patterns": patterns or [],
        }, ensure_ascii=False) + "\n")
