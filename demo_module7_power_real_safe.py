"""Safe one-command validation for Module 7 power simulators.

Default behavior is intentionally conservative for open-source users:
only the lightweight pandapower OPF smoke test is executed. ANDES and
AMS+ANDES checks are opt-in because they can take about a minute each on
Windows machines without Docker.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

from agents.code_engineer import CodeEngineer, ExperimentPlan


ROOT = Path(__file__).resolve().parent

CHECKS = {
    "opf": {
        "label": "pandapower OPF",
        "plan": ROOT / "examples" / "module7_power_opf_plan.json",
        "default": True,
    },
    "tds": {
        "label": "ANDES TDS short smoke test",
        "plan": ROOT / "examples" / "module7_power_tds_real_safe_plan.json",
        "default": False,
    },
    "cosim": {
        "label": "AMS+ANDES co-simulation short smoke test",
        "plan": ROOT / "examples" / "module7_power_cosim_real_safe_plan.json",
        "default": False,
    },
    "tds5s": {
        "label": "ANDES TDS five-second integration",
        "plan": ROOT / "examples" / "module7_power_tds_5s_stable_plan.json",
        "default": False,
    },
}


def dependency_snapshot() -> dict[str, bool]:
    return {
        name: importlib.util.find_spec(name) is not None
        for name in ("pandapower", "andes", "ams", "ltbams", "scipy")
    }


def select_checks(args: argparse.Namespace) -> list[str]:
    if args.all:
        return ["opf", "tds", "cosim", "tds5s"]
    selected: list[str] = []
    if not args.no_opf:
        selected.append("opf")
    if args.tds:
        selected.append("tds")
    if args.cosim:
        selected.append("cosim")
    if args.tds5s:
        selected.append("tds5s")
    return selected


def result_preview(result_path: str | None) -> dict[str, Any]:
    if not result_path:
        return {}
    path = Path(result_path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "case_source": data.get("metadata", {}).get("case_source"),
        "evidence_type": data.get("metadata", {}).get("evidence_type"),
        "simulation_type": data.get("simulation_type"),
        "engine": data.get("engine"),
        "observation_count": len(data.get("observations", [])),
    }


def run_check(
    engineer: CodeEngineer,
    key: str,
    backend: str,
    *,
    docker_image: str | None = None,
) -> dict[str, Any]:
    item = CHECKS[key]
    started = time.time()
    plan = plan_with_docker_image(
        ExperimentPlan.from_json_file(item["plan"]),
        docker_image if backend == "docker" else None,
    )
    report = engineer.run_plan(
        plan,
        backend_override=backend,
        qwen_override=False,
    )
    return {
        "check": key,
        "label": item["label"],
        "plan": str(item["plan"]),
        "status": report.status,
        "elapsed_seconds": round(time.time() - started, 3),
        "result_path": report.result_path,
        "failure_reason": report.failure_reason,
        "simulator_preflight": report.simulator_preflight,
        "result_preview": result_preview(report.result_path),
    }


def plan_with_docker_image(plan: ExperimentPlan, image: str | None) -> ExperimentPlan:
    if not image:
        return plan
    plan.execution["docker_image"] = image
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run safe Module 7 power-simulator validation checks."
    )
    parser.add_argument("--backend", choices=("auto", "docker", "local"), default="local")
    parser.add_argument("--docker-image", default="qwen-ai-scientist/power-experiment:latest")
    parser.add_argument("--output", default="results/module7_power_real_safe_summary.json")
    parser.add_argument("--all", action="store_true", help="Run OPF, TDS, COSIM and 5s TDS checks.")
    parser.add_argument("--tds", action="store_true", help="Also run the short ANDES TDS smoke test.")
    parser.add_argument("--cosim", action="store_true", help="Also run the short AMS+ANDES smoke test.")
    parser.add_argument("--tds5s", action="store_true", help="Also run the five-second ANDES TDS test.")
    parser.add_argument("--no-opf", action="store_true", help="Skip the default pandapower OPF check.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after a failed check.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    selected = select_checks(args)
    if not selected:
        raise SystemExit("No checks selected.")

    engineer = CodeEngineer(project_root=ROOT)
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "module": "module7_power_real_safe_validation",
        "backend": args.backend,
        "selected_checks": selected,
        "dependency_snapshot": dependency_snapshot(),
        "started_at": time.time(),
        "checks": [],
    }

    exit_code = 0
    for key in selected:
        result = run_check(
            engineer,
            key,
            args.backend,
            docker_image=args.docker_image,
        )
        summary["checks"].append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] not in {"success", "code_ready"}:
            exit_code = 1
            if not args.keep_going:
                break

    summary["finished_at"] = time.time()
    summary["overall_status"] = "success" if exit_code == 0 else "failed"
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY_JSON: {output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
