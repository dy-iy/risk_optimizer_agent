# Financial PhraseBank：少量标注引导的 LF 迭代

复刻同级 SMS 的流程，适配英文金融情感三分类：固定少量标注反馈 → Analyzer 分析 → JSON 格式检查修复 Agent → 本地结构校验 → Patcher 小步修改 LF → 未标注数据拟合 Snorkel → 开发集与独立测试集评价 → 版本、Token 用量与图表记录。

更新后的格式检查流程从后续迭代生效。数据清理说明保留在本文后半部分。

## 启动

环境依赖见 [requirements.txt](requirements.txt)，当前环境已包含这些依赖。从项目根目录运行：

```powershell
# 与 SMS 一样，交互询问起始版本、追加轮数。
python -B 'tasks/financial_phrasebank/orchestrator.py'

# 只评价 v1，不调用 LLM。
python -B 'tasks/financial_phrasebank/orchestrator.py' --version v1 --iterations 0

# 从 v1 追加一轮，生成 v2；按需修改轮数。
python -B 'tasks/financial_phrasebank/orchestrator.py' --version v1 --iterations 1

# 单独运行某个版本，不更新迭代汇总。
python -B 'tasks/financial_phrasebank/tools/runner.py' --version v1

# 根据已有版本结果，生成 PNG 和 SVG 图表。
python -B 'tasks/financial_phrasebank/picture/plot_metrics.py'
```

追加迭代沿用项目根目录 `.env` 的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`。地址为空时使用 `https://api.deepseek.com`。零轮评价和绘图不调用 LLM。

## 数据与 LF

输入为本目录的 [financial_phrasebank.csv](financial_phrasebank.csv)，共 4,836 条。首次运行按 seed 42 划分，之后校验源文件及三个划分文件的 SHA256 并重用；文件变化或缺失时停止，不自动重抽。

| 集合 | 默认规则 | 用途 |
| --- | --- | --- |
| `data/labeled.csv` | 总计 100 条：负面 34、中性 33、正面 33 | 整个实验累计金标预算，供分析、改规则和开发评价 |
| `data/test.csv` | 先按类别及正文分组，抽约 20% | 每个版本只预测和报告，不进入 LLM 反馈或版本选择 |
| `data/unlabeled.csv` | 剩余数据，仅保存 id、text | LF 投票、覆盖/冲突统计和 Snorkel 拟合 |
| `data/split.json` | 保存种子、预算、条数和哈希 | 冻结数据划分 |

相同正文去除空白后整组分配，避免跨集合重复。实验使用全部一致性级别的清理后数据，不把四个互相包含的版本当作不同集合。

标签固定为 `-1=弃权、0=negative、1=neutral、2=positive`。[versions/v1/lfs.py](versions/v1/lfs.py) 包含六个手写冷启动 LF：业绩下降、亏损或预警、业绩增长、获得合同、公司介绍、例行披露，每类两个；未根据测试集调优。每个 LF 只接收单条正文字符串：

```python
def lf_name(text):
    # 根据正文规则返回 -1、0、1 或 2。
    return ABSTAIN
```

源数据的 `label_name`、`agreement_level`、`source_line` 不复制到划分文件，也不传给 LF 或 LLM。未标注集物理移除 `label` 列。`label_conflicts.csv` 不参与实验。

## 参数与输出

`orchestrator.py` 顶部配置：`MAX_LF_CHANGES = 5`；`MAX_EXAMPLES_PER_GROUP = 20` 仅控制未标注冲突样例；`MAX_UNCOVERED_GROUPS = 20`、`REPRESENTATIVES_PER_GROUP = 2` 控制未覆盖文本分组及代表句；`HISTORY_ROUNDS = 5` 控制近期历史；`FEEDBACK_SEED = 42`；`LOW_COVERAGE_THRESHOLD = 0.8` 控制覆盖优化优先级。固定标注集始终全部展示。划分参数在 `tools/data.py`，标签顺序在 `tools/task.py`。修改划分参数不会改变已有快照。

与 SMS 一致，Analyzer 在同一次调用中先在 `decision` 选择新增、修改或删除，再分析具体 LF，`operations` 必须全部属于选定类型。未标注集或标注集 LF 覆盖率严格低于 `LOW_COVERAGE_THRESHOLD` 时优先改善覆盖，必须在 `coverage_plan` 中说明覆盖缺口、改善方案及误标控制；允许扩展已有 LF 或新增独立模式，删除本身不会提升整体 LF 覆盖率，低覆盖时优先删除须解释理由和后续方向。反馈增加各类别的已覆盖/未覆盖数量。该阈值不是版本验收门槛；原有分组、历史反馈和提案字段继续保留。Patcher 及修复遵守决策和覆盖方案。

Analyzer 每轮输出后都调用一次独立的 JSON 格式检查修复 Agent，只接收 Analyzer 原文，修复括号、逗号、冒号、转义等语法。合法输入应原样返回；禁止重新分析或增删改操作、标签、样本编号及分析文本。本地严格解析修复结果，并校验内容保留：合法原文的结构和值必须一致；非法原文的全部字段名和标量值必须按原顺序保留。无法确认内容保留、修复后仍非法或业务结构校验失败时停止，不重复调用格式 Agent，也不补造缺失业务字段。每轮增加一次真实 API 调用，其用量单列为 `Analyzer JSON 格式检查修复`。

