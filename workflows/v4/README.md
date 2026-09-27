# Workflow V4

V4 使用 12 个 Label Function 识别多标签风险类别和 `low / medium / high` 风险等级，不使用风险分数。

完整的逐步流程、12 类规则分支、指标公式、重试机制、历史版本结果和实现限制见 [完整工作流程与实现细节](WORKFLOW_DETAILS.md)。

## 目录

- `artifacts/vocabulary.json`：固定 12 类风险词表
- `artifacts/examples.json`：每类 Label Function 的固定参考样例
- `init.py`：分别调用 12 次 LLM，生成 `versions/v1/script_v1.py`
- `analyzer.py`：读取当前脚本及两类 mismatch 各前 20 条，输出修改分析
- `patcher.py`：根据分析生成下一版本完整脚本
- `orchestrator.py`：自动执行运行、分析、修改和下一版本评估
- `tools/runner.py`：运行当前版本并完成合并、评估和错误切分
- `tools/paths.py`：报告路径
- `tools/merger.py`：合并 gold 与预测结果
- `tools/evaluator.py`：计算类别、等级和高风险指标
- `tools/slicer.py`：切分类别与等级错误
- `tools/comparator.py`：用明确版本号比较两个版本的必要指标

## 运行

先在 `init.py` 中生成首版脚本：

```powershell
py -3 -m workflows.v4.init
```

再运行 `tools/runner.py`。当前版本由文件顶部的 `VERSION = "v1"` 控制：

```powershell
py -3 workflows/v4/tools/runner.py
```

自动迭代默认值由 `orchestrator.py` 顶部的 `START_VERSION` 和 `ITERATIONS` 控制。运行后可输入当前版本和迭代轮数，并通过 `tqdm` 查看整体进度：

```powershell
py -3 workflows/v4/orchestrator.py
```

## 迭代框架优化

针对 v1–v5 中出现的整份脚本重写、少数类召回下降但 Accuracy 上升、退化版本仍继续推进等问题，框架做了以下限制：

1. Analyzer 仍只读取当前脚本、`type_mismatch` 前 20 条和 `level_mismatch` 前 20 条，但必须输出 1–6 个明确的 `target_functions`。每项修改建议必须有 mismatch 样例支持。
2. Patcher 不再返回整份脚本，只返回目标函数的完整替换代码。未被 Analyzer 指定的 Label Function、其他函数和全局代码保持原样，避免一次修改影响全部类别。
3. Patcher 会校验函数名、函数数量、Python 语法和风险分数字段；生成结果不合法时自动重新生成一次。
4. Comparator 不以 Accuracy 或 mismatch 数量作为主要优化目标，因为训练集中 low 样本占 870/1000。综合质量分使用：类别 Micro-F1 25%、类别 Macro-F1 25%、等级 Macro-F1 20%、高风险 Recall 10%、高风险 F1 20%。
5. 新版本除了综合质量分必须提升，还限制关键指标最大下降幅度。未通过时，Patcher 会读取明确的版本对比反馈重新修改一次。
6. 同一版本连续两次未通过质量门槛时，orchestrator 保留当前版本并停止，不再自动推进退化版本。

相关全局配置：

- `patcher.py`: `MAX_FUNCTION_CHANGES`、`GENERATION_ATTEMPTS`
- `comparator.py`: `WEIGHTS`、`MAX_DROPS`
- `orchestrator.py`: `VERSION_ATTEMPTS`

## 报告

输出位于 `versions/v1/reports/`：

- `predictions/predictions.csv`
- `merged/merged.csv`
- `evals/category_metrics.json`
- `evals/level_metrics.json`
- `errors/type_mismatch.csv`
- `errors/level_mismatch.csv`
- `analysis/analysis.json`
- 下一版本的 `patch/patch_report.json`
- 下一版本的 `comparison/v1_vs_v2.json`（版本号随迭代变化）

类别报告只保留 micro-F1、macro-F1 和各类别 precision/recall/F1/support。等级报告只保留 accuracy、macro-F1、各等级 precision/recall/F1/support，以及高风险 recall/F1。
