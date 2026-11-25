# Benchmark｜统一 LF 迭代

| 任务 | 内容 |
| --- | --- |
| [SMS](SMS/README.md) | 短信垃圾信息二分类 |
| [AGnews](AGnews/README.md) | 英文新闻主题四分类 |
| [CHECKED](CHECKED/README.md) | 中文新闻真实性二分类 |
| [EDOS](EDOS/README.md) | 英文性别歧视二分类 |
| [IMDb](IMDb/README.md) | 英文影评情感二分类 |
| [Yelp](Yelp/README.md) | 英文商户评论情感二分类 |
| [YouTube](YouTube/README.md) | YouTube 单条评论垃圾信息二分类 |

七个任务使用相同的 LangGraph 编排、Analyzer、反馈构造和候选验收。各目录的 `tools/task.py` 定义任务语义和标签，各自保留数据划分、版本和训练记录。

## 迭代流程

1. 对同一批 LF 投票评估 Snorkel 和 `simple_vote`。
2. 用简单投票的训练统计、验证指标和样例预测构造 Analyzer 反馈。每组最多 30 个样例，训练未覆盖和冲突按原始 LF 投票识别。训练或验证的 LF 覆盖率低于 0.8 时优先补覆盖。
3. Analyzer 每轮选择新增、修改或删除一种操作，最多涉及 5 个 LF。Patcher 生成目标函数，删除由本地代码完成。
4. 评估候选并记录两种聚合器的修改影响，再用 Snorkel 验收。候选被拒后从保留版重试，Analyzer 会收到连续被拒的方案和分数变化。

## 候选验收

在 Snorkel 的验证集混淆矩阵中，每个真实类别单独计算 `(正确数 − 误标数) / 真实样本数`，再对各类别取等权平均。弃权只计入分母。候选相对保留版的分数提升须严格大于 `--acceptance-epsilon`，默认 `0.01`。连续拒绝 3 个有效候选时停止。验收只使用验证集。

每个候选使用现有最大版本号加一。反馈、分析和验收分别保存到候选的 `results/feedback.json`、`results/analysis.json` 和 `results/decision.json`。迁移前的记录保留原有内容与布局。

从项目根目录运行，以 SMS 为例。

```powershell
python -m pip install -r benchmark/SMS/requirements.txt
python -B benchmark/SMS/tools/data.py
python -B benchmark/SMS/orchestrator.py --version v1 --iterations 1
```

`--iterations 0` 只重新评估起点。追加迭代使用根目录 `.env` 中的 LLM 配置。各任务的 README 有单版评估和下游训练命令。
