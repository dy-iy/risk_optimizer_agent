# AGnews：Benchmark LF 迭代框架

本任务使用 [Benchmark 统一流程](../README.md)，任务标签、数据与文本语义在 [tools/task.py](tools/task.py) 中适配。

根据英文新闻的主要主题分类：国际/政治、体育、商业经济、科学技术。按主要报道主题判断，避免仅凭跨领域的公司名或人名分类。

标签：0=World；1=Sports；2=Business；3=Sci/Tech；-1=弃权。LF 统一为普通的 `def lf_name(text)`，输入仅为正文字符串；训练金标、ID 和数据来源均不传给 LF。
历史 `record["text"]` 规则已等价转换为字符串接口，规则的匹配逻辑与顺序保持原样。

## 数据与环境

沿用本地来源的冻结划分；仅离线读取 data，不访问 raw、不下载、不重新抽样。
[data/split.json](data/split.json) 固定来源、条数和 CSV 哈希：训练 **96000** / 验证 **12000** / 测试 **12000**。
训练 CSV 仅含 `id,text`，验证和测试 CSV 含 `id,text,label`。读取时校验 SHA-256、表头、条数、类别分布与 ID 唯一性。
CSV 弱标签沿用类别名 `0 / 1 / 2 / 3`，弃权为 `abstain`；概率列顺序为 `p_0`、`p_1`、`p_2`、`p_3`。

```powershell
# 从项目根目录安装任务依赖，包含 langgraph==1.1.10。
python -m pip install -r benchmark/AGnews/requirements.txt

# 离线核验现有划分。
python -B benchmark/AGnews/tools/data.py
```

追加迭代读取项目根目录 `.env` 中的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和可选 `DEEPSEEK_BASE_URL`；默认地址为 `https://api.deepseek.com`。
路径由任务目录定位，与启动工作目录无关。仅评估已有版本无需 API Key。

## 运行

```powershell
# 交互选择起始版本和追加轮数。
python -B benchmark/AGnews/orchestrator.py

# 评估已有 v1 的两种聚合器并更新迭代表，不调用 LLM。
python -B benchmark/AGnews/orchestrator.py --version v1 --iterations 0

# 从已有版本追加一轮，会调用配置的 API。
python -B benchmark/AGnews/orchestrator.py --version v1 --iterations 1

# 单版评估两种聚合器，不更新 iterations.csv。
python -B benchmark/AGnews/tools/runner.py --version v1

# 补记已有两版的修改影响，不执行 LF、不拟合模型、不调用 LLM。
python -B benchmark/AGnews/tools/impact.py --old-version v1 --new-version v2

# 绘制两种聚合器；也可指定 --aggregator Snorkel 或 simple_vote。
python -B benchmark/AGnews/picture/plot_metrics.py

# 分别用两种聚合器的训练硬弱标签训练 TF-IDF + LR。
python -B benchmark/AGnews/tools/train_logistic.py --version v1
```

也可以进入 `benchmark/AGnews/` 后运行 `python -B orchestrator.py --version v1 --iterations 0`。
影响记录需要两个不同且已评估的 LF 版本。追加候选从现有最大版本号加一。

## 编排与反馈

[orchestrator.py](orchestrator.py) 提供命令行和参数，[workflow.py](workflow.py) 实现 Benchmark 统一的 LangGraph 流程。
常用参数为每轮最多 5 项 LF 操作、五组样例各最多 30 条、抽样 seed 42、低覆盖阈值 0.8。

[tools/feedback.py](tools/feedback.py) 独立构造反馈，每轮调用 Analyzer 前保存到本轮候选版本的 `versions/vN/results/feedback.json`。
反馈标明 `aggregator=simple_vote`；验证指标、训练输出统计及样例预测统一取自简单投票，LF 投票矩阵和 LF 质量仍使用两种聚合器共享的数据，不包含测试信息。
连续拒绝时，反馈会附上此前被拒的方案和净收益变化。Analyzer 纠正时复用同一份反馈；仅评估（0 轮）不生成反馈文件。

