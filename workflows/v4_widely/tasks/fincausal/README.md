# FinCausal Task 1：金融因果关系标注

主程序按阶段拆为 [init.py](init.py)、[analyzer.py](analyzer.py)、[patcher.py](patcher.py) 和 [orchestrator.py](orchestrator.py)。`data/` 保存两个 CSV 和[数据集说明](data/README.md)；`artifacts/` 保存配置、提示词与数据清单；`tools/` 的职责见[工具说明](tools/README.md)；`versions/` 保存规则、候选和报告。

## 1. 数据集构成

原始带标签数据：trial **8,580 条**、practice **13,478 条**，共 **22,058 条原始记录**，不代表去重后的独立文本数量。只使用 Task 1，不使用 Task 2 的原因/结果片段抽取标注，也不将无金标的 blind evaluation 用于本地评分。

当前训练集 **4,948 条**、测试集 **1,236 条**。原始 22,058 条记录中，复核排除了 4 条空文本、726 条重复记录及 6 条同文异标记录；未猜测冲突标签。 从复核后的 21,322 条候选中，按标签下采样删除 15,138 条，再按标签组合分配约 80%/20%；训练、测试各自的非零类别数量最大/最小不超过 **3:1**。相同文本整组分配，不跨集合；seed 只用于同标签组合内的稳定排序。数量与比例详见 [数据说明](data/README.md)。

`artifacts/data_metadata/` 仅保留两个文件：`manifest.json` 用于数据校验和续跑绑定；`automated_review.json` 保存重新准备数据所需的源文件哈希、修复和排除规则。数量与标签分布见 [数据说明](data/README.md)。原始数据未改写，当前为清理后的实验子集，分数不直接等同官方完整评测。

标注类型是 **金融文本因果关系存在性二分类**：1 表示存在因果关系，0 表示不存在。示意：“Revenue fell because demand declined.” 可标为 `{"label": "1"}`；仅陈述“Revenue was reported on Monday.” 而无因果关系可标为 0。本任务不输出原因和结果的文本位置。

## 2. 初始 LF 怎么构建

共 **2 个 LF**：`lf_01` 对应 0，`lf_02` 对应 1。从训练集按 seed=42 打乱后，每个标签最多选 3 条样例，将各标签样例、本任务标注说明和 LF 设计提示交给 LLM，每次生成一个指定函数。生成后检查语法和接口；格式或代码校验失败时携带反馈重试一次。LF 运行时只接收 `text` 和 `inputs`，不接收金标或样本 ID；命中返回 `labels`、`reason`，不确定则返回 `None`。

正类 LF 识别原因与结果之间的联系；负类 LF 识别有依据的非因果结构。不能把单个 because 等连接词当作充分证据，也不能仅因缺少连接词就判为 0。最终采用标签投票；平票或全部弃权时按训练集类别频次优先，频次仍相同时按配置标签顺序选择。 数据已就绪，可直接运行本目录的 `orchestrator.py` 开始实验。

## 3. 如何评价 LF 的质量

单个 LF 统计触发次数、覆盖率（触发样本数 / 总样本数）和触发时标签精度（正确投票标签数 / 投票标签总数），标记从未触发的函数；未触发时精度未定义。整体检查未覆盖样本数和不同标签投票的冲突样本数。重点查看因果二分类混淆矩阵、正类漏报和误报，检查将时间先后或相关性误判为因果，以及隐式因果漏报；连接词列表仅作诊断线索。

聚合后以 **Macro-F1** 作为训练集质量分，候选必须严格提升，且正类 1 的 F1 下降不得超过 **0.02**（绝对差值）。同时报告 Accuracy 和各类 Precision/Recall/F1，不使用原因/结果片段抽取指标。

训练集用于生成样例、定位错误、修复 LF 和决定是否接受候选。测试集只在本次迭代决策结束后评估，不向模型提供测试样例、错例或指标，不影响重试及版本选择。每个保存的脚本（包括候选）同目录下的 `report.md` 同时列出训练集和测试集指标；任务总 `report.md` 和 `script_metrics.csv` 汇总全部脚本。单 LF 诊断基于原始投票，版本接受依据训练集聚合预测。

## 4. 主程序与运行方式

| 主程序 | 职责 |
| --- | --- |
| `init.py` | 从训练样例逐个生成 LF，校验后保存 `versions/v1/rules.py`；不做迭代修改 |
| `analyzer.py` | 根据某版本的训练错例输出修改建议，保存到 `versions/vN/reports/analysis/analysis.json` |
| `patcher.py` | 读取匹配当前脚本和数据的分析，只替换指定 LF；候选保存到 `versions/vN/reports/patch/attempt_N/` |
| `orchestrator.py` | 串联初始化、训练评估、分析、修改、质量比较、重试、接受版本和最终测试报告；负责续跑 |

在本任务目录中运行完整流程（自动从当前已接受版本继续；无版本时创建首版）：

```powershell
python orchestrator.py --iterations 1
```

尚无版本时只生成首版：

```powershell
python init.py
```

按需单独分析和修改已保存版本：

```powershell
python analyzer.py --version v1
python patcher.py --version v1
```

单独运行 `patcher.py` 只保存候选，不推进已接受版本；总控负责自动版本选择。总控在 `versions/iterations/` 保存自己的迭代候选。各入口支持 `--help`；模型调用配置仍来自项目根目录 `.env`，token 继续累计到本任务账本。

## 数据准备与重新开始

数据准备由 `tools/data.py` 完成，多数类下采样与分层分配一起求解；数据读取时再次校验两侧类别比例与文本隔离。准备新快照需要 SciPy（已加入根目录 requirements.txt）；运行现有 CSV 不依赖求解器。

当前已清空旧规则、候选、报告、检查点、token 账本和历史归档。运行 `python orchestrator.py --new --iterations 1` 将重新生成 v1 并迭代一轮，指标与 token 从头记录。首轮完成后，去掉 `--new` 即可继续迭代。数据、配置、提示词和流程代码均保留。
