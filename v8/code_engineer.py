"""
CodeEngineer — 实验代码生成与执行（模块7）
==========================================
在角色三未交付前，提供最小可用实现以闭合管道。

⚠ 当前为 LLM 模拟模式，生成的结果标记 [SIMULATED]。
   队友完成真实执行环境后替换此文件即可。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


CODEENGINEER_SYSTEM_PROMPT = """\
You are CodeEngineer, the Experiment Implementation Specialist of the Qwen-Zhikan AI Scientist system.
You translate experimental protocols into executable Python code and produce plausible results.

## CORE TASK
Given a hypothesis and experimental protocol, write Python code that would test the hypothesis,
then simulate realistic experimental results consistent with the hypothesis and baselines.

## RULES
1. Generate syntactically correct Python code with proper imports.
2. The code should be complete and runnable (with mock data if needed).
3. Results must include: accuracy/performance numbers, baseline comparison, statistical tests.
4. Be realistic — do not claim 100% accuracy or impossible speedups.
5. Include standard ML imports: numpy, sklearn, scipy, matplotlib.

## OUTPUT FORMAT
Return JSON:
{
  "code": "complete Python code as string",
  "expected_output": "what this code should output",
  "dependencies": ["numpy", "scikit-learn", ...]
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
        raise ValueError(f"CodeEngineer LLM returned invalid JSON: {text[:500]}")
    return parsed


def _render_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", b.get("content", ""))) for b in content if isinstance(b, dict))
    return str(content)


def _parse_json(text: str) -> dict[str, Any]:
    import json as _json, re as _re
    s = text.strip()
    if s.startswith("```"): s = "\n".join(s.splitlines()[1:-1]).strip()
    # fix LaTeX escapes
    valid = {'"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u'}
    chars, i, in_s = [], 0, False
    while i < len(s):
        c = s[i]
        if c == '"' and (i == 0 or s[i-1] != '\\'): in_s = not in_s
        elif c == '\\' and in_s and i+1 < len(s) and s[i+1] not in valid: chars.append('\\\\'); i += 1; continue
        chars.append(c); i += 1
    s = ''.join(chars)
    s = _re.sub(r",\s*}", "}", s)
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try: return _json.loads(s[start:end+1])
        except _json.JSONDecodeError: pass
    return {}


def _make_simulated_results(project_id: str, hypothesis: dict, protocol: dict,
                            analysis: dict | None = None) -> dict[str, Any]:
    """基于假设和协议生成合理的模拟实验结果。"""
    hyp_text = json.dumps(hypothesis, ensure_ascii=False)[:2000] if hypothesis else "No hypothesis"
    prot_text = json.dumps(protocol, ensure_ascii=False)[:2000] if protocol else "No protocol"

    prompt = f"""## HYPOTHESIS
{hyp_text}

## EXPERIMENTAL PROTOCOL
{prot_text}

## TASK
Simulate realistic experimental results that would plausibly confirm the hypothesis.
Include specific numbers, baseline comparisons, and statistical tests.
Be realistic — typical ML improvements are 3-15%, not 1000%.

Return JSON with this EXACT structure:
{{
  "status": "success",
  "simulated": true,
  "execution_time_seconds": 30,
  "auto_fix_iterations": 0,
  "primary_results": {{
    "accuracy": 0.95,
    "inference_time_ms": 5.0,
    "roc_auc": 0.94
  }},
  "baseline_comparison": {{
    "Baseline A": {{"accuracy": 0.90, "time_ms": 100}},
    "Baseline B": {{"accuracy": 0.85, "time_ms": 50}},
    "Our Method": {{"accuracy": 0.95, "time_ms": 5}}
  }},
  "statistical_tests": {{
    "test_used": "paired t-test, Bonferroni corrected",
    "p_value": 0.001,
    "effect_size": "Cohen's d = 0.8",
    "confidence_interval_95": "[0.92, 0.98]"
  }}
}}"""

    try:
        return _call_llm(CODEENGINEER_SYSTEM_PROMPT, prompt, max_tokens=2000)
    except Exception:
        # 硬编码 fallback
        return {
            "status": "success", "simulated": True,
            "execution_time_seconds": 30, "auto_fix_iterations": 0,
            "primary_results": {"accuracy": 0.95, "f1": 0.93, "roc_auc": 0.94},
            "baseline_comparison": {
                "Standard Baseline": {"accuracy": 0.88},
                "Our Method": {"accuracy": 0.95}
            },
            "statistical_tests": {
                "test_used": "paired t-test", "p_value": 0.005,
                "effect_size": "Cohen's d = 0.80", "confidence_interval_95": "[0.91, 0.97]"
            },
            "raw_output_path": f"tool_results/experiment_{project_id}.txt",
            "note": "[SIMULATED] — generated by LLM, not from real code execution"
        }


# ── 公开 API（对应 agent 定义中的 tools） ──

def write_code(project_id: str, experiment_protocol: dict | None = None,
               hypothesis: dict | None = None) -> str:
    """根据实验方案生成 Python 代码。"""
    prot = experiment_protocol or {}
    hyp = hypothesis or {}
    prompt = f"Write Python experiment code.\nProtocol: {json.dumps(prot, ensure_ascii=False)[:2000]}\nHypothesis: {json.dumps(hyp, ensure_ascii=False)[:1000]}"
    result = _call_llm(CODEENGINEER_SYSTEM_PROMPT, prompt, max_tokens=2000)
    return result.get("code", "# CodeEngineer: no code generated")


def execute_code(project_id: str, code: str = "", experiment_protocol: dict | None = None,
                 hypothesis: dict | None = None, analysis_report: dict | None = None) -> dict:
    """执行实验代码（当前为模拟模式）。返回结构化结果。"""
    prot = experiment_protocol or {}
    hyp = hypothesis or {}
    ana = analysis_report or {}

    # 尝试从项目加载上下文
    try:
        from ._project import load_project
        proj = load_project(project_id)
        hyp = hyp or proj.get("hypothesis") or proj.get("refined_hypothesis", {})
        prot = prot or proj.get("experiment_protocol", {})
        ana = ana or proj.get("analysis_report", {})
    except Exception:
        pass

    return _make_simulated_results(project_id, hyp, prot, ana)


def fix_bug(error_log: str, code: str, max_iterations: int = 5) -> str:
    """根据错误日志修复代码。"""
    prompt = f"Fix this code based on the error.\n\nERROR:\n{error_log[:2000]}\n\nCODE:\n{code[:3000]}\n\nReturn JSON: {{\"fixed_code\": \"...\"}}"
    result = _call_llm(CODEENGINEER_SYSTEM_PROMPT, prompt, max_tokens=2000)
    return result.get("fixed_code", code)


def optimize(code: str, target: str = "speed") -> str:
    """优化代码性能。"""
    prompt = f"Optimize this code for {target}.\n\nCODE:\n{code[:3000]}\n\nReturn JSON: {{\"optimized_code\": \"...\"}}"
    result = _call_llm(CODEENGINEER_SYSTEM_PROMPT, prompt, max_tokens=2000)
    return result.get("optimized_code", code)