1. 一次执行 LF，复用同一批投票评估 Snorkel 与 `simple_vote`。Snorkel 以训练投票拟合，CPU、500 epochs、seed 42；类别先验按完整固定验证集估计，按任务类别顺序排列。
2. 简单投票忽略弃权，各 LF 等权；仅唯一最多票类别输出标签，并列最多票或全弃权均弃权。概率列表示非弃权票占比；全弃权以均匀分布占位。
3. Analyzer 使用简单投票的完整验证反馈及无金标训练信息。验证错例、弃权、正确三组按简单投票预测划分；训练未覆盖、冲突两组继续按原始 LF 投票划分，冲突指至少出现两个不同的有效类别。Snorkel 继续独立评估和保存结果。
4. Analyzer 每轮只选一个 `add` / `modify` / `delete`，返回 `action,summary,operations`。严格解析 JSON 并集中校验；解析或业务校验失败，原 Analyzer 接收原回复及具体错误，最多纠正一次。
5. Patcher 一次只输出计划中的目标函数；本地按源码位置合并，保留其余 LF、常量和辅助代码。删除在本地完成，不调用 Patcher。AST 校验操作类型、函数目标与预算，删除后至少保留 3 个 LF。
6. 生成校验与候选执行共用一次 LF 技术修复额度。API/文件错误直接停止；每轮重置修复额度。成功后记录两种聚合器的影响，并用 Snorkel 的验证集净收益验收候选。默认要求提升严格大于 0.01，连续拒绝 3 次则停止。

候选拒绝后从保留版继续。可通过 `--acceptance-epsilon` 调整门槛，公式见 [Benchmark 验收规则](../README.md#候选验收)。

测试正文、标签、预测和指标不进入 Analyzer/Patcher，不用于选择操作或版本。
验证标签用于类别先验和开发反馈，计入监督预算；训练与测试金标均不参与拟合。
全量指标将弃权计为漏判，并同时报告已覆盖口径、每类 Precision/Recall/F1、Macro-F1 与混淆矩阵。
所有 LF 弃权时两种聚合器均输出弃权。

## 输出

已有训练和迭代记录保留原内容。新候选生成以下结果。

```text
versions/vN/
├── lfs.py
├── Snorkel/
│   ├── metrics.json / metrics.csv / report.md
│   ├── weak_labels.csv / snorkel_model.pkl
│   ├── impact.json / impact.md / impact_samples.csv
│   └── tfidf_lr_results/
├── simple_vote/
│   ├── metrics.json / metrics.csv / report.md / weak_labels.csv
│   ├── impact.json / impact.md / impact_samples.csv
│   └── tfidf_lr_results/
└── results/
    ├── lf_votes.csv / lf_quality.csv
    ├── feedback.json
    ├── analysis*.json / analysis*_raw.txt / patcher*.txt / changes.md / errors.json
    └── decision.json
```

`results/` 只保存一份共享投票及质量。新运行的反馈、分析、修改、评估和验收都归候选版本。
`iterations.csv` 在运行后按版本及聚合器各占一行；`tokens.csv` / `tokens.md` 记录实际调用用量。
绘图读取新布局，PNG/SVG 分别写入 `picture/Snorkel/` 和 `picture/simple_vote/`。

修改影响只比较训练与验证集，按 ID 和 LF 名称对齐；新增/删除 LF 的缺失票视为弃权。
记录覆盖变化、投票和预测变化、冲突、全部类别的概率变化，以及验证修复、退化和新增误标。
影响记录用于诊断，不送给 Analyzer/Patcher，不触发自动接受、回退或停机决策。

下游模型仅保留非弃权训练硬弱标签，要求包含全部任务类别。使用 **英文词 1–2 gram**、最多 100,000 特征、sublinear TF；
在 `C=[0.1,1,10]` 中按验证集 **Macro-F1** 选参，同分取第一个。二分类用 liblinear，四分类用 lbfgs，max_iter=2000、seed=42。
词表与系数仅拟合训练数据，完整预测验证与测试集，不弃权；结果和模型保存在各聚合器的 `tfidf_lr_results/`。
