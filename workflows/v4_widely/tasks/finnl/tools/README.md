# 工具职责

主流程请从任务根目录的 `init.py`、`analyzer.py`、`patcher.py`、`orchestrator.py` 阅读。这里的工具不负责决定整个迭代流程。

| 文件 | 职责 | 主要接口 |
| --- | --- | --- |
| [runner.py](runner.py) | 校验、加载和执行 LF，汇总投票；提供离线评估入口 | `RuleSet`、`run_records`、`read_script` |
| [evaluator.py](evaluator.py) | 计算分类指标、覆盖率、混淆矩阵与 LF 质量 | `evaluate`、`quality_report` |
| [slicer.py](slicer.py) | 按输出字段抽取前 20 条错例，补充任务诊断 | `sample_errors` |
| [comparator.py](comparator.py) | 比较质量分与指标下降幅度，决定候选是否通过门槛 | `compare` |
| [data.py](data.py) | 字段映射、按标签下采样与分层分配（每侧 ≤3:1）、CSV 与分布校验 | `load_prepared`、`prepare` |
| [paths.py](paths.py) | 定义任务配置与数据、静态资源、版本、报告路径 | `get_task`、`build_paths` |
| [model.py](model.py) | 模型请求、提示词契约、网络重试与 token 记账 | `ModelSession`、`ModelCaller`、`UsageLedger` |
| [reporter.py](reporter.py) | 写入报告和指标表，读取检查点及恢复轮次 | `write_results`、`load_checkpoint` |
| [common.py](common.py) | JSON 原子写入、哈希计算与项目根路径 | `read_json`、`write_json`、`digest` |

`__init__.py` 仅声明 Python 包。任务配置、提示词和数据复核记录位于 [artifacts/](../artifacts/)。

在任务目录离线评估某个版本：

```powershell
python tools/runner.py --version v1 --split train
```

评估独立修改生成的候选：

```powershell
python tools/runner.py --script versions/v1/reports/patch/attempt_1/rules.py --split train
```

默认将预测、指标、LF 质量和错例写到脚本同目录的 `reports/<split>/`。该命令不调用模型，也不修改当前已接受版本。
