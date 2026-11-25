# Yelp 英文商户评论情感分类

复用 [IMDb_improve](../IMDb_improve/) 的 LangGraph 弱监督迭代、计时和下游训练流程。训练、验证、测试规模依次为 **30,400 / 3,800 / 3,800**。

按消费者对商户、服务或商品的整体评价分类。0 为 Negative，1 为 Positive。留意否定、比较和褒贬混合。

[v1 冷启动规则](versions/v1/lfs.py) 沿用 [原任务](../../benchmark/Yelp/)。LF 只接收正文，返回 0、1 或 -1（弃权）。任务定义见 [tools/task.py](tools/task.py)。

## 数据与环境

使用原任务的冻结划分。[data/split.json](data/split.json) 记录文件的 SHA-256、条数和类别分布。训练 CSV 字段为 id、text，验证与测试 CSV 额外含 label。弱标签文件使用 0、1 或 abstain，概率列依次为 p_0、p_1。

```powershell
# 从项目根目录安装任务依赖，包含 langgraph==1.1.10。
python -m pip install -r improve/Yelp_improve/requirements.txt

# 离线核验现有划分。
python -B improve/Yelp_improve/tools/data.py
```

追加迭代读取项目根目录 `.env` 中的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和可选 `DEEPSEEK_BASE_URL`；默认地址为 `https://api.deepseek.com`。
路径由任务目录定位，与启动工作目录无关。仅评估已有版本无需 API Key。若本机默认 `python` 指向 Windows 应用别名，可将命令中的 `python` 替换为 `& D:/Miniconda/python.exe`。

## 运行

```powershell
# 交互选择起始版本和追加轮数。
python -B improve/Yelp_improve/orchestrator.py

# 评估已有 v1 的两种聚合器并更新迭代表，不调用 LLM。
python -B improve/Yelp_improve/orchestrator.py --version v1 --iterations 0

# 从已有版本追加一轮，会调用配置的 API。
python -B improve/Yelp_improve/orchestrator.py --version v1 --iterations 1

# 单版评估两种聚合器，不更新 iterations.csv。
python -B improve/Yelp_improve/tools/runner.py --version v1

# 补记已有两版的修改影响，不执行 LF、不拟合模型、不调用 LLM。
python -B improve/Yelp_improve/tools/impact.py --old-version v1 --new-version v2

# 绘制两种聚合器；也可指定 --aggregator Snorkel 或 simple_vote。
python -B improve/Yelp_improve/picture/plot_metrics.py

# 各聚合器按验证集选版，分别训练 TF-IDF + LR。
python -B improve/Yelp_improve/tools/train_logistic.py

# 安装 BERT 依赖，校验输入，再微调 bert-base-cased。
python -m pip install -r improve/Yelp_improve/requirements-bert.txt
python -B improve/Yelp_improve/tools/train_bert.py --check-data
python -B improve/Yelp_improve/tools/train_bert.py --device cuda
```

也可以进入任务目录后运行 `python -B orchestrator.py --version v1 --iterations 0`。
影响记录需要两个不同且已评估的 LF 版本；从旧版追加可能覆盖已有后续同名版本，运行前应确认起点。

## 编排与反馈

[orchestrator.py](orchestrator.py) 提供命令行和参数，[workflow.py](workflow.py) 使用与 IMDb_improve 相同的 LangGraph 节点及条件边。
常用参数为每轮最多 5 项 LF 操作、样例基数 `n=30`、抽样 seed 42。`add` 仅提供 `valid_abstained`、`train_uncovered`，各最多 `2n=60` 条，总计最多 120 条；`modify` 仅提供 `valid_wrong`、`valid_correct`、`train_conflict`，各最多 `n=30` 条，总计最多 90 条。各组不足时取全部，不重复或从其他组补齐。判定参数仅在总控 [orchestrator.py](orchestrator.py) 明文设置：

```python
LOW_COVERAGE_THRESHOLD = 0.80
HIGH_COVERED_ACCURACY_THRESHOLD = 0.90
```

