# EDOS+Cluster：由样本簇驱动的 LF 自动修复

EDOS Task A 二分类：`0=not sexist`、`1=sexist`、`-1=abstain`。LF 接口为普通的 `def lf_name(text)`，只读取正文。训练金标、样本 ID、Embedding 和簇编号均不进入 LF。

## 运行

使用实际实验环境的 Python；本机为 `D:/Miniconda/python.exe`。依赖包含 PyTorch、scikit-learn、Sentence-Transformers 和 LangGraph。

```powershell
& D:/Miniconda/python.exe -m pip install -r 'EDOS+Cluster/requirements.txt'

# 新建独立实验，只评估起始 LF；不调用 LLM、不加载句向量模型、不评估测试集。
& D:/Miniconda/python.exe -B 'EDOS+Cluster/orchestrator.py' --version v1 --iterations 0 --skip-final-test

# 聚类结构化反馈，每轮一个候选；最后只对选定版本评估测试集。
& D:/Miniconda/python.exe -B 'EDOS+Cluster/orchestrator.py' --version v1 --iterations 5

# 同簇三个独立候选，选择集验收后最多接受一个。
& D:/Miniconda/python.exe -B 'EDOS+Cluster/orchestrator.py' --version v1 --iterations 5 --candidates 3

# 只改变反馈模式，统一 seed、候选数、轮数和验收配置。
& D:/Miniconda/python.exe -B 'EDOS+Cluster/orchestrator.py' --version v1 --iterations 5 --mode random --skip-final-test
& D:/Miniconda/python.exe -B 'EDOS+Cluster/orchestrator.py' --version v1 --iterations 5 --mode cluster_random --skip-final-test
& D:/Miniconda/python.exe -B 'EDOS+Cluster/orchestrator.py' --version v1 --iterations 5 --mode cluster_diverse --skip-final-test
```

`--run-dir` 指定新输出目录，已存在时拒绝覆盖。默认写入 `runs/<mode>-<时间>-<随机编号>/`；起始 LF 从现有版本复制，历史版本和历史统计不变。当前不提供自动恢复或重放中断请求；中断状态和真实调用用量保留在原运行中。

追加迭代从项目根 `.env` 读取 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和可选 `DEEPSEEK_BASE_URL`。仅评估不需要密钥。`--acceptance off` 提供单候选、可执行即推进的工程对照；该模式只允许 `--candidates 1`。默认开启实际收益验收。

## 数据隔离

冻结划分为训练 14,000 / 验证 2,000 / 测试 4,000，沿用现有 CSV 与 SHA-256 校验，不读取 `raw/`。训练文件只有 `id,text`。

每次新实验以相同 seed 和比例生成固定开发角色，保存为 `development_roles.json`。默认 seed 42，验证集内分层划分发现集 1,500 / 选择集 500；规范化后相同正文整体分组，避免跨角色重复。

- `valid` 在新实验中明确表示规则发现角色。Analyzer 指标、金标样例、LF 质量及 Snorkel 类别先验全部仅来自该角色。
- `selection` 只由本地候选验收和后续下游模型选参使用；其正文、ID、金标、指标和拒绝原因均不送入模型提示词。
- 开发轮次不读取或运行测试样本。全部候选选择结束后，在单独的 `final/` 中评估最终版本；测试错误不反馈给 Patcher。`--skip-final-test` 可暂缓这一步。

开发角色仍属于自适应使用的开发数据，多轮验收可能过拟合；历史完整验证集已用于旧实验，该划分不代表历史未见数据。正式消融应从相同冷启动 LF 开始。

## 策略和聚类

覆盖阈值 0.80、已覆盖准确率阈值 0.90，集中在 `orchestrator.py`。`C=min(训练输出覆盖率,发现集输出覆盖率)`，`Q=发现集已输出样本准确率`。

| 状态 | 允许操作 |
| --- | --- |
| 低覆盖、高准确率 | add |
| 低覆盖、低准确率 | modify |
| 高覆盖、低准确率 | modify |
| 高覆盖、高准确率 | add 或 modify |