`analysis.json` 保存通过格式检查与业务校验的决策及覆盖上下文，`changes.md` 记录本轮决策。Analyzer 原文保存在被分析版本的 `results/analysis_raw.txt`，格式 Agent 原文另存为 `analysis_format_raw.txt`；合法 JSON 的业务结构校验失败时另存 `analysis_invalid.json`。每轮的输入、两份原文、成功分析或错误也独立保存在 `results/analysis_calls/<运行批次>/`，重跑不会覆盖旧批次。新版本的 `errors.json` 按实际失败阶段记录错误，格式修复失败不会覆盖 Analyzer 原文。

Analyzer 接收全部 100 条标注样本，按错误、弃权、正确分组；未标注的全部未覆盖正文仅在本地做数字归一化、TF-IDF 与最大组优先二分聚类，排除英文停用词、新闻套话、币种和数值单位。空特征文本单独成组，不再成为大主题组的代表；每个有效组给出一个中心句和一个兼顾差异性的代表句，同时给出组内平均余弦相似度。默认最多 20 组、每组 2 句，另附最多 10 条冲突样例。分组不使用金标，低相似度组仍可能混杂；组规模不等于某条候选规则的实际命中数。

反馈增加开发集/未标注集的每个 LF 独有覆盖、共同投票及冲突统计。近期 5 轮历史按实际代码 AST 差异提取 LF 增删改和有长度限制的差异片段，只附开发指标和未标注统计的变化；不读取历史报告或整个迭代表，也不传测试指标或未来版本。历史不完整的相邻版本对跳过。

反馈还提供 `unlabeled_previously_covered`：当前未覆盖、近期曾有 LF 投票的未标注文本总数及最多 10 个样例，附旧版票和当前票。仅从历史投票 CSV 的 `unlabeled` 行取值，不传测试行，不使用金标。旧票是覆盖回退的检查线索，不证明旧预测正确。Analyzer 在开发错误较少时优先考虑未标注覆盖缺口，并说明本轮模式与近期覆盖回退之间的优先级；没有新增自动淘汰或回退门槛。

每项 Analyzer 提案必须说明 `missing_pattern`（通用缺失模式）、`action_rationale`（为何新增/修改/删除）、`expected_labeled_changes`（已提供标注样本的目标 LF 预期投票）、`abstain_conditions`（弃权边界）和 `unlabeled_group_ids`（相关未覆盖组）。没有对应标注样本或分组时允许空数组，避免限制基于未标注模式的探索。新字段描述设计意图并校验引用合法性，不增加自动效果验收门槛，也不改版本推进方式。

Snorkel 使用三分类、CPU、500 epochs、seed 42、均匀先验 `[1/3, 1/3, 1/3]`，顺序为 negative、neutral、positive。该先验是默认假设，不代表真实数据类别均衡，也不会随 LF 投票自动学习。固定的 100 条标注按 34/33/33 配额抽取，不能像代表总体的随机验证集一样直接用于估计先验；因此保留均匀假设，不从完整源数据或测试金标取类别比例。省略 `class_balance` 也会让 Snorkel 回退到均匀先验。若要校正，需另有符合实验预算、能代表目标分布的标注样本或独立先验依据。

只在未标注投票矩阵上拟合，不用任何金标估计先验；标注集和测试集复用同一个已拟合模型。结果在 `metrics.json` 中记录 `class_balance_source=uniform_assumption`，报告同时展示先验及其限制。全量指标把弃权计入真实类别的漏判；所有 LF 弃权时，最终输出也弃权。

| 路径 | 内容 |
| --- | --- |
| `tools/analyzer.py` / `tools/patcher.py` | 完整开发反馈、按通用模式分析、LF 生成及 AST 改动校验 |
| `tools/format_checker.py` | Analyzer 输出的 JSON 格式检查修复 Agent、严格解析与内容保留校验 |
| `tools/feedback.py` | 未覆盖文本分组、LF 独有/重叠覆盖、仅开发信息的近期历史 |
| `tools/data.py` / `tools/task.py` | 固定划分、完整性校验、统一标签映射 |
| `tools/runner.py` / `tools/label_model.py` / `tools/evaluate.py` | LF 投票、三分类聚合、含弃权评价和报告 |
| `tools/llm.py` | DeepSeek 调用、每次请求的 Token 记录与汇总 |
| `versions/vN/results/` | `metrics.json/csv`、`report.md`、`lf_votes.csv`、`weak_labels.csv`、`lf_quality.csv`、`snorkel_model.pkl` |
| `versions/vN/results/analysis.json` | 对 vN 的分析，生成下一版时写入 |
| `versions/vN/results/analysis_raw.txt` / `analysis_format_raw.txt` | Analyzer 与格式检查修复 Agent 的原始回复 |
| `versions/vN/results/analysis_calls/<运行批次>/` | 按批次保留输入、原文、成功分析或错误 |
| `versions/vN/results/feedback.json` | 生成下一版时实际发给 Analyzer 的结构化输入快照；含固定预算内的金标，不含测试信息 |
| 新版本的 `results/changes.md` / `errors.json` | 实际 LF 改动；技术错误发生时保留错误记录 |
| `iterations.csv` | 每个成功版本一行，开发与测试指标分列，包含三类 Precision/Recall/F1 |
| `tokens.csv` / `tokens.md` | 实际 API 调用的 Token 明细和本次/历史累计，未知用量不记为 0 |
| `picture/plot_metrics.py` | 指标总览、三类指标、预测构成、逐轮变化、投票与聚合方向对比 |

