# SMS：固定验证集引导的 LF 迭代

基于 WRENCH / SMS 的固定划分执行短信垃圾信息二分类：`0=ham`、`1=spam`、`-1=弃权`。LF 只接收正文字符串；训练金标不传给 LF 或 LLM。完整反馈和修改流程见 [迭代框架.md](迭代框架.md)。

## 数据

| 集合 | 条数 | 字段 | 用途 |
| --- | ---: | --- | --- |
| [data/train.csv](data/train.csv) | 4571 | id,text | LF 投票、Snorkel 拟合、弱标签生成 |
| [data/valid.csv](data/valid.csv) | 500 | id,text,label | 开发指标和 Analyzer 反馈 |
| [data/test.csv](data/test.csv) | 500 | id,text,label | 预测与最终评估，不进入 LLM 反馈 |

[data/split.json](data/split.json) 冻结 WRENCH 提交、来源和 CSV 哈希，后续启动校验复用，不重新划分。Snorkel 使用 CPU、500 epochs、seed 42、固定均匀先验 `[0.5, 0.5]`，只在训练投票上拟合；所有 LF 弃权时最终也弃权。

## 运行

在项目根目录、实际实验 Python 环境中执行，依赖见 [requirements.txt](requirements.txt)：

```powershell
# 交互输入已有起点版本和追加轮数；输入 0 只评价起点，不调用 LLM。
python -B tasks/SMS/orchestrator.py

# 单版评价，不更新 iterations.csv。
python -B tasks/SMS/tools/runner.py --version v1

# 从已有版本结果绘制 PNG / SVG。
python -B tasks/SMS/picture/plot_metrics.py

# 下游 TF-IDF + LR 输入核验，不训练、不写文件。
python -B tasks/SMS/versions/v15/train_logistic.py --check-data
```

SMS 的 orchestrator 使用交互输入。追加轮数大于 0 时读取根目录 `.env` 的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`，调用配置的 API。常用参数在 `orchestrator.py` 顶部。

## 输出

`versions/vN/results/` 保存指标、报告、投票、弱标签、LF 质量和 Snorkel 模型。分析记录归当前版本，修改归下一版；`iterations.csv` 汇总成功版本，`tokens.csv` / `tokens.md` 记录真实调用用量。以最新可运行版本继续，不因指标下降自动回退；从旧版追加可能覆盖已有后续版本，重跑前应先备份。

已有下游模型位于 `versions/v15/tfidf_lr_results/`。训练仅使用非弃权硬弱标签，验证集 spam F1 选择 C，测试金标只用于最终评价。重新训练应通过 `--output-dir` 指定新目录。