无输出时 Q 为 null，保守建立覆盖；双操作状态仍有覆盖缺口时选择 add。add 每轮一个案例组、一条 LF，modify 每轮最多 5 条 LF，每组样例最多 30 条。delete/stop 不属于 Analyzer 操作。

`all_abstain` 为所有 LF 弃权；`tied` 为有有效票但 simple_vote 平票。仅 `all_abstain` 进入新增规则聚类池。modify 继续使用发现错例、发现正确例和训练冲突例。

默认模型 `sentence-transformers/all-mpnet-base-v2`，固定 revision `e8c3b32edf5434bc2275fc9bab85f82640a19130`，768 维，最大 384 tokens，batch 64，设备自动优先 CUDA。`--device` 可指定设备；`--local-models-only` 禁止下载。

首次运行在工作区 `cache/embeddings/models/` 下载普通目录快照；按模型 revision 保存，不依赖 Windows 符号链接。向量以规范化 float32 缓存在 `cache/embeddings/`，身份包含完整文本和 ID 顺序、模型/revision、编码配置及相关库版本，记录截断数量。缓存是可复用实验资产。

每个 add 轮次：

1. 按当前 LF 结果更新未覆盖集合；对发现集分别按真实标签聚类。
2. 使用 average-link 层次聚类、余弦距离、`distance_threshold=0.55`，无需预设 K。小于 4 条独立正文的簇暂缓处理。该默认值经发现集粒度诊断调整：冷启动时 0.35 仅有 1 个合格簇，0.55 有 31 个，两个标签的簇中心凝聚度中位数约 0.78/0.80；未使用选择或测试指标调节。
3. 归一化簇中心检索当前训练全弃权样本，初始相似阈值 0.65。训练支持是潜在规模，不是 LF 命中或标签正确性证据；不同簇支持可能重叠，不能累加为总体收益。
4. 每类内部按 `log1p(发现金标支持数) × log1p(训练相似支持数) × 凝聚度` 排序；类间优先尝试次数较少的标签，同分按标签编号调度。
5. 选择一个尚未尝试的簇。簇 ID 由标签与成员 ID 哈希生成；未接受且成员不变的簇不反复送给模型。簇不足时记录 `no_eligible_cluster` 并消耗一个跳过轮次，不强制生成规则。

Embedding 每轮复用；层次聚类只对剩余发现样本重新运行，训练集只做向量检索。所有配置和阈值都由总控集中传入。

## 三种反馈模式

| 模式 | 目标标签金标案例 |
| --- | --- |
| random | 从同标签当前全弃权池随机选最多 6 条，不按语义成簇 |
| cluster_random | 从选中簇随机选最多 6 条 |
| cluster_diverse | 最多 2 条中心、3 条多样性、1 条边界 |

三种模式使用同一标签调度逻辑，并采用相同反例检索算法和数量预算：最多 3 条异标签发现集近邻、3 条无标签训练支持。cluster_random 与 cluster_diverse 共用簇构建和价值排序。不足时记录真实数量，不补齐或重复。random 案例组的中心与支持只针对其抽中的样本，不表示发现了语义簇。

多样性案例逐步最大化与已选案例的差异；边界案例按簇中心相似度与最近异标签相似度之差排序。反例从完整发现角色的另一标签中检索，包括已覆盖样本。同标签聚类不能用天然 100% 的标签纯度证明规则可靠。

Analyzer 接收 `cluster` 的完整案例组，归纳共同可实现机制及弃权边界。输出保持 `action,operations`，每项为 `target,evidence,plan`。add 的 evidence 至少引用两条不同的 `[gold:真实ID]`，存在反例时至少引用一条 `[negative:真实ID]`；训练引用为 `[train:真实ID]`。本地校验引用是否存在且来源匹配。

Patcher 接收同一案例组，只生成指定函数；局部源码合并、AST 预算校验与实际 LF 运行检查继续生效。禁止整句查表、单例条件拼接和无依据默认标签。

## 独立候选和实际收益验收