`C = min(训练简单投票输出覆盖率, 验证简单投票输出覆盖率)`，`Q = 验证集已输出样本准确率`。
`C < 0.80` 为低覆盖，`Q >= 0.90` 为高准确率；恰好达到阈值时归入高覆盖/高准确率。策略模块接收总控参数，工作流只计算一次策略；Analyzer 直接使用计算结果，不自行判定。

| 系统状态 | 优先目标 | 允许操作 |
| --- | --- | --- |
| 低覆盖、高准确率 | 扩大正确覆盖 | 仅 add |
| 低覆盖、低准确率 | 先修复高影响误标，再扩大可靠覆盖 | 仅 modify |
| 高覆盖、低准确率 | 减少误标 | 仅 modify |
| 高覆盖、高准确率 | 针对剩余缺口精细优化 | add 或 modify |

验证集没有输出时，Q 为空，表示尚无精度证据，采用保守新增或扩展策略，不将其伪记为 0% 或 100%。本阶段不接通 stop，仍按指定轮数运行，不拆分开发集、不增加聚类或自动回滚。

策略在抽样前确定 `selected_action`。只有一种允许操作时直接采用；同时允许 add/modify 时，仍有训练或验证输出覆盖缺口就选 add，完全覆盖后选 modify。Analyzer 必须返回本轮已选操作，不能在只收到 add 样例时改选 modify，反之亦然。

[tools/feedback.py](tools/feedback.py) 独立构造反馈，每轮调用 Analyzer 前保存到被分析版本的 `versions/vN/results/feedback.json`。
反馈标明 `aggregator=simple_vote`；验证指标、训练输出统计及样例预测统一取自简单投票，LF 投票矩阵和 LF 质量仍使用两种聚合器共享的数据，不包含测试信息。`examples` 只包含本轮操作所需的两组或三组样例；`example_sampling` 记录操作、基数 n、各组上限及可用数量。工作流附加 `strategy_context` 后保存，记录本轮状态、阈值、目标、允许操作及已选操作，与发送给 Analyzer 的反馈一致。
同一版本重新分析时覆盖该文件，Analyzer 纠正时复用同一份反馈；仅评估（0 轮）不生成反馈文件。

1. 一次执行 LF，复用同一批投票评估 Snorkel 与 `simple_vote`。Snorkel 以训练投票拟合，CPU、500 epochs、seed 42；类别先验按完整固定验证集估计，按任务类别顺序排列。
2. 简单投票忽略弃权，各 LF 等权；仅唯一最多票类别输出标签，并列最多票或全弃权均弃权。概率列表示非弃权票占比；全弃权以均匀分布占位。
3. Analyzer 使用简单投票的完整验证统计及无金标训练信息，样例按已选操作提供。验证错例、弃权、正确按简单投票预测划分；`valid_abstained` 包含全弃权和平票，`train_uncovered` 只含所有 LF 均弃权；`train_conflict` 指原始 LF 至少投出两个不同的有效类别。Snorkel 继续独立评估和保存结果。
4. Analyzer 只接收反馈和调用函数。add 提示词要求先归纳多例共享的模式族，每条新增 LF 用至少两条独立输入样例和训练分布支持说明依据，在 plan 中写明泛化机制与弃权边界；禁止“一条样例一条 LF”、整句正则、专有人名特判、伪装成规则的单例条件拼接和凑满操作预算。训练例不视作标签证据，编造的变体不视作观测证据；Patcher 延续这些要求。modify 使用错例定位错误机制、正确样例约束保留行为、冲突样例检查投票矛盾。回复仍仅含 `action,operations`，每项只含 `target,evidence,plan`。操作必须与抽样前选择一致；delete 和 stop 均被本地校验拒绝。解析或业务校验失败，原 Analyzer 接收原回复及具体错误，最多纠正一次。
5. Patcher 一次只输出计划中的目标函数；本地按源码位置合并，保留其余 LF、常量和辅助代码。AST 校验操作类型、函数目标与预算，防止通过新增方案修改已有 LF，或通过修改方案新增 LF。
6. 生成校验与候选执行共用一次 LF 技术修复额度。API/文件错误直接停止；每轮重置修复额度。成功后记录两种聚合器的影响，继续最新可运行版本。

