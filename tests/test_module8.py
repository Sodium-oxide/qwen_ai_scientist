from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path

from agents.mingbian import MingBian


class Module8Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def _output(self, label: str) -> Path:
        path = self.root / "results" / "module8_tests" / f"{label}_{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _load(self, filename: str) -> dict:
        return json.loads(
            (self.root / "examples" / filename).read_text(encoding="utf-8")
        )

    def test_general_supported_and_artifacts(self) -> None:
        analyzer = MingBian(project_root=self.root)
        output = self._output("general")
        report = analyzer.analyze_file(
            self.root / "examples" / "module7_result_example.json",
            output_dir=output,
        )
        self.assertEqual(report.hypothesis_verdict, "supported")
        self.assertEqual(report.primary_metric, "primary_metric")
        self.assertLess(report.metrics["primary_metric"]["p_value"], 0.05)
        self.assertIn(report.metrics["primary_metric"]["significance"], {"*", "**", "***"})
        for name in (
            "analysis_report.json",
            "mingbian_report.md",
            "plot_mingbian_results.py",
            "method_memory_update.json",
            "figures/mingbian_comparison.png",
        ):
            self.assertTrue((output / name).is_file(), name)

    def test_opf_power_metrics_and_target_direction(self) -> None:
        report = MingBian(project_root=self.root).analyze_data(
            self._load("module8_power_opf_result.json"),
            output_dir=self._output("opf"),
        )
        self.assertEqual(report.hypothesis_verdict, "supported")
        self.assertTrue(report.power_analysis["safety_gate"]["passed"])
        self.assertGreater(report.power_analysis["network_loss_reduction_percent"], 5.0)
        self.assertEqual(report.metrics["voltage_min_pu"]["verdict"], "supported")

    def test_tds_power_metrics(self) -> None:
        report = MingBian(project_root=self.root).analyze_data(
            self._load("module8_power_tds_result.json"),
            output_dir=self._output("tds"),
        )
        self.assertEqual(report.hypothesis_verdict, "supported")
        self.assertGreater(report.power_analysis["proposed_cct_mean_seconds"], 0.2)
        self.assertEqual(report.power_analysis["proposed_stability_rate"], 1.0)
        self.assertTrue(report.power_analysis["safety_gate"]["passed"])

    def test_real_tds_smoke_test_is_engineering_validated_but_inconclusive(self) -> None:
        report = MingBian(project_root=self.root).analyze_data(
            self._real_tds_smoke_result(stable=True, converged=True),
            output_dir=self._output("tds_smoke"),
        )
        self.assertEqual(report.hypothesis_verdict, "inconclusive")
        self.assertEqual(report.data_quality["evidence_grade"], "real_smoke_test")
        self.assertTrue(report.validation_analysis["engineering_validation_passed"])
        self.assertEqual(
            report.validation_analysis["validation_verdict"],
            "engineering_validated_statistically_insufficient",
        )
        self.assertTrue(report.power_analysis["safety_gate"]["passed"])

    def test_cosim_power_metrics(self) -> None:
        report = MingBian(project_root=self.root).analyze_data(
            self._load("module8_power_cosim_result.json"),
            output_dir=self._output("cosim"),
        )
        self.assertEqual(report.hypothesis_verdict, "supported")
        self.assertGreater(report.power_analysis["dispatch_cost_improvement_percent"], 0.0)
        self.assertGreater(
            report.power_analysis["proposed_stability_rate"],
            report.power_analysis["baseline_stability_rate"],
        )

    def test_real_cosim_handoff_without_tds_convergence_is_flagged(self) -> None:
        report = MingBian(project_root=self.root).analyze_data(
            self._real_cosim_smoke_result(),
            output_dir=self._output("cosim_smoke"),
        )
        self.assertEqual(report.hypothesis_verdict, "inconclusive")
        self.assertEqual(report.data_quality["evidence_grade"], "real_smoke_test")
        self.assertEqual(report.power_analysis["dispatch_convergence_rate"], 1.0)
        self.assertEqual(report.power_analysis["dynamic_handoff_rate"], 1.0)
        self.assertEqual(report.power_analysis["tds_convergence_rate"], 0.0)
        self.assertFalse(report.power_analysis["safety_gate"]["passed"])
        self.assertFalse(report.validation_analysis["engineering_validation_passed"])
        self.assertIn(
            "Dynamic handoff occurred",
            " ".join(report.validation_analysis["limitations"]),
        )

    def test_safety_gate_overrides_primary_support(self) -> None:
        data = self._load("module8_power_opf_result.json")
        for observation in data["observations"]:
            if observation["condition"] == "proposed":
                observation["metrics"]["voltage_violation_count"] = 1
        report = MingBian(project_root=self.root).analyze_data(
            data,
            output_dir=self._output("safety_gate"),
        )
        self.assertEqual(report.metrics["network_loss_mw"]["verdict"], "supported")
        self.assertEqual(report.hypothesis_verdict, "refuted")
        self.assertFalse(report.power_analysis["safety_gate"]["passed"])

    def test_refuted_when_direction_is_worse(self) -> None:
        data = self._simple_result(
            baseline=[1.0, 1.1, 0.9, 1.05, 0.95],
            proposed=[0.5, 0.6, 0.4, 0.55, 0.45],
        )
        report = MingBian(project_root=self.root).analyze_data(
            data,
            output_dir=self._output("refuted"),
        )
        self.assertEqual(report.hypothesis_verdict, "refuted")

    def test_inconclusive_when_sample_is_too_small(self) -> None:
        data = self._simple_result(baseline=[1.0, 1.1], proposed=[1.5, 1.6])
        report = MingBian(project_root=self.root).analyze_data(
            data,
            output_dir=self._output("inconclusive"),
        )
        self.assertEqual(report.hypothesis_verdict, "inconclusive")
        self.assertTrue(report.data_quality["insufficient_sample"])

    def test_inconclusive_when_variance_is_too_high(self) -> None:
        data = self._simple_result(
            baseline=[-100.0, 100.0, -90.0, 90.0, 0.0],
            proposed=[-99.0, 101.0, -89.0, 91.0, 1.0],
        )
        report = MingBian(project_root=self.root).analyze_data(
            data,
            output_dir=self._output("high_variance"),
        )
        self.assertEqual(report.hypothesis_verdict, "inconclusive")
        self.assertIn("score", report.data_quality["high_variance_metrics"])

    def test_detects_three_round_stagnation(self) -> None:
        data = self._simple_result(
            baseline=[1.0, 1.1, 0.9, 1.05, 0.95],
            proposed=[1.5, 1.6, 1.4, 1.55, 1.45],
        )
        data["iteration_history"] = [
            {"improvement": 0.0},
            {"improvement": -0.01},
            {"improvement": 0.0},
        ]
        report = MingBian(project_root=self.root).analyze_data(
            data,
            output_dir=self._output("stagnation"),
        )
        self.assertTrue(report.data_quality["stagnation_detected"])

    @staticmethod
    def _simple_result(baseline: list[float], proposed: list[float]) -> dict:
        observations = []
        for index, value in enumerate(baseline):
            observations.append(
                {"condition": "baseline", "repeat": index, "metrics": {"score": value}}
            )
        for index, value in enumerate(proposed):
            observations.append(
                {"condition": "proposed", "repeat": index, "metrics": {"score": value}}
            )
        return {
            "schema_version": "1.0",
            "experiment_id": f"simple_{uuid.uuid4().hex}",
            "hypothesis_id": "hyp_simple",
            "hypothesis": "The proposed condition improves score.",
            "branch": "general",
            "simulation_type": "general",
            "engine": "python-scientific",
            "status": "success",
            "primary_metric": "score",
            "metric_directions": {"score": "maximize"},
            "observations": observations,
            "artifacts": [],
        }

    @staticmethod
    def _real_tds_smoke_result(*, stable: bool, converged: bool) -> dict:
        return {
            "schema_version": "1.0",
            "experiment_id": f"real_tds_{uuid.uuid4().hex}",
            "hypothesis_id": "hyp_real_tds",
            "hypothesis": "A real ANDES TDS smoke test executes through Module 7.",
            "branch": "power",
            "simulation_type": "tds",
            "engine": "andes",
            "status": "success",
            "primary_metric": "cct_seconds",
            "metadata": {
                "case_source": "provided_andes_case",
                "evidence_type": "real_andes_tds",
                "real_execution_enabled": True,
            },
            "metric_directions": {
                "cct_seconds": "maximize",
                "max_rotor_angle_deg": "minimize",
                "voltage_nadir_pu": "maximize",
            },
            "observations": [
                {
                    "condition": "baseline",
                    "repeat": 0,
                    "metrics": {
                        "stable": stable,
                        "tds_converged": converged,
                        "max_rotor_angle_deg": 0.0,
                        "voltage_nadir_pu": 0.984,
                        "cct_seconds": 0.01,
                    },
                },
                {
                    "condition": "proposed",
                    "repeat": 0,
                    "metrics": {
                        "stable": stable,
                        "tds_converged": converged,
                        "max_rotor_angle_deg": 0.0,
                        "voltage_nadir_pu": 0.984,
                        "cct_seconds": 0.01,
                    },
                },
            ],
            "artifacts": [],
        }

    @staticmethod
    def _real_cosim_smoke_result() -> dict:
        observations = []
        for condition in ("baseline", "proposed"):
            observations.append(
                {
                    "condition": condition,
                    "repeat": 0,
                    "metrics": {
                        "dispatch_cost": 9.5359,
                        "network_loss_mw": None,
                        "voltage_nadir_pu": 0.0,
                        "stable": False,
                        "dispatch_converged": True,
                        "dynamic_handoff": True,
                        "tds_converged": False,
                    },
                }
            )
        return {
            "schema_version": "1.0",
            "experiment_id": f"real_cosim_{uuid.uuid4().hex}",
            "hypothesis_id": "hyp_real_cosim",
            "hypothesis": "A real AMS+ANDES smoke test executes through Module 7.",
            "branch": "power",
            "simulation_type": "cosim",
            "engine": "ltbams",
            "status": "success",
            "primary_metric": "dispatch_cost",
            "metadata": {
                "case_source": "provided_ams_andes_cases",
                "evidence_type": "real_ams_andes_cosim",
                "real_execution_enabled": True,
            },
            "metric_directions": {
                "dispatch_cost": "minimize",
                "voltage_nadir_pu": "maximize",
                "stable": "maximize",
                "dispatch_converged": "maximize",
                "dynamic_handoff": "maximize",
                "tds_converged": "maximize",
            },
            "observations": observations,
            "artifacts": [],
        }


if __name__ == "__main__":
    unittest.main()