`--candidates` 为 1～3，默认 1。每个候选从同一个已接受基线生成，分别拥有 LF 文件、分析原文、代码、投票和评估结果；先前方案只用于提示后续方案采用不同机制，选择集结果不回流给模型。

Analyzer 解析/业务校验允许一次纠正。Patcher 生成和 LF 执行共用一次技术修复额度；技术修复耗尽则该候选失败，继续其他候选。API/文件错误立即停止并记录中断。每候选通常 2 次 LLM 调用、最多 4 次，所有真实用量写入运行 Token 表。

默认以 simple_vote 的选择集结果验收：正确输出净增至少 1、原正确退化最多 0、新增误标最多 2、Macro-F1 不下降、已覆盖准确率下降不超过 0.005。add 还要求原全弃权样本中新增正确输出至少 1，训练实际输出数增加至少 1。阈值集中在总控；这些是初始实验设置，不是泛化保证。

通过者按正确输出净增、Macro-F1 增量、更少新增误标、训练输出增量排序，同分选先生成的候选。每轮最多接受一个；全部失败或未通过则保留基线。Snorkel 使用同一批 LF 投票独立评估，不参与验收。

影响记录区分原全弃权转正确/转错误、原平票解决、已有错例修正、原正确转错/转弃权。实际输出收益与潜在语义支持分别记录。

## 输出与查看

```text
runs/<run>/
├── run_config.json / run_state.json / development_roles.json
├── rounds.json / rounds.csv / iterations.csv / tokens.csv / tokens.md
├── embedding_cache.json
├── versions/vN/                  # 起始及已接受版本
│   ├── lfs.py
│   ├── results/lf_votes.csv / lf_quality.csv / changes.md ...
│   ├── simple_vote/metrics.json / report.md / weak_labels.csv / impact.*
│   └── Snorkel/...
├── rounds/r001/
│   ├── feedback.json / clusters.json / selection.json
│   └── candidate_01/versions/vN/ # 每个独立候选，含精确反馈及验收记录
│       └── results/feedback.json / analysis*.json / patcher*.txt / acceptance.json ...
└── final/versions/vN/            # 最终测试报告，不进入后续反馈
```

仅 modify 的轮次没有 `clusters.json`；跳过轮次没有模型反馈；只有加载向量后才生成 `embedding_cache.json`。`rounds.csv` 保留被拒绝/跳过轮次的基线水平段；`iterations.csv` 只保存起点和已接受版本。配置记录数据/角色/起始 LF 身份、LLM 名称、预算和阈值，密钥不写入日志。

```powershell
# 将 <run>、版本号替换为实际运行输出。
& D:/Miniconda/python.exe -B 'EDOS+Cluster/picture/plot_metrics.py' --run-dir 'EDOS+Cluster/runs/<run>'
& D:/Miniconda/python.exe -B 'EDOS+Cluster/picture/plot_metrics.py' --compare-runs 'EDOS+Cluster/runs/<random>' 'EDOS+Cluster/runs/<cluster_random>' 'EDOS+Cluster/runs/<cluster_diverse>'
& D:/Miniconda/python.exe -B 'EDOS+Cluster/tools/impact.py' --run-dir 'EDOS+Cluster/runs/<run>' --old-version v1 --new-version v2
& D:/Miniconda/python.exe -B 'EDOS+Cluster/tools/train_logistic.py' --run-dir 'EDOS+Cluster/runs/<run>' --version v2
```

新运行绘图展示训练输出覆盖、发现集输出覆盖、已覆盖准确率、发现/选择 Macro-F1 和新增正确/错误输出；比较入口检查除反馈模式外的配置及预算是否相同。实际 Token 和调用数仍需与结果一起报告。

下游 TF-IDF + LR 仅拟合非弃权训练弱标签，词表不使用开发或测试正文；新运行按选择集 sexist F1 从 `C=[0.1,1,10]` 选参，再报告发现、选择和最终测试指标。该脚本只接受运行目录 `versions/` 中的版本，不使用被拒绝候选。
