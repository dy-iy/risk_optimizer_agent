# 迭代优化工作流

## 设计原则

当前闭环采用四个互补原则：

1. **Counterexample-guided iteration**：FP、FN、type mismatch 行不是只供阅读的报表，而是下一轮 patch 的反例集合。分析器按错误方向和类型对抽样，patch 必须解释覆盖哪些反例及保留哪些 guardrail。
2. **Coordinate descent**：每轮只选择一个 `focus_metric` 和一个独立修改机制，避免一次修改多个 scorer 后无法归因。
3. **Constrained Pareto promotion**：FP、FN、type mismatch 是晋级目标；任一回归即拒绝，至少一个实际改善且焦点改善才晋级。label/primary accuracy、MAE、RMSE 和 matched rows 是防止指标投机的质量护栏。
4. **Dataset-scoped memory**：评估报告记录 Gold SHA-256；历史趋势只使用同一数据集指纹，避免数据集升级造成伪 regression 或伪 improvement。

这些原则分别对应 CEGIS 的“反例驱动候选修正”、Reflexion 的“把外部反馈写回下一次决策”、DSPy 的“用明确指标编译/优化 LM pipeline”，以及多目标优化中的约束与 Pareto dominance。

参考：

- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366)
- [DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines](https://arxiv.org/abs/2310.03714)
- [Model Selection using Multi-Objective Optimization](https://arxiv.org/abs/1810.10669)

## 每轮状态机

```text
run baseline
  -> slice counterexamples + counterfactual diagnostics
  -> choose one focus metric
  -> analyze one falsifiable hypothesis
  -> build candidate in reports/patched staging area
  -> run candidate on the same Gold fingerprint
  -> constrained-Pareto gate
       accepted -> promote to workflows/baseline/versions/vN/scripts/
       rejected -> keep diagnostic artifacts, do not create next baseline script
```

## Type mismatch 专项诊断

`tools/slicer.py` 将类型错配拆成：

- `overassigned_weak_type`：Gold 为“无明显风险”，规则分配了弱主类型。
- `missed_primary_type`：Gold 有主类型，规则输出“无明显风险”。
- `cross_type_confusion`：Gold 和规则都有类型，但类别不同。

同时计算反事实 `force_no_risk_primary_when_rule_label_low`。该反事实不修改 scorer、风险分数或风险等级，用于判断问题是否位于主类别仲裁层。

## v20 -> v21 实验

最终 1000 条 Gold 上，v20 的错配构成为：

- `overassigned_weak_type`: 99
- `missed_primary_type`: 42
- `cross_type_confusion`: 21

反事实显示，把 low 等级的主类型与风险等级边界对齐，可将 type mismatch 从 162 降到 80。因此 v21 只做一个修改：

```python
PRIMARY_TYPE_MIN = 0.40
```

回放结果：

| metric | v20 | v21 | delta |
|---|---:|---:|---:|
| false_positive_rows | 46 | 46 | 0 |
| false_negative_rows | 14 | 14 | 0 |
| type_mismatch_rows | 162 | 80 | -82 |
| primary_type_accuracy | 0.756 | 0.821 | +0.065 |
| label_accuracy | 0.868 | 0.868 | 0 |

候选通过 `constrained_pareto_v1` 并已晋级为 v21。

随后又完成两次单变量实验：

- **v22**：修正 `NEG_HACK_RESEARCH` 过宽 guard，明确“被盗”事实不能被研究/识别语境提前归零；FN 14 → 12。
- **v23**：修正美元金额覆盖“ATM 交易量”非清算语境的问题；FP 46 → 45。

同一最终 Gold SHA-256 下的累计结果：

| metric | v20 baseline | v23 | delta |
|---|---:|---:|---:|
| false_positive_rows | 46 | 45 | -1 |
| false_negative_rows | 14 | 12 | -2 |
| type_mismatch_rows | 162 | 80 | -82 |
| label_accuracy | 0.868 | 0.870 | +0.002 |
| primary_type_accuracy | 0.756 | 0.824 | +0.068 |
| MAE | 11.909 | 11.741 | -0.168 |
| RMSE | 17.843 | 17.460 | -0.383 |

累计报告：`workflows/baseline/versions/v23/reports/comparisons/v20_vs_v23_cumulative.md`。