Patcher 逐项实现 `plan`，保留规则的行为和弃权边界。新增规则还需实现计划中的泛化机制。评估与影响记录展示新增的正确覆盖，`changes.md` 保存每项操作的依据和方案。

测试正文、标签、预测和指标不进入 Analyzer/Patcher，不用于选择操作或版本。
验证标签用于类别先验和开发反馈，计入监督预算；训练与测试金标均不参与拟合。
全量指标将弃权计为漏判，并同时报告已覆盖口径、每类 Precision/Recall/F1、Macro-F1 与混淆矩阵。
所有 LF 弃权时两种聚合器均输出弃权。

## 输出

任务提供冷启动 `versions/v1/lfs.py`。评估、迭代和下游训练各自生成对应结果。

```text
versions/vN/
├── lfs.py
├── Snorkel/
│   ├── metrics.json / metrics.csv / report.md
│   ├── weak_labels.csv / snorkel_model.pkl
│   ├── impact.json / impact.md / impact_samples.csv
│   ├── tfidf_lr_results/
│   └── bert_base_cased_results/
├── simple_vote/
│   ├── metrics.json / metrics.csv / report.md / weak_labels.csv
│   ├── impact.json / impact.md / impact_samples.csv
│   ├── tfidf_lr_results/
│   └── bert_base_cased_results/
└── results/
    ├── lf_votes.csv / lf_quality.csv
    ├── feedback.json
    └── analysis*.json / analysis*_raw.txt / patcher*.txt / changes.md / errors.json
```

`results/` 只保存一份共享投票及质量。分析归被分析版本，修改及评估归下一版。
`iterations.csv` 按版本及聚合器记录指标。每次模型调用的 Token 和请求耗时（秒）写入 `tokens.csv`，包括失败请求、Analyzer 纠正和 LF 修复。旧版 Token 日志可继续使用，历史请求的耗时留空。

`timing.csv` 按运行批次保存起点评估、每轮迭代及整次运行的墙钟耗时。单轮耗时覆盖反馈准备、模型请求及纠正、候选评估和结果保存。整次运行包括起点评估与所有已执行轮次。失败或中断会记录当时的耗时，`--iterations 0` 也会记录本地评估和总用时。终端及 `tokens.md` 汇总本次运行的模型请求耗时和工作流耗时。
绘图读取新布局，PNG/SVG 分别写入 `picture/Snorkel/` 和 `picture/simple_vote/`。

修改影响只比较训练与验证集，按 ID 和 LF 名称对齐；新增/删除 LF 的缺失票视为弃权。
记录覆盖变化、投票和预测变化、冲突、全部类别的概率变化，以及验证修复、退化和新增误标。
影响记录用于诊断，不送给 Analyzer/Patcher，不触发自动接受、回退或停机决策。

## 下游训练

两个脚本默认分别为 Snorkel 和 `simple_vote` 选择验证集全量 Macro-F1 最高的 LF 版本，同分取较早版本。用 `--version v5` 固定版本，或用 `--aggregator Snorkel` 只训练一个聚合器。`--plan` 显示所选版本，`--check-data` 额外校验弱标签，两者均不训练或写结果。

两种模型都只拟合非弃权训练硬弱标签，完整预测验证和测试集。验证集 正面类 F1 用于选参或选检查点，测试集仅用于最终评估。

- [逻辑回归](tools/train_logistic.py) 使用英文词 1–2 gram、最多 100,000 特征和 sublinear TF。在 `C=[0.1,1,10]` 中选参，同分取第一个。使用 liblinear、max_iter=2000、seed=42。模型、指标和预测保存在各聚合器的 `tfidf_lr_results/`。
- [BERT](tools/train_bert.py) 全量微调 `bert-base-cased`，默认 3 个 epoch、学习率 `2e-5`、seed 42 和动态 padding。每条正文最多输入 512 个 token，超长时保留开头和结尾。默认使用本地缓存，也可指定 `--model-path` 或通过 `--allow-download` 下载权重。训练结果包含模型、分词器、标签清单、指标和预测，保存在 `bert_base_cased_results/`，任务级汇总见 `bert_summary.md`。相同配置和完整产物默认跳过重训，可用 `--no-resume` 强制重训。
