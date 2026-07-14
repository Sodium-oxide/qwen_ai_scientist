from __future__ import annotations

import json
import unittest
import uuid
from argparse import Namespace
from pathlib import Path

from agents.code_engineer import CodeEngineer, ExperimentPlan, MAX_REPAIR_ROUNDS
from demo_module7_power_real_safe import (
    dependency_snapshot,
    plan_with_docker_image,
    select_checks,
)


class Module7Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def _load(self, name: str) -> ExperimentPlan:
        return ExperimentPlan.from_json_file(self.root / "examples" / name)

    def _results_dir(self) -> Path:
        path = self.root / "results" / "module7_tests" / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=False)
        return path

    def test_routes_all_power_engines(self) -> None:
        cases = {
            "module7_power_opf_plan.json": ("opf", "pandapower"),
            "module7_power_tds_plan.json": ("tds", "andes"),
            "module7_power_cosim_plan.json": ("cosim", "ltbams"),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                decision = CodeEngineer.classify(self._load(name))
                self.assertEqual((decision.task_type, decision.engine), expected)

    def test_power_templates_pass_static_gate(self) -> None:
        engineer = CodeEngineer(project_root=self.root, results_root=self._results_dir())
        for name in (
            "module7_power_opf_plan.json",
            "module7_power_tds_plan.json",
            "module7_power_cosim_plan.json",
        ):
            plan = self._load(name)
            decision = engineer.classify(plan)
            code = engineer._template_code(plan, decision, "exp_power_result.json")
            self.assertEqual(
                engineer.validate_generated_code(code, "exp_power_result.json"),
                [],
            )
            compile(code, name, "exec")

    def test_power_local_timeout_is_capped_by_default(self) -> None:
        engineer = CodeEngineer(project_root=self.root, results_root=self._results_dir())
        plan = self._load("module7_power_tds_plan.json")
        decision = engineer.classify(plan)
        limits = engineer._sandbox_limits(plan, None, decision)
        self.assertEqual(limits.timeout_seconds, 120)

    def test_tds_and_cosim_real_paths_require_explicit_safety_flags(self) -> None:
        engineer = CodeEngineer(project_root=self.root, results_root=self._results_dir())
        tds = engineer._template_code(
            self._load("module7_power_tds_plan.json"),
            engineer.classify(self._load("module7_power_tds_plan.json")),
            "exp_power_result.json",
        )
        cosim = engineer._template_code(
            self._load("module7_power_cosim_plan.json"),
            engineer.classify(self._load("module7_power_cosim_plan.json")),
            "exp_power_result.json",
        )
        self.assertIn("allow_real_tds", tds)
        self.assertIn("provided_andes_case_blocked_by_safety_gate", tds)
        self.assertIn("allow_real_cosim", cosim)
        self.assertIn("provided_ams_andes_cases_blocked_by_safety_gate", cosim)
        self.assertIn("import importlib.util", cosim)

    def test_power_dry_run_reports_simulator_preflight(self) -> None:
        engineer = CodeEngineer(project_root=self.root, results_root=self._results_dir())
        report = engineer.run_plan(
            self._load("module7_power_opf_plan.json"),
            dry_run=True,
            backend_override="local",
            qwen_override=False,
        )
        self.assertEqual(report.status, "code_ready")
        self.assertEqual(report.simulator_preflight["engine"], "pandapower")
        self.assertIn("pandapower", report.simulator_preflight["python_packages"])

    def test_generic_local_end_to_end(self) -> None:
        engineer = CodeEngineer(project_root=self.root, results_root=self._results_dir())
        report = engineer.run_plan(
            self._load("module7_general_plan.json"),
            backend_override="local",
            qwen_override=False,
        )
        self.assertEqual(report.status, "success", report.failure_reason)
        self.assertIsNotNone(report.result_path)
        data = json.loads(Path(report.result_path).read_text(encoding="utf-8"))
        self.assertEqual(data["branch"], "general")
        self.assertGreaterEqual(len(data["observations"]), 6)

    def test_repair_limit_is_fixed(self) -> None:
        self.assertEqual(MAX_REPAIR_ROUNDS, 5)

    def test_power_real_safe_demo_defaults_to_opf_only(self) -> None:
        args = Namespace(
            all=False,
            no_opf=False,
            tds=False,
            cosim=False,
            tds5s=False,
        )
        self.assertEqual(select_checks(args), ["opf"])

    def test_power_real_safe_demo_all_order_is_stable(self) -> None:
        args = Namespace(
            all=True,
            no_opf=True,
            tds=False,
            cosim=False,
            tds5s=False,
        )
        self.assertEqual(select_checks(args), ["opf", "tds", "cosim", "tds5s"])

    def test_power_dependency_snapshot_has_expected_keys(self) -> None:
        snapshot = dependency_snapshot()
        for name in ("pandapower", "andes", "ams", "ltbams", "scipy"):
            self.assertIn(name, snapshot)
            self.assertIsInstance(snapshot[name], bool)

    def test_power_real_safe_demo_can_override_docker_image(self) -> None:
        plan = self._load("module7_power_opf_plan.json")
        updated = plan_with_docker_image(plan, "example/power:dev")
        self.assertEqual(updated.execution["docker_image"], "example/power:dev")


if __name__ == "__main__":
    unittest.main()
