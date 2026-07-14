"""模块8：MingBian 实验结果统计分析智能体。

本模块只消费模块7生成的结构化结果，不启动仿真、不生成实验数据，也不允许
大模型修改统计数字。核心输出包括：

1. 通用描述统计、95%置信区间、Welch独立样本t检验、Cohen's d；
2. supported / refuted / inconclusive 三分类假设判定；
3. OPF、TDS、AMS协同仿真的电力专属指标与安全门槛；
4. 带显著性星标的图表、Markdown报告和模块9可消费的JSON；
5. 数据集、实验参数、原始假设三层迭代建议与方法记忆更新。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0"
MODULE_VERSION = "0.2.0"
MIN_SAMPLE_SIZE = 3
DEFAULT_ALPHA = 0.05


class ResultValidationError(ValueError):
    """模块7结果不满足模块8输入契约。"""


@dataclass
class GroupStats:
    n: int
    mean: float
    std: float
    variance: float
    standard_error: float
    ci95_low: float
    ci95_high: float
    minimum: float
    maximum: float


@dataclass
class MetricAnalysis:
    metric: str
    direction: str
    comparison_basis: str
    baseline: GroupStats
    proposed: GroupStats
    raw_delta: float
    favorable_delta: float
    relative_improvement_percent: float | None
    t_statistic: float
    degrees_of_freedom: float
    p_value: float
    significance: str
    cohens_d: float | None
    direction_adjusted_effect_size: float | None
    effect_magnitude: str
    difference_ci95_low: float
    difference_ci95_high: float
    practical_threshold: dict[str, float]
    high_variance: bool
    zero_variance: bool
    verdict: str
    verdict_reason: str


@dataclass
class MingBianReport:
    schema_version: str
    module: str
    module_version: str
    experiment_id: str
    hypothesis_id: str
    hypothesis: str
    branch: str
    simulation_type: str
    engine: str
    status: str
    primary_metric: str
    hypothesis_verdict: str
    verdict_reason: str
    alpha: float
    metrics: dict[str, dict[str, Any]]
    power_analysis: dict[str, Any]
    validation_analysis: dict[str, Any]
    data_quality: dict[str, Any]
    iteration_recommendations: list[dict[str, Any]]
    method_memory_update: dict[str, Any]
    downstream_contract: dict[str, Any]
    artifacts: dict[str, str]
    generated_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MingBian:
    """模块8主入口。"""

    def __init__(
        self,
        project_root: str | Path | None = None,
        results_root: str | Path = "results",
        *,
        alpha: float = DEFAULT_ALPHA,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        root = Path(results_root)
        if not root.is_absolute():
            root = self.project_root / root
        self.results_root = root.resolve()
        self.results_root.mkdir(parents=True, exist_ok=True)
        if not 0 < alpha < 1:
            raise ValueError("alpha must be between 0 and 1")
        self.alpha = float(alpha)

    def analyze_file(
        self,
        result_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        primary_metric: str | None = None,
    ) -> MingBianReport:
        path = Path(result_path).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResultValidationError(f"Cannot read module7 result JSON: {exc}") from exc
        return self.analyze_data(
            data,
            source_path=path,
            output_dir=output_dir,
            primary_metric=primary_metric,
        )

    def analyze_data(
        self,
        result: Mapping[str, Any],
        *,
        source_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        primary_metric: str | None = None,
    ) -> MingBianReport:
        data = self._validate_input(result)
        experiment_id = str(data["experiment_id"])
        branch = str(data["branch"])
        simulation_type = str(data["simulation_type"])
        output = self._resolve_output_dir(output_dir, experiment_id)
        output.mkdir(parents=True, exist_ok=True)

        grouped, collection_warnings = self._collect_metrics(data["observations"])
        metric_directions = self._metric_directions(data, grouped)
        selected_primary = primary_metric or self._infer_primary_metric(
            data, grouped, simulation_type
        )
        if selected_primary not in grouped:
            raise ResultValidationError(
                f"Primary metric '{selected_primary}' has no baseline/proposed observations"
            )

        metric_reports: dict[str, MetricAnalysis] = {}
        for metric in sorted(grouped):
            conditions = grouped[metric]
            if not conditions.get("baseline") or not conditions.get("proposed"):
                continue
            direction = metric_directions.get(metric, self._known_direction(metric))
            thresholds = self._thresholds_for_metric(data, metric)
            metric_reports[metric] = self._analyze_metric(
                metric,
                conditions["baseline"],
                conditions["proposed"],
                direction,
                thresholds,
            )

        if selected_primary not in metric_reports:
            raise ResultValidationError(
                f"Primary metric '{selected_primary}' could not be statistically analyzed"
            )

        power_analysis = self._power_analysis(data, metric_reports, grouped)
        validation_analysis = self._validation_analysis(data, grouped, power_analysis)
        primary_report = metric_reports[selected_primary]
        overall_verdict = primary_report.verdict
        overall_reason = primary_report.verdict_reason
        safety_gate = power_analysis.get("safety_gate", {})
        if overall_verdict == "supported" and safety_gate.get("passed") is False:
            overall_verdict = "refuted"
            overall_reason = (
                "Primary metric improved, but the power-system safety/constraint gate failed: "
                + str(safety_gate.get("reason") or "unspecified safety violation")
            )

        data_quality = self._data_quality(
            data,
            grouped,
            metric_reports,
            collection_warnings,
        )
        if data_quality["insufficient_sample"]:
            overall_verdict = "inconclusive"
            overall_reason = "At least one primary comparison has fewer than three valid repetitions."
        elif primary_report.high_variance and primary_report.p_value >= self.alpha:
            overall_verdict = "inconclusive"
            overall_reason = "Primary metric uncertainty is too large for a stable conclusion."

        recommendations = self._recommendations(
            data,
            selected_primary,
            overall_verdict,
            metric_reports,
            power_analysis,
            data_quality,
        )
        method_memory = self._method_memory(
            data,
            selected_primary,
            overall_verdict,
            metric_reports[selected_primary],
            data_quality,
        )

        report_path = output / "analysis_report.json"
        markdown_path = output / "mingbian_report.md"
        plot_script_path = output / "plot_mingbian_results.py"
        figure_path = output / "figures" / "mingbian_comparison.png"
        memory_path = output / "method_memory_update.json"

        report = MingBianReport(
            schema_version=SCHEMA_VERSION,
            module="module8_mingbian",
            module_version=MODULE_VERSION,
            experiment_id=experiment_id,
            hypothesis_id=str(data.get("hypothesis_id") or ""),
            hypothesis=str(data.get("hypothesis") or ""),
            branch=branch,
            simulation_type=simulation_type,
            engine=str(data.get("engine") or ""),
            status="success",
            primary_metric=selected_primary,
            hypothesis_verdict=overall_verdict,
            verdict_reason=overall_reason,
            alpha=self.alpha,
            metrics={name: asdict(item) for name, item in metric_reports.items()},
            power_analysis=power_analysis,
            validation_analysis=validation_analysis,
            data_quality=data_quality,
            iteration_recommendations=recommendations,
            method_memory_update=method_memory,
            downstream_contract={
                "consumer": "module9_paper_writer",
                "source_result": str(source_path) if source_path else "in_memory",
                "claims_must_use": [
                    "hypothesis_verdict",
                    "metrics",
                    "power_analysis",
                    "validation_analysis",
                    "data_quality",
                    "iteration_recommendations",
                ],
                "numeric_values_are_verified": True,
                "paper_writer_must_not_recalculate_or_modify_numbers": True,
            },
            artifacts={
                "analysis_json": str(report_path),
                "markdown_report": str(markdown_path),
                "plot_script": str(plot_script_path),
                "comparison_figure": str(figure_path),
                "method_memory": str(memory_path),
            },
            generated_at=time.time(),
        )

        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        markdown_path.write_text(self._markdown_report(report), encoding="utf-8")
        plot_script_path.write_text(_PLOT_SCRIPT, encoding="utf-8")
        memory_path.write_text(
            json.dumps(method_memory, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        figure_status = self._render_figure(report_path, figure_path)
        if figure_status != "success":
            report.artifacts["comparison_figure"] = figure_status
            report_path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
        return report

    @staticmethod
    def _validate_input(result: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise ResultValidationError("Module7 result must be a JSON object")
        data = dict(result)
        required = {
            "experiment_id",
            "hypothesis_id",
            "branch",
            "simulation_type",
            "engine",
            "status",
            "observations",
        }
        missing = sorted(required - set(data))
        if missing:
            raise ResultValidationError("Module7 result missing fields: " + ", ".join(missing))
        if str(data.get("status")) != "success":
            raise ResultValidationError("Module7 result status is not success")
        if str(data.get("branch")) not in {"general", "power"}:
            raise ResultValidationError("branch must be general or power")
        observations = data.get("observations")
        if not isinstance(observations, list) or not observations:
            raise ResultValidationError("observations must be a non-empty list")
        conditions = {
            str(item.get("condition") or "")
            for item in observations
            if isinstance(item, Mapping)
        }
        if not {"baseline", "proposed"}.issubset(conditions):
            raise ResultValidationError("observations must contain baseline and proposed")
        return data

    @staticmethod
    def _collect_metrics(
        observations: Sequence[Any],
    ) -> tuple[dict[str, dict[str, list[float]]], list[str]]:
        grouped: dict[str, dict[str, list[float]]] = {}
        warnings: list[str] = []
        for index, observation in enumerate(observations):
            if not isinstance(observation, Mapping):
                warnings.append(f"observation[{index}] is not an object")
                continue
            condition = str(observation.get("condition") or "")
            if condition not in {"baseline", "proposed"}:
                continue
            metrics = observation.get("metrics")
            if not isinstance(metrics, Mapping):
                warnings.append(f"observation[{index}] has no metrics object")
                continue
            for name, raw_value in metrics.items():
                value = _finite_number(raw_value)
                if value is None:
                    continue
                grouped.setdefault(str(name), {"baseline": [], "proposed": []})[
                    condition
                ].append(value)
        incomplete = [
            name
            for name, values in grouped.items()
            if not values["baseline"] or not values["proposed"]
        ]
        for name in incomplete:
            warnings.append(f"metric '{name}' is missing one comparison condition")
        return grouped, warnings

    @staticmethod
    def _metric_directions(
        data: Mapping[str, Any],
        grouped: Mapping[str, Any],
    ) -> dict[str, str]:
        directions = data.get("metric_directions")
        output = dict(directions) if isinstance(directions, Mapping) else {}
        for metric in grouped:
            output.setdefault(metric, MingBian._known_direction(metric))
        return {str(k): str(v).lower() for k, v in output.items()}

    @staticmethod
    def _known_direction(metric: str) -> str:
        lower = metric.lower()
        minimize = (
            "loss",
            "cost",
            "violation",
            "error",
            "angle",
            "deviation",
            "runtime",
            "emission",
        )
        maximize = (
            "cct",
            "nadir",
            "accuracy",
            "efficiency",
            "stability",
            "stable",
            "primary_metric",
        )
        if any(token in lower for token in minimize):
            return "minimize"
        if any(token in lower for token in maximize):
            return "maximize"
        return "maximize"

    @staticmethod
    def _infer_primary_metric(
        data: Mapping[str, Any],
        grouped: Mapping[str, Any],
        simulation_type: str,
    ) -> str:
        declared = str(data.get("primary_metric") or "")
        if declared in grouped:
            return declared
        preferred = {
            "general": ["primary_metric"],
            "opf": ["network_loss_mw", "generation_cost"],
            "tds": ["cct_seconds", "max_rotor_angle_deg"],
            "cosim": ["dispatch_cost", "voltage_nadir_pu"],
        }
        for metric in preferred.get(simulation_type, []):
            if metric in grouped:
                return metric
        return sorted(grouped)[0]

    @staticmethod
    def _thresholds_for_metric(
        data: Mapping[str, Any], metric: str
    ) -> dict[str, float]:
        thresholds: dict[str, float] = {
            "minimum_absolute_improvement": 0.0,
            "minimum_relative_improvement_percent": 0.0,
        }
        all_criteria = data.get("success_criteria") or data.get("metric_thresholds")
        if not isinstance(all_criteria, Mapping):
            return thresholds
        criterion = all_criteria.get(metric)
        if isinstance(criterion, (int, float)) and not isinstance(criterion, bool):
            thresholds["minimum_absolute_improvement"] = float(criterion)
        elif isinstance(criterion, Mapping):
            for key in tuple(thresholds):
                raw = criterion.get(key)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    thresholds[key] = float(raw)
        return thresholds

    def _analyze_metric(
        self,
        metric: str,
        baseline_values: Sequence[float],
        proposed_values: Sequence[float],
        direction: str,
        thresholds: dict[str, float],
    ) -> MetricAnalysis:
        baseline_stats = _group_stats(baseline_values, self.alpha)
        proposed_stats = _group_stats(proposed_values, self.alpha)
        comparison_baseline, comparison_proposed, basis = _comparison_values(
            baseline_values, proposed_values, direction
        )
        t_stat, df, p_value, diff_ci = _welch_test(
            comparison_baseline, comparison_proposed, self.alpha
        )
        raw_delta = proposed_stats.mean - baseline_stats.mean
        if direction.startswith("target_"):
            comparison_baseline_mean = statistics.fmean(comparison_baseline)
            comparison_proposed_mean = statistics.fmean(comparison_proposed)
            favorable_delta = comparison_baseline_mean - comparison_proposed_mean
            relative_improvement = _relative_improvement(
                comparison_baseline_mean, comparison_proposed_mean, "minimize"
            )
        else:
            favorable_delta = _favorable_delta(
                baseline_stats.mean, proposed_stats.mean, direction
            )
            relative_improvement = _relative_improvement(
                baseline_stats.mean, proposed_stats.mean, direction
            )
        cohens_d = _cohens_d(comparison_baseline, comparison_proposed)
        adjusted_d = None
        if cohens_d is not None:
            adjusted_d = (
                -cohens_d
                if direction == "minimize" or direction.startswith("target_")
                else cohens_d
            )
        effect_magnitude = _effect_magnitude(adjusted_d)
        pooled_sd = _pooled_std(comparison_baseline, comparison_proposed)
        zero_variance = pooled_sd == 0.0
        high_variance = (
            p_value >= self.alpha
            and pooled_sd > max(abs(favorable_delta) * 3.0, 1e-12)
        )
        n_ok = (
            baseline_stats.n >= MIN_SAMPLE_SIZE
            and proposed_stats.n >= MIN_SAMPLE_SIZE
        )
        abs_ok = favorable_delta > thresholds["minimum_absolute_improvement"]
        rel_ok = (
            relative_improvement is None
            or relative_improvement
            > thresholds["minimum_relative_improvement_percent"]
        )
        if not n_ok:
            verdict = "inconclusive"
            reason = "Fewer than three valid repetitions in baseline or proposed condition."
        elif high_variance:
            verdict = "inconclusive"
            reason = "Within-condition variance is too large relative to the observed improvement."
        elif p_value < self.alpha and abs_ok and rel_ok:
            verdict = "supported"
            reason = (
                f"Proposed condition improves the direction-adjusted metric with p={p_value:.4g} "
                "and meets the practical threshold."
            )
        elif favorable_delta <= 0:
            verdict = "refuted"
            reason = "Proposed condition does not improve the metric in the declared direction."
        elif not abs_ok or not rel_ok:
            verdict = "refuted"
            reason = "Observed improvement does not meet the predefined practical threshold."
        else:
            verdict = "refuted"
            reason = f"Observed improvement is not statistically significant at alpha={self.alpha}."

        return MetricAnalysis(
            metric=metric,
            direction=direction,
            comparison_basis=basis,
            baseline=baseline_stats,
            proposed=proposed_stats,
            raw_delta=raw_delta,
            favorable_delta=favorable_delta,
            relative_improvement_percent=relative_improvement,
            t_statistic=t_stat,
            degrees_of_freedom=df,
            p_value=p_value,
            significance=_significance_stars(p_value),
            cohens_d=cohens_d,
            direction_adjusted_effect_size=adjusted_d,
            effect_magnitude=effect_magnitude,
            difference_ci95_low=diff_ci[0],
            difference_ci95_high=diff_ci[1],
            practical_threshold=thresholds,
            high_variance=high_variance,
            zero_variance=zero_variance,
            verdict=verdict,
            verdict_reason=reason,
        )

    def _power_analysis(
        self,
        data: Mapping[str, Any],
        reports: Mapping[str, MetricAnalysis],
        grouped: Mapping[str, Mapping[str, Sequence[float]]],
    ) -> dict[str, Any]:
        if str(data.get("branch")) != "power":
            return {}
        simulation_type = str(data.get("simulation_type"))
        if simulation_type == "opf":
            return self._opf_analysis(reports)
        if simulation_type == "tds":
            return self._tds_analysis(reports, grouped)
        if simulation_type == "cosim":
            return self._cosim_analysis(reports, grouped)
        return {"simulation_type": simulation_type, "safety_gate": {"passed": None}}

    @staticmethod
    def _opf_analysis(reports: Mapping[str, MetricAnalysis]) -> dict[str, Any]:
        violations = reports.get("voltage_violation_count")
        voltage_pass = violations is None or violations.proposed.mean <= 0.0
        reason = (
            "No proposed-condition voltage violations were observed."
            if voltage_pass
            else f"Proposed condition has mean voltage violations={violations.proposed.mean:.4g}."
        )
        return {
            "simulation_type": "opf",
            "network_loss_reduction_percent": _metric_improvement(
                reports.get("network_loss_mw")
            ),
            "generation_cost_reduction_percent": _metric_improvement(
                reports.get("generation_cost")
            ),
            "proposed_voltage_violation_mean": (
                violations.proposed.mean if violations else None
            ),
            "safety_gate": {"passed": voltage_pass, "reason": reason},
        }

    @staticmethod
    def _tds_analysis(
        reports: Mapping[str, MetricAnalysis],
        grouped: Mapping[str, Mapping[str, Sequence[float]]],
    ) -> dict[str, Any]:
        cct = reports.get("cct_seconds")
        angle = reports.get("max_rotor_angle_deg")
        nadir = reports.get("voltage_nadir_pu")
        stable_values = grouped.get("stable", {}).get("proposed", [])
        stability_rate = (
            sum(stable_values) / len(stable_values) if stable_values else None
        )
        angle_pass = angle is None or angle.proposed.maximum < 180.0
        nadir_pass = nadir is None or nadir.proposed.minimum >= 0.70
        stable_pass = stability_rate is None or stability_rate >= 0.80
        passed = angle_pass and nadir_pass and stable_pass
        failed = []
        if not angle_pass:
            failed.append("rotor-angle separation reached or exceeded 180 degrees")
        if not nadir_pass:
            failed.append("voltage nadir fell below 0.70 p.u.")
        if not stable_pass:
            failed.append("proposed stability rate is below 80%")
        return {
            "simulation_type": "tds",
            "cct_improvement_percent": _metric_improvement(cct),
            "proposed_cct_mean_seconds": cct.proposed.mean if cct else None,
            "proposed_max_rotor_angle_deg": angle.proposed.maximum if angle else None,
            "proposed_voltage_nadir_min_pu": nadir.proposed.minimum if nadir else None,
            "proposed_stability_rate": stability_rate,
            "safety_gate": {
                "passed": passed,
                "reason": "All transient safety gates passed." if passed else "; ".join(failed),
            },
        }

    @staticmethod
    def _cosim_analysis(
        reports: Mapping[str, MetricAnalysis],
        grouped: Mapping[str, Mapping[str, Sequence[float]]],
    ) -> dict[str, Any]:
        cost = reports.get("dispatch_cost")
        loss = reports.get("network_loss_mw")
        nadir = reports.get("voltage_nadir_pu")
        base_stable = grouped.get("stable", {}).get("baseline", [])
        prop_stable = grouped.get("stable", {}).get("proposed", [])
        dispatch_values = grouped.get("dispatch_converged", {}).get("proposed", [])
        handoff_values = grouped.get("dynamic_handoff", {}).get("proposed", [])
        tds_values = grouped.get("tds_converged", {}).get("proposed", [])
        base_rate = sum(base_stable) / len(base_stable) if base_stable else None
        prop_rate = sum(prop_stable) / len(prop_stable) if prop_stable else None
        dispatch_rate = sum(dispatch_values) / len(dispatch_values) if dispatch_values else None
        handoff_rate = sum(handoff_values) / len(handoff_values) if handoff_values else None
        tds_rate = sum(tds_values) / len(tds_values) if tds_values else None
        stability_not_worse = (
            base_rate is None or prop_rate is None or prop_rate >= base_rate
        )
        nadir_pass = nadir is None or nadir.proposed.minimum >= 0.70
        dispatch_pass = dispatch_rate is None or dispatch_rate >= 1.0
        handoff_pass = handoff_rate is None or handoff_rate >= 1.0
        tds_pass = tds_rate is None or tds_rate >= 0.80
        passed = stability_not_worse and nadir_pass and dispatch_pass and handoff_pass and tds_pass
        failed = []
        if not dispatch_pass:
            failed.append("dispatch solver did not converge for all proposed cases")
        if not handoff_pass:
            failed.append("AMS-to-ANDES dynamic handoff was incomplete")
        if not tds_pass:
            failed.append("ANDES dynamic simulation did not converge often enough")
        if not stability_not_worse or not nadir_pass:
            failed.append("dynamic stability or voltage-nadir gate degraded after dispatch")
        return {
            "simulation_type": "cosim",
            "dispatch_cost_improvement_percent": _metric_improvement(cost),
            "network_loss_improvement_percent": _metric_improvement(loss),
            "voltage_nadir_improvement_percent": _metric_improvement(nadir),
            "baseline_stability_rate": base_rate,
            "proposed_stability_rate": prop_rate,
            "dispatch_convergence_rate": dispatch_rate,
            "dynamic_handoff_rate": handoff_rate,
            "tds_convergence_rate": tds_rate,
            "safety_gate": {
                "passed": passed,
                "reason": "Co-simulation dispatch and dynamic gates passed." if passed else "; ".join(failed),
            },
        }

    @staticmethod
    def _validation_analysis(
        data: Mapping[str, Any],
        grouped: Mapping[str, Mapping[str, Sequence[float]]],
        power_analysis: Mapping[str, Any],
    ) -> dict[str, Any]:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
        evidence_type = str(metadata.get("evidence_type") or "unspecified")
        case_source = str(metadata.get("case_source") or "unspecified")
        real_execution = bool(metadata.get("real_execution_enabled") or evidence_type.startswith("real_"))
        simulation_type = str(data.get("simulation_type") or "")
        min_count = min(
            (
                min(len(values.get("baseline", [])), len(values.get("proposed", [])))
                for values in grouped.values()
            ),
            default=0,
        )
        smoke_test = real_execution and min_count < MIN_SAMPLE_SIZE
        safety = power_analysis.get("safety_gate", {})
        safety_passed = safety.get("passed")
        engineering_passed = bool(real_execution and safety_passed is not False)
        limitations: list[str] = []
        if smoke_test:
            limitations.append("Only a smoke-test number of repetitions is available; do not claim statistical support.")
        if "fallback" in case_source.lower() or "fallback" in evidence_type.lower():
            limitations.append("Fallback or surrogate case source limits external validity.")
        if simulation_type == "cosim":
            tds_rate = power_analysis.get("tds_convergence_rate")
            handoff_rate = power_analysis.get("dynamic_handoff_rate")
            if handoff_rate is not None and handoff_rate >= 1.0:
                limitations.append("AMS-to-ANDES handoff was exercised.")
            if tds_rate is not None and tds_rate < 1.0:
                limitations.append("Dynamic handoff occurred, but downstream TDS did not fully converge.")
        if simulation_type == "tds":
            tds_values = grouped.get("tds_converged", {}).get("proposed", [])
            if tds_values and sum(tds_values) / len(tds_values) < 1.0:
                limitations.append("At least one real ANDES TDS run terminated before convergence.")
        if not limitations:
            limitations.append("No validation-specific limitation was detected beyond the statistical report.")
        if engineering_passed and smoke_test:
            validation_verdict = "engineering_validated_statistically_insufficient"
        elif engineering_passed:
            validation_verdict = "engineering_validated"
        elif real_execution:
            validation_verdict = "real_execution_with_safety_or_convergence_failure"
        else:
            validation_verdict = "demonstration_or_fallback_only"
        return {
            "evidence_type": evidence_type,
            "case_source": case_source,
            "real_execution_enabled": real_execution,
            "minimum_condition_repetitions": min_count,
            "smoke_test": smoke_test,
            "engineering_validation_passed": engineering_passed,
            "validation_verdict": validation_verdict,
            "limitations": limitations,
        }

    @staticmethod
    def _data_quality(
        data: Mapping[str, Any],
        grouped: Mapping[str, Mapping[str, Sequence[float]]],
        reports: Mapping[str, MetricAnalysis],
        warnings: Sequence[str],
    ) -> dict[str, Any]:
        sample_counts = {
            metric: {
                "baseline": len(values.get("baseline", [])),
                "proposed": len(values.get("proposed", [])),
            }
            for metric, values in grouped.items()
        }
        insufficient = any(
            counts["baseline"] < MIN_SAMPLE_SIZE
            or counts["proposed"] < MIN_SAMPLE_SIZE
            for counts in sample_counts.values()
        )
        metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
        source_text = json.dumps(metadata, ensure_ascii=False).lower()
        evidence_type = str(metadata.get("evidence_type") or "").lower()
        real_execution = bool(metadata.get("real_execution_enabled") or evidence_type.startswith("real_"))
        synthetic = any(token in source_text for token in ("synthetic", "fallback", "surrogate"))
        zero_variance = [name for name, item in reports.items() if item.zero_variance]
        high_variance = [name for name, item in reports.items() if item.high_variance]
        if insufficient and real_execution:
            evidence_grade = "real_smoke_test"
        elif insufficient:
            evidence_grade = "insufficient"
        elif synthetic:
            evidence_grade = "demonstration_only"
        elif min(
            min(counts["baseline"], counts["proposed"])
            for counts in sample_counts.values()
        ) < 5:
            evidence_grade = "preliminary"
        else:
            evidence_grade = "experimental"
        return {
            "sample_counts": sample_counts,
            "minimum_required_per_condition": MIN_SAMPLE_SIZE,
            "insufficient_sample": insufficient,
            "synthetic_or_fallback_data": synthetic,
            "real_execution": real_execution,
            "evidence_type": metadata.get("evidence_type"),
            "case_source": metadata.get("case_source"),
            "zero_variance_metrics": zero_variance,
            "high_variance_metrics": high_variance,
            "collection_warnings": list(warnings),
            "evidence_grade": evidence_grade,
            "stagnation_detected": _detect_stagnation(data.get("iteration_history")),
        }

    @staticmethod
    def _recommendations(
        data: Mapping[str, Any],
        primary_metric: str,
        verdict: str,
        reports: Mapping[str, MetricAnalysis],
        power_analysis: Mapping[str, Any],
        quality: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        if quality.get("evidence_grade") == "real_smoke_test":
            dataset_text = (
                "Keep the verified real simulator chain, then expand to at least three to five operating "
                "scenarios per condition for statistical claims."
            )
            dataset_priority = "high"
        elif quality.get("synthetic_or_fallback_data"):
            dataset_text = "Replace fallback data with a traceable real dataset and retain the same splits/scenarios."
            dataset_priority = "high"
        elif quality.get("insufficient_sample"):
            dataset_text = "Increase each condition to at least five independent repetitions or operating scenarios."
            dataset_priority = "high"
        else:
            dataset_text = "Expand operating scenarios and preserve raw per-repeat observations for robustness analysis."
            dataset_priority = "medium"
        recommendations.append(
            {
                "layer": "dataset_optimization",
                "priority": dataset_priority,
                "recommendation": dataset_text,
                "expected_impact": "Improves external validity, uncertainty estimation, and reproducibility.",
            }
        )

        parameter_text = "Run a targeted parameter sweep around the best configuration and add negative controls."
        safety = power_analysis.get("safety_gate", {})
        if safety.get("passed") is False:
            parameter_text = (
                "Tighten the violated power-system constraints, refine the operating-point grid, "
                "and rerun boundary/fault scenarios before optimizing the primary metric."
            )
        elif reports[primary_metric].high_variance:
            parameter_text = "Reduce uncontrolled variability, fix random seeds, and increase repetitions before tuning means."
        recommendations.append(
            {
                "layer": "experiment_parameter_adjustment",
                "priority": "high" if verdict != "supported" else "medium",
                "recommendation": parameter_text,
                "expected_impact": "Separates genuine mechanism effects from numerical or scenario sensitivity.",
            }
        )

        if verdict == "supported":
            hypothesis_text = (
                "Retain the hypothesis but narrow its claim to the tested dataset, parameter range, and confidence interval."
            )
            priority = "low"
        elif verdict == "refuted":
            hypothesis_text = (
                "Revise the causal mechanism or claimed improvement direction; document the failed hypothesis as negative knowledge."
            )
            priority = "high"
        else:
            hypothesis_text = (
                "Suspend support/refutation language and rewrite the hypothesis around the unresolved source of uncertainty."
            )
            priority = "high"
        recommendations.append(
            {
                "layer": "hypothesis_revision",
                "priority": priority,
                "recommendation": hypothesis_text,
                "expected_impact": "Prevents claims from exceeding the experimental evidence.",
            }
        )
        return recommendations

    @staticmethod
    def _method_memory(
        data: Mapping[str, Any],
        primary_metric: str,
        verdict: str,
        primary: MetricAnalysis,
        quality: Mapping[str, Any],
    ) -> dict[str, Any]:
        successful_patterns: list[str] = []
        failed_patterns: list[str] = []
        if verdict == "supported":
            successful_patterns.append(
                f"{data.get('simulation_type')} comparison improved {primary_metric} with {primary.significance}."
            )
        else:
            failed_patterns.append(
                f"{data.get('simulation_type')} comparison did not establish {primary_metric} improvement."
            )
        if quality.get("evidence_grade") == "real_smoke_test":
            successful_patterns.append("Real simulator execution chain produced a valid Module 7 result contract.")
            failed_patterns.append("Real smoke test has too few repetitions for statistical support.")
        if quality.get("synthetic_or_fallback_data"):
            failed_patterns.append("Evidence relied on synthetic/fallback data and cannot support broad claims.")
        if quality.get("high_variance_metrics"):
            failed_patterns.append("High-variance metrics require more scenarios or better controls.")
        return {
            "experiment_id": str(data.get("experiment_id") or ""),
            "method_name": str(data.get("engine") or data.get("simulation_type") or "unknown"),
            "application_domain": str(data.get("branch") or "general"),
            "outcome": verdict,
            "primary_metric": primary_metric,
            "successful_patterns": successful_patterns,
            "failed_patterns": failed_patterns,
            "lessons_learned": primary.verdict_reason,
            "last_updated_epoch": time.time(),
        }

    def _resolve_output_dir(
        self, output_dir: str | Path | None, experiment_id: str
    ) -> Path:
        if output_dir is None:
            return self.results_root / "module8" / experiment_id
        path = Path(output_dir)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    @staticmethod
    def _markdown_report(report: MingBianReport) -> str:
        lines = [
            "# MingBian Statistical Analysis Report",
            "",
            f"- Experiment: `{report.experiment_id}`",
            f"- Hypothesis: `{report.hypothesis_id}`",
            f"- Branch / engine: `{report.branch}` / `{report.engine}`",
            f"- Primary metric: `{report.primary_metric}`",
            f"- Verdict: **{report.hypothesis_verdict}**",
            f"- Reason: {report.verdict_reason}",
            "",
            "## Statistical Results",
            "",
            "| Metric | Baseline mean [95% CI] | Proposed mean [95% CI] | p-value | Cohen's d | Verdict |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for name, metric in report.metrics.items():
            base = metric["baseline"]
            prop = metric["proposed"]
            d_value = metric["cohens_d"]
            d_text = "NA" if d_value is None else f"{d_value:.4f}"
            lines.append(
                f"| {name} | {base['mean']:.6g} [{base['ci95_low']:.6g}, {base['ci95_high']:.6g}] "
                f"| {prop['mean']:.6g} [{prop['ci95_low']:.6g}, {prop['ci95_high']:.6g}] "
                f"| {metric['p_value']:.4g} {metric['significance']} | {d_text} | {metric['verdict']} |"
            )
        lines.extend(["", "## Validation Analysis", "", "```json"])
        lines.append(json.dumps(report.validation_analysis, ensure_ascii=False, indent=2))
        lines.extend(["```", "", "## Data Quality", "", "```json"])
        lines.append(json.dumps(report.data_quality, ensure_ascii=False, indent=2))
        lines.extend(["```", "", "## Iteration Recommendations", ""])
        for item in report.iteration_recommendations:
            lines.append(
                f"- **{item['layer']} ({item['priority']})**: {item['recommendation']} "
                f"Expected impact: {item['expected_impact']}"
            )
        lines.extend(
            [
                "",
                "## PaperWriter Constraint",
                "",
                "All numeric values in this report are programmatically verified. Module 9 may quote them but must not recalculate or modify them.",
            ]
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_figure(report_path: Path, figure_path: Path) -> str:
        try:
            _render_report_figure(report_path, figure_path)
            return "success"
        except Exception as exc:
            return f"figure_generation_failed:{type(exc).__name__}:{exc}"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _critical_t(probability: float, degrees_of_freedom: float) -> float:
    try:
        from scipy.stats import t as t_distribution

        return float(t_distribution.ppf(probability, max(degrees_of_freedom, 1.0)))
    except ImportError:
        return 1.96


def _group_stats(values: Sequence[float], alpha: float) -> GroupStats:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        raise ResultValidationError("Cannot compute statistics for an empty group")
    n = len(clean)
    mean = statistics.fmean(clean)
    std = statistics.stdev(clean) if n > 1 else 0.0
    variance = std * std
    standard_error = std / math.sqrt(n) if n > 0 else 0.0
    critical = _critical_t(1.0 - alpha / 2.0, n - 1)
    half_width = critical * standard_error
    return GroupStats(
        n=n,
        mean=mean,
        std=std,
        variance=variance,
        standard_error=standard_error,
        ci95_low=mean - half_width,
        ci95_high=mean + half_width,
        minimum=min(clean),
        maximum=max(clean),
    )


def _comparison_values(
    baseline: Sequence[float], proposed: Sequence[float], direction: str
) -> tuple[list[float], list[float], str]:
    if direction.startswith("target_"):
        try:
            target = float(direction.split("_", 1)[1])
        except ValueError:
            target = 0.0
        return (
            [abs(value - target) for value in baseline],
            [abs(value - target) for value in proposed],
            f"absolute deviation from target {target}",
        )
    return list(baseline), list(proposed), "raw metric values"


def _welch_test(
    baseline: Sequence[float], proposed: Sequence[float], alpha: float
) -> tuple[float, float, float, tuple[float, float]]:
    n1, n2 = len(baseline), len(proposed)
    mean1, mean2 = statistics.fmean(baseline), statistics.fmean(proposed)
    var1 = statistics.variance(baseline) if n1 > 1 else 0.0
    var2 = statistics.variance(proposed) if n2 > 1 else 0.0
    se2 = var1 / n1 + var2 / n2
    delta = mean2 - mean1
    if se2 <= 0:
        if delta == 0:
            return 0.0, float(max(n1 + n2 - 2, 1)), 1.0, (0.0, 0.0)
        return math.copysign(1e12, delta), float(max(n1 + n2 - 2, 1)), 0.0, (delta, delta)
    t_stat = delta / math.sqrt(se2)
    denominator = 0.0
    if n1 > 1:
        denominator += (var1 / n1) ** 2 / (n1 - 1)
    if n2 > 1:
        denominator += (var2 / n2) ** 2 / (n2 - 1)
    df = se2 * se2 / denominator if denominator > 0 else float(max(n1 + n2 - 2, 1))
    try:
        from scipy.stats import t as t_distribution

        p_value = float(2.0 * t_distribution.sf(abs(t_stat), df))
    except ImportError:
        p_value = math.erfc(abs(t_stat) / math.sqrt(2.0))
    critical = _critical_t(1.0 - alpha / 2.0, df)
    half_width = critical * math.sqrt(se2)
    return t_stat, df, min(max(p_value, 0.0), 1.0), (delta - half_width, delta + half_width)


def _pooled_std(baseline: Sequence[float], proposed: Sequence[float]) -> float:
    n1, n2 = len(baseline), len(proposed)
    if n1 + n2 <= 2:
        return 0.0
    var1 = statistics.variance(baseline) if n1 > 1 else 0.0
    var2 = statistics.variance(proposed) if n2 > 1 else 0.0
    pooled_variance = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    return math.sqrt(max(pooled_variance, 0.0))


def _cohens_d(baseline: Sequence[float], proposed: Sequence[float]) -> float | None:
    pooled = _pooled_std(baseline, proposed)
    delta = statistics.fmean(proposed) - statistics.fmean(baseline)
    if pooled == 0.0:
        return 0.0 if delta == 0.0 else None
    return delta / pooled


def _favorable_delta(baseline: float, proposed: float, direction: str) -> float:
    if direction == "minimize" or direction.startswith("target_"):
        return baseline - proposed
    return proposed - baseline


def _relative_improvement(
    baseline: float, proposed: float, direction: str
) -> float | None:
    if abs(baseline) < 1e-12:
        return None
    return _favorable_delta(baseline, proposed, direction) / abs(baseline) * 100.0


def _effect_magnitude(value: float | None) -> str:
    if value is None:
        return "undefined_zero_variance"
    absolute = abs(value)
    if absolute < 0.2:
        return "negligible"
    if absolute < 0.5:
        return "small"
    if absolute < 0.8:
        return "medium"
    return "large"


def _significance_stars(p_value: float) -> str:
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def _metric_improvement(report: MetricAnalysis | None) -> float | None:
    return report.relative_improvement_percent if report else None


def _detect_stagnation(history: Any) -> bool:
    if not isinstance(history, list) or len(history) < 3:
        return False
    recent = history[-3:]
    improvements: list[float] = []
    for item in recent:
        if isinstance(item, Mapping):
            value = item.get("improvement")
        else:
            value = item
        number = _finite_number(value)
        if number is not None:
            improvements.append(number)
    return len(improvements) == 3 and all(value <= 0 for value in improvements)


def _render_report_figure(report_path: Path, output_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = json.loads(report_path.read_text(encoding="utf-8"))
    metrics = list(report.get("metrics", {}).items())
    if not metrics:
        raise ValueError("No metric analyses available for plotting")
    columns = min(3, len(metrics))
    rows = math.ceil(len(metrics) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(4.4 * columns, 3.8 * rows),
        squeeze=False,
    )
    for axis, (name, item) in zip(axes.flat, metrics):
        baseline = item["baseline"]
        proposed = item["proposed"]
        means = [baseline["mean"], proposed["mean"]]
        errors = [
            max(0.0, baseline["ci95_high"] - baseline["mean"]),
            max(0.0, proposed["ci95_high"] - proposed["mean"]),
        ]
        axis.bar(
            [0, 1],
            means,
            yerr=errors,
            capsize=5,
            color=["#6b7280", "#0f766e"],
        )
        axis.set_xticks([0, 1], ["Baseline", "Proposed"])
        axis.set_title(name)
        axis.text(
            0.5,
            0.95,
            item.get("significance", "ns"),
            ha="center",
            va="top",
            fontsize=12,
            fontweight="bold",
            transform=axis.transAxes,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.7, "pad": 1.0},
        )
        axis.grid(axis="y", alpha=0.2)
    for axis in list(axes.flat)[len(metrics) :]:
        axis.axis("off")
    figure.suptitle(
        f"MingBian analysis: {report.get('hypothesis_verdict', 'unknown')}"
    )
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)


_PLOT_SCRIPT = r'''"""Auto-generated Module 8 significance plot."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def render(report_path, output_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report_path = Path(report_path)
    output_path = Path(output_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    metrics = list(report.get("metrics", {}).items())
    if not metrics:
        raise ValueError("No metric analyses available for plotting")
    columns = min(3, len(metrics))
    rows = math.ceil(len(metrics) / columns)
    fig, axes = plt.subplots(rows, columns, figsize=(4.4 * columns, 3.8 * rows), squeeze=False)
    for axis, (name, item) in zip(axes.flat, metrics):
        baseline = item["baseline"]
        proposed = item["proposed"]
        means = [baseline["mean"], proposed["mean"]]
        errors = [
            max(0.0, baseline["ci95_high"] - baseline["mean"]),
            max(0.0, proposed["ci95_high"] - proposed["mean"]),
        ]
        axis.bar([0, 1], means, yerr=errors, capsize=5, color=["#6b7280", "#0f766e"])
        axis.set_xticks([0, 1], ["Baseline", "Proposed"])
        axis.set_title(name)
        axis.text(0.5, 0.95, item.get("significance", "ns"), ha="center", va="top", fontsize=12, fontweight="bold", transform=axis.transAxes, bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.7, "pad": 1.0})
        axis.grid(axis="y", alpha=0.2)
    for axis in list(axes.flat)[len(metrics):]:
        axis.axis("off")
    fig.suptitle(f"MingBian analysis: {report.get('hypothesis_verdict', 'unknown')}")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", default="analysis_report.json")
    parser.add_argument("--output", default="figures/mingbian_comparison.png")
    args = parser.parse_args()
    render(args.report, args.output)


if __name__ == "__main__":
    main()
'''


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Module 8 MingBian result analysis")
    parser.add_argument("result", help="Module 7 result JSON")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--primary-metric", default=None)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    return parser


def main() -> int:
    args = _build_cli().parse_args()
    try:
        report = MingBian(alpha=args.alpha).analyze_file(
            args.result,
            output_dir=args.output_dir,
            primary_metric=args.primary_metric,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "module8_failed", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
