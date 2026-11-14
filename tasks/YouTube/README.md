# YouTube：WRENCH 固定划分下的 LF 迭代

使用与同级 SMS 相同的 WRENCH 固定划分导入方式：训练集 LF 投票拟合 Snorkel → 固定验证集提供开发反馈 → Analyzer 先选择操作类型再分析 LF → Patcher 生成下一版 → 验证集和测试集分别评价，保存版本、Token 用量和图表。

## 数据和标签

直接导入 [WRENCH 官方 YouTube 数据](https://huggingface.co/datasets/jieyuz2/WRENCH/tree/d1185f6cfba8d5ace2a39229799cb3f1c136bf66/classification/youtube) 的 `train.json`、`valid.json`、`test.json`，固定提交为 `d1185f6cfba8d5ace2a39229799cb3f1c136bf66`。三个集合的样本归属、顺序和正文逐条与该版本一致，不重新抽样、分组、去重或改写文本。正文包含官方预处理保留的多语言、URL、HTML、表情和换行。

官方 `0=HAM`、`1=SPAM` 映射为 CSV 中的 `ham/spam`；LF 和模型内部仍用 `0=ham、1=spam、-1=ABSTAIN`。编号为 `youtube_<split>_<官方编号>`，三个集合的编号互不重复。与 SMS 一样，训练 CSV 只保存 `id,text`，隐藏官方训练金标，不导入官方预定义 LF 的 `weak_labels`。

| 集合 | 条数 | CSV 字段 | 用途 |
| --- | ---: | --- | --- |
| `data/train.csv` | 1,686 | `id,text` | 运行 LF、统计覆盖/冲突、拟合 Snorkel |
| `data/valid.csv` | 120（ham 64 / spam 56） | `id,text,label` | 开发指标、错例、弃权/正确样例和 LF 质量反馈 |
| `data/test.csv` | 250（ham 132 / spam 118） | `id,text,label` | 每版仅预测、评价和报告，不进入 LLM 反馈 |
| `data/split.json` | 合计 2,056 | 来源、固定提交、源 JSON 与 CSV 的 SHA256、条数、用途 | 冻结划分，后续离线校验复用 |

`tools/data.py` 固定源 JSON 哈希；三份源文件全部下载并校验通过后才写入 CSV。后续启动只校验本地数据，不重新下载或抽样。数据不完整、内容或来源记录变化时停止；旧 `labeled/unlabeled` 划分也会被明确拒绝。仅在三个 CSV 和清单都不存在时自动导入。

`youtube_spam.csv` 保留为原始资料，不参与当前数据划分或模型流程。官方数据并非该 CSV 的重新划分；总样本数也不同。官方样本划分中的重复正文和跨集合同文保持原样，不额外做正文隔离。

## 实验起点

当前保留 WRENCH 固定划分下的 `versions/v1` 至 `v10`、对应评估结果、迭代汇总、Token 记录和图表；v10 另有下游模型。继续实验时从需要的已有版本启动；从旧版追加会覆盖同名后续版本。

各版新生成的 `metrics.json` 记录数据提交、条数和 CSV 哈希；绘图只加载与当前 `data/split.json` 完全一致的结果。

## 运行

从项目根目录执行，依赖见 [requirements.txt](requirements.txt)：

```powershell
# 与 SMS 一样，询问起始版本和追加轮数。
python -B 'tasks/YouTube/orchestrator.py'

# 仅评价 v1，不调用 LLM。
python -B 'tasks/YouTube/orchestrator.py' --version v1 --iterations 0

# 从 v1 追加一轮，生成 v2。
python -B 'tasks/YouTube/orchestrator.py' --version v1 --iterations 1

# 单独评价某个版本，不更新 iterations.csv。
python -B 'tasks/YouTube/tools/runner.py' --version v1

# 根据已有版本结果绘制 PNG/SVG。
python -B 'tasks/YouTube/picture/plot_metrics.py'
```

LLM 沿用项目根目录 `.env` 的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`；地址为空时为 `https://api.deepseek.com`。零轮评价和绘图不调用 LLM。

## v10 逻辑回归下游模型

`versions/v10/train_logistic.py` 参考 SMS v15 的同名脚本，使用 `results/weak_labels.csv` 中 train 的非弃权硬标签训练 TF-IDF + Logistic Regression。默认词 1–2 gram、最多 100,000 个特征、`sublinear_tf=True`，逻辑回归使用 `liblinear`、最多 2,000 次迭代、seed 42。词表、IDF 和分类器均只在清洗后的训练集上拟合；验证集金标仅按 spam F1 从 `C=[0.1, 1, 10]` 中选模型，同分时取列表中的第一个。测试集在选 C 后完整预测，不弃权，金标仅用于最终指标。

```powershell
# 只检查输入，不训练、不写文件。
python -B 'tasks/YouTube/versions/v10/train_logistic.py' --check-data

# 训练并保存模型、完整测试集预测、指标和报告。
python -B 'tasks/YouTube/versions/v10/train_logistic.py'

# 已有结果时指定新的空目录，避免覆盖现有模型。
python -B 'tasks/YouTube/versions/v10/train_logistic.py' --output-dir 'tasks/YouTube/versions/v10/tfidf_lr_results_run2'
```

默认输出到 `versions/v10/tfidf_lr_results/`：`model.joblib` 包含词表、IDF 和分类器，可直接 `joblib.load(path).predict_proba(texts)`；`train_cleaned.csv` 保存实际训练样本，`test_predictions.csv` 保存完整测试集的类别、概率和金标对照，`metrics.json`、`tuning_history.json`、`run_config.json`、`data_summary.json`、`report.md` 保存指标、参数选择、配置、输入哈希和可读报告。现有 Snorkel 结果仍保存在 `results/`。

本次运行剔除 302 条弃权，实际使用 1,384 条弱标签（ham 657 / spam 727），验证集选得 C=10。测试集 250 条全部预测，Accuracy 94.40%、Spam F1 93.69%、Macro-F1 94.33%；验证集 Accuracy 90.83%、Spam F1 89.11%、Macro-F1 90.60%。详见 [下游模型报告](versions/v10/tfidf_lr_results/report.md)。

## 与 SMS 一致的实验流程

使用 SMS 的五组抽样反馈：验证错例、验证弃权、验证正确、训练未覆盖、训练冲突，每组最多 10 条；按 seed 42 和版本名抽样。验证集样例附金标和聚合预测，训练样例仅附正文、编号和 LF 投票。模型看到完整集合的汇总指标，但没有全部样本正文或历轮分析记录。测试集的样本、标签、预测和指标不进入 Analyzer/Patcher。

Analyzer 在同一次调用中先通过 `decision` 选择本轮唯一操作类型（新增、修改或删除），再分析具体 LF；`operations` 必须全部与决策一致。训练集或验证集的 LF 覆盖率低于 `LOW_COVERAGE_THRESHOLD = 0.7` 时优先改善覆盖，必须给出 `coverage_plan`，说明覆盖缺口、改善方案和误标控制；可以扩展已有 LF，也可以新增独立模式。删除不能直接提升整体 LF 覆盖率，低覆盖时若优先删除有害规则，须说明原因及后续扩覆盖方向。

默认每轮只增、改、删一个 LF，至少保留三个，以 AST 校验实际改动与 Analyzer 建议一致。Patcher 及技术修复都须遵守已选操作类型和覆盖方案。LF 只接收单条 `text` 字符串，不能读文件、网络、编号或金标，也不能抄整句建立标签查表。语法、接口、执行或投票错误最多额外修复一次；API 和文件错误直接停止。

反馈增加各类别的已覆盖/未覆盖数量；`analysis.json` 保存决策及覆盖上下文，`changes.md` 记录本轮决策。Analyzer 原始回复保存为被分析版本的 `results/analysis_raw.txt`；合法 JSON 的结构校验失败时另存 `analysis_invalid.json`，记录错误后停止。

Snorkel LabelModel 使用二分类、CPU、500 epochs、seed 42、固定均匀先验 `[0.5, 0.5]`，只在训练集投票上拟合；验证集和测试集金标不参与模型拟合或先验估计。验证集与测试集使用同一已拟合模型预测。所有 LF 弃权时强制最终弃权，最高概率并列时也弃权；`weak_labels.csv` 中写为 `abstain`，概率列为 `p_ham`、`p_spam`。全量指标包含弃权，另报已覆盖指标。

以最新可运行版本继续，不因质量指标下降自动回退；从 vN 生成 vN+1。从旧版重新追加会覆盖对应后续版本。常用参数位于 `orchestrator.py` 顶部：`MAX_LF_CHANGES = 1`、`MAX_EXAMPLES_PER_GROUP = 10`、`FEEDBACK_SEED = 42`、`LOW_COVERAGE_THRESHOLD = 0.7`。阈值按两组 LF 覆盖率的较小值严格小于阈值触发，只决定优化优先级，不是版本验收门槛。

## 文件与结果

| 路径 | 内容 |
| --- | --- |
| `versions/v1/lfs.py` | 六个手写冷启动 LF：自我宣传、推广链接、赚钱奖品、视频评价、播放量讨论、个人观看反应 |
| `tools/data.py` | WRENCH 固定提交导入、训练金标隔离和哈希校验 |
| `tools/analyzer.py` / `tools/patcher.py` | 分析、LF 生成和实际改动校验 |
| `tools/label_model.py` / `tools/evaluate.py` / `tools/runner.py` | 聚合、含弃权评价、投票与结果输出 |
| `tools/llm.py` | DeepSeek 请求、Token 逐次明细与汇总 |
| `versions/vN/results/` | `metrics.json/csv`、`report.md`、`lf_votes.csv`、`weak_labels.csv`、`lf_quality.csv`、`snorkel_model.pkl` |
| 被分析版本的 `feedback.json` / `analysis.json` | 实际输入快照、用于生成下一版的分析 |
| 新版本的 `changes.md` / `errors.json` | LF 改动；发生技术错误时保留记录 |
| `iterations.csv` | 当前 WRENCH 划分下每个成功版本一行，验证与测试指标分开 |
| `tokens.csv` / `tokens.md` | 实际 API Token 记录，本次与历史分开，未知用量不记作 0 |
| `picture/plot_metrics.py` | 总览、分类别指标、预测构成、逐轮变化、投票与聚合方向；单个版本不生成逐轮变化图 |

初始 LF 根据评论任务的一般模式构造，未调用 LLM 初始化，也未使用划分后的测试集调优。规则覆盖和质量以实际运行后的结果为准。详细约定见 [迭代框架.md](迭代框架.md)。
