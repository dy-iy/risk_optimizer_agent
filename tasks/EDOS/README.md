# EDOS：固定验证集引导的 LF 迭代

按 [Finnsp](../Finnsp/) 的迭代流程复刻。任务配置在 [tools/task.py](tools/task.py)，冷启动规则在 [versions/v1/lfs.py](versions/v1/lfs.py)。

## 已确认的任务与数据

英文性别歧视二分类（EDOS Task A）。1=针对女性的性别歧视、贬损、刻板印象、排斥或暴力威胁，0=非性别歧视。提到女性、一般粗口、讨论或反驳性别歧视不自动构成正类；区分引用、否定、反讽和说话者立场。仅做二分类，不输出 category/vector 子类别。

标签：0=not sexist；1=sexist；-1=弃权。每个 LF 是普通的 `def lf_name(record)`，唯一输入是 `record['text']`；执行器为每条规则复制输入，不传编号、来源或金标。

沿用现有 [data/split.json](data/split.json) 中的来源划分：训练 **14000** / 验证 **2000** / 测试 **4000**，不重新抽样或下载。训练 CSV 仅含 id,text，验证/测试 CSV 含 id,text,label；读取时将源标签映射成上述整数。启动核验文件 SHA-256、表头、条数、标签、类别数和编号唯一性，缺失或变化直接停止。

`raw/` 仅为来源归档，运行器和模型 Agent 不读取源金标或已有弱标签。原有清单和数据文件保持原样。

## 迭代行为

1. 评价指定起点，每版只在训练 LF 投票上拟合一次 Snorkel（CPU、500 epochs、seed 42），同一模型预测验证和测试集。类别先验按完整验证集比例估计，概率列顺序为 [0, 1]。
2. Analyzer 在同一回复中先选择唯一操作类型 add/modify/delete，再提出 1–5 项具体操作。五组反馈为验证错例、弃权、正确、训练未覆盖、训练冲突，每组至多 30 条，seed 42；只提供完整验证指标和无金标训练信息。
3. 每轮将 Analyzer 原文交独立 JSON 格式 Agent 一次，只允许修语法；本地严格校验内容保留和业务结构，失败直接停止。
4. Patcher 输出完整 lfs.py，以 AST 校验实际改动和建议一致；保留至少 3 个 LF。技术错误最多再修复一次，同轮操作决策保持不变；数据/接口错误直接停止。
5. 保存覆盖率、全量/已覆盖准确率、每类 Precision/Recall/F1、Macro-F1、混淆矩阵、训练冲突率和 LF 质量。低于 0.8 的 LF 覆盖率要求改善方案；以最新可运行版本继续，无质量淘汰或指标下降回退。

测试样本、标签、预测和指标不进入 Analyzer/Patcher，也不用于决定轮数或选版。验证标签通过类别先验参与拟合，因此明确记录 `gold_used_for_fit=true`、`validation_gold_usage=class_balance_only`；训练与测试金标不参与拟合。全量指标将弃权计为漏判；所有 LF 弃权时聚合也弃权。

## 运行

从项目根目录执行：

```powershell
# 离线数据校验
python -B 'tasks/EDOS/tools/data.py'

# 交互输入起点和追加轮数
python -B 'tasks/EDOS/orchestrator.py'

# 只评价基线，不调用 LLM
python -B 'tasks/EDOS/orchestrator.py' --version v1 --iterations 0

# 从 v1 追加一轮（会调用配置的模型 API）
python -B 'tasks/EDOS/orchestrator.py' --version v1 --iterations 1

# 单版评价；绘图
python -B 'tasks/EDOS/tools/runner.py' --version v1
python -B 'tasks/EDOS/picture/plot_metrics.py'
```

使用项目根目录 `.env` 的 DEEPSEEK_API_KEY、DEEPSEEK_MODEL、DEEPSEEK_BASE_URL（空地址默认 https://api.deepseek.com）。常用参数集中在 orchestrator.py，依赖见 requirements.txt。

`versions/vN/results/` 保存模型、指标、报告、投票、弱标签和 LF 质量；`weak_labels.csv` 的标签为整数文本或 abstain，概率列为 p_0 至 p_1。分析及原文归被分析版本，改动归新版本，每批原文另存于 `analysis_calls/<批次>/`。`iterations.csv`、`tokens.csv`、`tokens.md` 在运行时生成，只记录真实 API 用量。重新执行同一起点会重写后续同名 LF/结果，请选定起点和轮数后运行。

绘图只接受与当前清单一致的结果，保存 PNG/SVG：指标总览、全部类别指标、正确/错误/弃权构成、相邻版本变化，以及全部类别的一致投票与聚合方向分歧。迭代轮数为 0 时不生成分析或改动记录。

重跑时先清空当前分析展示，历史批次保留在 analysis_calls 中；目标版本指标清除后，iterations.csv 同步移除旧行，避免失败批次留下过期结果。清单内部字段结构损坏按数据错误直接停止。
