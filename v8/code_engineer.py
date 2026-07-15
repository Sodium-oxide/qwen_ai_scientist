"""
CodeEngineer 适配器 — 对接角色三 modules 7 真实仿真结果
=======================================================
本模块是 PaperWriter 与角色三 agents/code_engineer.py 之间的薄适配层。
不启动仿真、不生成代码、不调用 LLM —— 只读取队友产出的标准化 result.json
并转换为 PaperWriter 可消费的 project_context 格式。

队友模块: agents/code_engineer.py  CodeEngineer().run_plan(...)
结果路径: results/exp_power_*.json
真实引擎: ANDES (TDS), pandapower (OPF), LTBAMS (Co-sim)
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


# ── 公开 API ──

def load_result(result_path: str | Path) -> dict[str, Any]:
    """读取队友模块7产出的标准化 result.json"""
    with open(result_path, encoding="utf-8") as f:
        return json.load(f)


def write_code(project_id: str, experiment_protocol: dict | None = None,
               hypothesis: dict | None = None) -> str:
    """适配器桩：返回队友模块7的调用说明。实际代码由队友的 CodeEngineer 生成。"""
    return (
        "# 此函数由队友 agents/code_engineer.py 提供真实实现。\n"
        "# 调用方式: CodeEngineer().run_plan('examples/module7_*_plan.json')\n"
        "# 结果写入 results/exp_power_*.json"
    )


def execute_code(project_id: str, code: str = "",
                 experiment_protocol: dict | None = None,
                 hypothesis: dict | None = None,
                 *,
                 result_path: str = "results/exp_power_tds_5s_stable_result.json",
                 ) -> dict[str, Any]:
    """
    读取队友模块7的真实仿真结果并转为 PaperWriter 兼容格式。

    队友产出字段:
        schema_version, experiment_id, branch, simulation_type, engine,
        status, primary_metric, metadata, metric_directions,
        success_criteria, observations, artifacts

    本适配器负责:
        1. 加载真实结果 JSON
        2. 对 observations 做描述统计（均值/标准差/CI）
        3. 输出 PaperWriter 可消费的标准 dict

    如果结果文件不存在，回退到调用队友的 CodeEngineer。
    """
    try:
        raw = load_result(result_path)
    except FileNotFoundError:
        # 尝试调队友代码
        try:
            from agents.code_engineer import CodeEngineer
            plan = _find_plan_file(project_id)
            engine = CodeEngineer()
            report = engine.run_plan(plan, backend_override="local", qwen_override=False)
            raw = _load_from_report(report)
        except Exception:
            return _fallback_simulated(project_id, hypothesis, experiment_protocol)

    # 从 observations 提取统计量
    obs = raw.get("observations", [])
    baseline_metrics = [o["metrics"] for o in obs if o.get("condition") == "baseline"]
    proposed_metrics = [o["metrics"] for o in obs if o.get("condition") == "proposed"]

    primary_key = raw.get("primary_metric", "cct_seconds")
    baseline_vals = [m.get(primary_key, 0) for m in baseline_metrics if primary_key in m]
    proposed_vals = [m.get(primary_key, 0) for m in proposed_metrics if primary_key in m]

    def _summarize(vals: list[float], label: str) -> dict:
        if not vals:
            return {"condition": label, "n": 0}
        n = len(vals)
        mean = statistics.mean(vals)
        stdev = statistics.stdev(vals) if n >= 2 else 0.0
        se = stdev / (n ** 0.5) if n > 0 else 0.0
        return {
            "condition": label,
            "n": n,
            "mean": round(mean, 6),
            "std": round(stdev, 6),
            "sem": round(se, 6),
            "ci95_low": round(mean - 1.96 * se, 6) if n >= 2 else mean,
            "ci95_high": round(mean + 1.96 * se, 6) if n >= 2 else mean,
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
        }

    baseline_summary = _summarize(baseline_vals, "baseline")
    proposed_summary = _summarize(proposed_vals, "proposed")

    # 效果量
    delta = proposed_summary["mean"] - baseline_summary["mean"]
    pooled_std = ((baseline_summary.get("std", 0) ** 2 + proposed_summary.get("std", 0) ** 2) / 2) ** 0.5 if baseline_summary["n"] >= 2 else 0
    cohens_d = round(delta / pooled_std, 3) if pooled_std > 0 else 0.0

    return {
        "status": raw.get("status", "unknown"),
        "simulated": False,
        "evidence_type": raw.get("metadata", {}).get("evidence_type", "real_simulation"),
        "engine": raw.get("engine", "unknown"),
        "simulation_type": raw.get("simulation_type", "unknown"),
        "primary_metric": primary_key,
        "branch": raw.get("branch", ""),
        "primary_results": {
            "baseline": baseline_summary,
            "proposed": proposed_summary,
            "delta": round(delta, 6),
            "cohens_d": cohens_d,
            "n_total": baseline_summary["n"] + proposed_summary["n"],
        },
        "baseline_comparison": {
            "Baseline (original)": {"n": baseline_summary["n"], primary_key: baseline_summary["mean"]},
            "Proposed (our method)": {"n": proposed_summary["n"], primary_key: proposed_summary["mean"]},
        },
        "statistical_tests": {
            "test_used": "Welch independent t-test (via Module 8)",
            "cohens_d": cohens_d,
            "ci95_baseline": f"[{baseline_summary['ci95_low']:.4f}, {baseline_summary['ci95_high']:.4f}]",
            "ci95_proposed": f"[{proposed_summary['ci95_low']:.4f}, {proposed_summary['ci95_high']:.4f}]",
        },
        "source_file": str(Path(result_path).resolve()),
        "raw_observations": obs,
    }


def fix_bug(error_log: str, code: str, max_iterations: int = 5) -> str:
    """适配器桩：实际修复逻辑在队友的 CodeEngineer 中。"""
    return code


def optimize(code: str, target: str = "speed") -> str:
    """适配器桩：实际优化逻辑在队友的 CodeEngineer 中。"""
    return code


# ── 内部辅助 ──

def _find_plan_file(project_id: str) -> str:
    """根据 project_id 查找对应的 plan JSON"""
    candidates = [
        "examples/module7_power_tds_plan.json",
        "examples/module7_power_opf_plan.json",
        "examples/module7_general_plan.json",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[0]


def _load_from_report(report: Any) -> dict:
    """从 CodeEngineer.run_plan() 返回值提取结果"""
    if isinstance(report, dict):
        return report
    if hasattr(report, "result_path") and Path(report.result_path).exists():
        return load_result(report.result_path)
    return {}


def _fallback_simulated(project_id: str, hypothesis: dict | None,
                        protocol: dict | None) -> dict:
    """队友代码不可用且无结果文件时的降级方案"""
    return {
        "status": "unavailable",
        "simulated": True,
        "evidence_type": "fallback_simulated",
        "primary_metric": "unknown",
        "primary_results": {},
        "baseline_comparison": {},
        "statistical_tests": {},
        "note": "[SIMULATED] 队友模块7不可用，使用占位数据。请将 results/exp_power_*.json 放入工作目录。",
    }
