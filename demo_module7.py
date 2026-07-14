"""模块7一键演示。

默认运行不依赖电力仿真库的通用实验，以验证 GeWu -> CodeEngineer ->
Sandbox -> 标准 result.json 全链路。电力 JSON 可配合 ``--dry-run`` 仅验证
路由和代码生成，待后续电力库联调时去掉该参数。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.code_engineer import CodeEngineer


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Module 7 end-to-end demo")
    parser.add_argument(
        "--plan",
        default="examples/module7_general_plan.json",
        help="GeWu plan JSON",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backend", choices=("auto", "docker", "local"), default=None)
    qwen = parser.add_mutually_exclusive_group()
    qwen.add_argument("--qwen", action="store_true", help="Use Qwen Bailian instead of the audited template")
    qwen.add_argument("--no-qwen", action="store_true", help="Use deterministic audited templates")
    args = parser.parse_args()

    if args.qwen:
        qwen_override = True
    elif args.no_qwen:
        qwen_override = False
    else:
        qwen_override = False

    engineer = CodeEngineer(project_root=Path(__file__).resolve().parent)
    report = engineer.run_plan(
        args.plan,
        dry_run=args.dry_run,
        backend_override=args.backend,
        qwen_override=qwen_override,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.status in {"success", "code_ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
