# 模块7对接契约

`agents/code_engineer.py` 接收模块6 GeWu 的 JSON，输出实验代码、沙箱日志和
模块8可读取的结果 JSON。当前契约版本为 `1.0`。

## 输入

最低必填字段：

- `experiment_id`：本轮实验唯一标识。
- `hypothesis.id`、`hypothesis.statement`：假设标识与可证伪陈述。
- `domain`：学科或业务领域。
- `task.type`：建议使用 `steady_state_opf`、`transient_tds`、
  `dispatch_dynamic_cosim` 或通用实验类型。
- `metrics`：指标名、优化方向和主指标标志。

`execution.timeout_seconds`、`memory_limit_mb`、`cpu_limit` 可针对长时 TDS
调整。Docker 是正式环境的安全边界；本地后端仅用于开发，并在报告中标记为
`degraded_isolation`。

## 输出

每个实验的审计目录为 `results/module7/<experiment_id>/`：

- `workspace/plan.json`：规范化的 GeWu 输入。
- `workspace/experiment.py`：最终执行版本。
- `workspace/experiment_repair_N.py`：第 N 轮修复版本。
- `sandbox_runs/*/stdout.log`、`stderr.log`：完整运行日志。
- `sandbox_runs/*/execution_report.json`：资源、超时、返回码和结果校验。
- `module7_report.json`：模块7总报告及全部修复轮次。

成功后将结果发布为：

- 电力分支：`results/exp_power_result.json`
- 通用分支：`results/exp_general_result.json`

结果必须包含 `experiment_id`、`hypothesis_id`、`branch`、
`simulation_type`、`engine`、`status`、`observations` 和 `artifacts`。
`observations` 中每行包含 `condition`、`repeat` 和 `metrics`，模块8可直接按
基线/方案分组做 t 检验、效应量和置信区间分析。

## 命令

```powershell
# 不依赖电力库的端到端验证
python demo_module7.py

# 只生成并检查 pandapower OPF 代码
python demo_module7.py --plan examples/module7_power_opf_plan.json --dry-run

# 配置 QWEN_API_KEY 后使用百炼生成/修复
python demo_module7.py --plan examples/module7_power_opf_plan.json --dry-run --qwen
```

## Safe real-simulator verification

Module 7 now separates the public engine name from the import package:

- OPF engine: `pandapower`, import package `pandapower`
- TDS engine: `andes`, import package `andes`
- Co-simulation engine: `ltbams`, import package `ams` plus `andes`

Recommended local verification order:

```cmd
cd /d D:\AIone\qwen_ai_scientist
python -B -m unittest tests.test_module7 tests.test_module8 -v
python -B demo_module7_power_real_safe.py
python -B agents\code_engineer.py examples\module7_power_opf_plan.json --backend local --no-qwen
python -B demo_module7.py --plan examples\module7_power_tds_real_safe_plan.json --backend local --no-qwen
python -B demo_module7.py --plan examples\module7_power_cosim_real_safe_plan.json --backend local --no-qwen
python -B demo_module7.py --plan examples\module7_power_tds_5s_stable_plan.json --backend local --no-qwen
```

Safety gates:

- Local/auto power runs are capped by Module 7 unless `execution.allow_long_local_run=true`.
- Real ANDES TDS is blocked unless `execution.allow_real_tds=true` and an ANDES case file exists.
- Real AMS+ANDES co-simulation is blocked unless `execution.allow_real_cosim=true` and both case files exist.
- Real TDS duration is capped by `execution.max_tds_simulation_seconds` unless `execution.allow_long_tds=true`.
- When a real case is blocked, the result metadata records `case_source=...blocked_by_safety_gate`; this is not real simulator evidence.

The Module 7 report includes `simulator_preflight`, which records installed
Python packages, required packages, dataset-file availability, selected
execution mode, and whether the current plan is safe to execute locally.

The `*_real_safe_plan.json` examples are short smoke tests. They prove real
package invocation and result-contract compatibility, but they are not long-run
scientific evidence.

`module7_power_tds_5s_stable_plan.json` is a controlled five-second ANDES TDS
integration demo. It is the safest local long-duration check without Docker.

For open-source users, prefer the one-command validator:

```cmd
python -B demo_module7_power_real_safe.py
```

This default command only runs the lightweight pandapower OPF check and writes
`results/module7_power_real_safe_summary.json`. Heavier real checks are opt-in:

```cmd
python -B demo_module7_power_real_safe.py --tds
python -B demo_module7_power_real_safe.py --tds --cosim
python -B demo_module7_power_real_safe.py --all --keep-going
```

## Docker deployment

Long ANDES/AMS runs should use Docker. Build the Module 7 power image after
Docker Desktop is installed and running:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_module7_power_docker.ps1
```

Then verify the container backend:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_module7_power_docker.ps1
```

Optional heavier checks:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_module7_power_docker.ps1 -RunTds
powershell -ExecutionPolicy Bypass -File scripts\verify_module7_power_docker.ps1 -RunTds -RunCosim
```

The Docker image is defined by `docker/module7-power.Dockerfile` and installs
the packages listed in `docker/requirements-power.txt`. Container runs set
single-threaded numeric defaults and isolated HOME paths to reduce ANDES/AMS
instability.