`weak_labels.csv` 的概率列依次为 `p_negative`、`p_neutral`、`p_positive`。从指定的 vN 写入 vN+1，以最新可运行版本继续；与 SMS 一致，从旧版重新追加会覆盖对应的后续版本。只有一个版本时不生成逐轮变化图。详细流程见 [迭代框架.md](迭代框架.md)。

## 数据清理记录

主文件：`financial_phrasebank.csv`，共 **4836** 条英文金融情感样本。`label_conflicts.csv` 保留从主数据中排除的 2 个标签冲突句子，共 4 条候选标签记录。

## 来源与处理

清理时的原始来源位于旧目录 `data/benchmarks/financial_phrasebank/raw/FinancialPhraseBank-v1.0/`；整理后该原始目录不再随当前仓库提供。当前使用本任务目录的 `financial_phrasebank.csv` 及 `data/` 中的冻结划分，以下保留原始处理记录。

| 原始版本 | 原始行数 | 去重后的文本与标签组合数 |
| --- | ---: | ---: |
| Sentences_50Agree.txt | 4846 | 4840 |
| Sentences_66Agree.txt | 4217 | 4211 |
| Sentences_75Agree.txt | 3453 | 3448 |
| Sentences_AllAgree.txt | 2264 | 2259 |

1. 使用 Latin-1 严格解码原始文本，再输出为 UTF-8 CSV（无 BOM，逗号分隔，CRLF 换行）。
2. 从每行最后一个 `@` 分离句子和情感标签，校验非空文本及三类合法标签。
3. 文本做 Unicode NFC 规范化、去除首尾空白、将连续空白合并为一个空格。四个文件中有 104 行发生此类空白或规范化调整。保留大小写、标点、数字与原始分词间距，不猜测修复原始语料中的人名乱码或拼写问题。
4. 四个一致性版本互相包含，50Agree 覆盖全部文本与标签组合。按规范化后的文本及标签合并重复记录，不将四个版本当作互斥数据集拼接。50Agree 的 4846 行去掉 6 条同标签重复记录后得到 4840 个文本与标签组合。
5. 其中 2 个句子各自同时有 neutral 和 positive 标签。两个句子均整体排除，4 个候选标签记录保存在 `label_conflicts.csv`，不自动裁定标签。最终主数据为 4836 条，每个文本唯一。
6. 按 50Agree 中首次出现的顺序输出；未划分训练、验证或测试集。

## 主 CSV 字段

| 字段 | 含义 |
| --- | --- |
| id | 清理结果中从 1 开始的连续编号 |
| text | 清理后的英文句子 |
| label | 整数标签：0=negative，1=neutral，2=positive，与上游数据卡映射一致 |
| label_name | 原始英文情感标签 |
| agreement_level | 该文本及标签所属的最高一致性版本：50、66、75、100；是版本级别，不是逐句精确投票比例 |
| source_line | 首次出现在 Sentences_50Agree.txt 中的行号，从 1 开始 |

`label_conflicts.csv` 中的 `source_rows` 是 JSON 数组，保存该候选标签在四个原始文件中的全部文件名及行号。

## 清理后统计

| 标签 | 编码 | 样本数 |
| --- | ---: | ---: |
| negative | 0 | 604 |
| neutral | 1 | 2871 |
| positive | 2 | 1361 |
| 合计 | | 4836 |

| 最高一致性版本 | 仅该级别样本数 | 该级别及以上样本数 |
| --- | ---: | ---: |
| 50 | 627 | 4836 |
| 66 | 761 | 4209 |
| 75 | 1189 | 3448 |
| 100 | 2259 | 2259 |

例如，筛选 `agreement_level >= 75` 可得到清理后的 75Agree 子集，`agreement_level == 100` 可得到清理后的 AllAgree 子集。不同一致性子集存在重叠，不能直接分别充当训练集与测试集。

## 许可与引用

原始处理记录注明数据许可为 CC BY-NC-SA 3.0。原始 `License.txt`、`README.txt` 和 `UPSTREAM_README.md` 未包含在当前目录中，重新分发时需同时保留上游许可和引用说明。

Malo, P., Sinha, A., Korhonen, P., Wallenius, J., and Takala, P. (2014). Good debt or bad debt: Detecting semantic orientations in economic texts. Journal of the Association for Information Science and Technology, 65.
