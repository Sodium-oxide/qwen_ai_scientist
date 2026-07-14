# 模块8对接契约

`agents/mingbian.py` 消费模块7发布的 `exp_general_result.json` 或
`exp_power_result.json`，输出模块9可直接引用的统计报告。模块8不运行仿真，
也不调用大模型计算或修改数字。

## 输入要求

结果 JSON 至少包含：

- `experiment_id`、`hypothesis_id`
- `branch`：`general` 或 `power`
- `simulation_type`：`general`、`opf`、`tds` 或 `cosim`
- `engine` 和成功的 `status`
- `metric_directions`
- `observations`：每条包含 `condition`、`repeat` 和 `metrics`

`observations` 必须同时包含 `baseline` 和 `proposed`。每组至少三次有效重复，
否则判定为 `inconclusive`。

## 统计规则

- 描述统计：均值、样本标准差、标准误、最小值、最大值。
- 区间估计：t 分布 95% 置信区间。
- 假设检验：Welch 独立样本 t 检验，默认 `alpha=0.05`。
- 效应量：Cohen's d，并区分统计显著性和实际显著性。
- 判定：`supported`、`refuted`、`inconclusive`。
- 电力安全门：即使主指标显著改善，电压、功角或稳定率约束失败时仍会驳回。

## 输出目录

默认目录为 `results/module8/<experiment_id>/`：

- `analysis_report.json`：模块9的权威数字来源。
- `mingbian_report.md`：人类和论文模块可读报告。
- `plot_mingbian_results.py`：可独立重绘的脚本。
- `figures/mingbian_comparison.png`：带显著性星标和95% CI的对比图。
- `method_memory_update.json`：成功/失败模式与经验。

## 命令

```cmd
python demo_module8.py
python demo_module8.py --input examples\module8_power_opf_result.json
python demo_module8.py --input examples\module8_power_tds_result.json
python demo_module8.py --input examples\module8_power_cosim_result.json
```

模块9必须直接引用 `analysis_report.json` 中的数字，不得自行重算或改写。
