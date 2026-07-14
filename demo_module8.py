"""模块8一键演示，不启动任何仿真软件。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.mingbian import MingBian


def main() -> int:
    root = Path(__file__).resolve().parent
    default_result = root / "results" / "exp_general_result.json"
    if not default_result.exists():
        default_result = root / "examples" / "module7_result_example.json"

    parser = argparse.ArgumentParser(description="Run Module 8 MingBian analysis")
    parser.add_argument("--input", default=str(default_result))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--primary-metric", default=None)
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    report = MingBian(project_root=root, alpha=args.alpha).analyze_file(
        args.input,
        output_dir=args.output_dir,
        primary_metric=args.primary_metric,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
