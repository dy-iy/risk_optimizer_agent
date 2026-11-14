# FinNSP：固定验证集引导的 LF 迭代

迭代主体沿用同级 [SMS](../SMS/)，JSON 格式检查修复阶段复刻 [financial_phrasebank](../financial_phrasebank/)，保留 FinNSP 的文本与候选实体输入，以及 0/1 任务语义。数据已按确认方案替换为 **训练 3499 / 验证 500 / 测试 500**。

## 数据来源与划分

使用官方仓库固定提交 `3b686ca79e2184b36498ba15fd1e9609d9eda3cb` 的两个文件：

- [train_list.json](https://github.com/supersymmetry-technologies/BBT-FinCUGE-Applications/blob/3b686ca79e2184b36498ba15fd1e9609d9eda3cb/FinCUGE_Publish/finnsp/train_list.json)：4000 行中包含 1 行混入表头，剔除后有 3999 条有效样本。
- [eval_list.json](https://github.com/supersymmetry-technologies/BBT-FinCUGE-Applications/blob/3b686ca79e2184b36498ba15fd1e9609d9eda3cb/FinCUGE_Publish/finnsp/eval_list.json)：官方 dev 的全部 500 条，直接作为本实验测试集。

| 集合 | 条数 | 字段 | 来源与用途 |
| --- | ---: | --- | --- |
| [data/train.csv](data/train.csv) | 3499 | id,text,inputs | 官方 train 剩余样本；运行 LF、拟合 Snorkel、生成弱标签，物理移除金标和负面主体 |
| [data/valid.csv](data/valid.csv) | 500 | id,text,inputs,label | 从官方 train 固定分层抽取；类别先验、完整开发指标、反馈样例和 LF 质量 |
| [data/test.csv](data/test.csv) | 500 | id,text,inputs,label | 官方 dev 全量；每个版本只预测、评价和报告 |

验证集使用 `sklearn.model_selection.train_test_split`，`test_size=500`、`random_state=42`，按 0/1 标签分层抽取。训练源金标仅用于这次分层分配，随后从训练 CSV 中物理移除，不参与 Snorkel 拟合。三个集合都保持各自源文件中的顺序；测试集不参与拆分。

验证集类别数为 **0 类 222 / 1 类 278**，测试集为 **0 类 241 / 1 类 259**。类别先验按验证集估计为 `[0.444, 0.556]`，类别顺序为 `[0, 1]`。

仅剔除训练源中索引 1939（从 0 起）的完整表头行 `[BOM+id, title, text, entity, negative, key_entity]`，不补造样本。空候选实体串转为 `[]`，相关样本保留；重复候选和候选顺序保留。正文不修改，不额外去重或按正文分组；不同编号的相同正文可能跨集合。ID 为 `finnsp_<split>_<官方编号>`，原编号与各集合归属保存在清单中。

[data/split.json](data/split.json) 冻结官方提交、源文件地址与 SHA-256、剔除表头、分层种子、条数、验证/测试类别数、样本归属以及 CSV 的 SHA-256。后续启动离线核验重用；缺失或变化直接报错，不静默重分。核查摘要见 [data/source_check.json](data/source_check.json)。

这是基于官方 train/dev 的自定义实验划分，不是官方发布的 4800/600/600 划分。无标签的官方 `problem_list.json` 不使用。旧 `finnsp.csv`、`labeled.csv`、`unlabeled.csv` 已删除。

## 导入与校验

CSV 已生成，正常启动不会下载或重新抽样。仅当三个 CSV 和清单均不存在时自动导入；需要显式重新导入时执行：

```powershell
python -B 'tasks/Finnsp/tools/data.py'
```

离线重新导入可同时传入 `--train` 和 `--dev`，文件必须是上述固定官方版本的原文件，且通过内置 SHA-256 校验。

## 任务与 LF 输入

0 表示没有针对候选实体的负面消息，1 表示至少一个候选实体存在负面消息，-1 表示弃权。本任务不输出负面主体名单；一般负面语气或针对候选列表之外实体的风险不能直接判为 1。

每个 LF 是 `def lf_name(record)`，只接收 `record['text']` 与 `record['inputs']['entities']`。执行器为每个 LF 单独复制输入，不传入编号、金标、metadata、来源或负面主体。官方空候选列表保留，现有 LF 在没有候选实体时弃权。

以 [versions/v1/lfs.py](versions/v1/lfs.py) 的六个初始 LF 为基线。当前保留该固定划分下的 v1–v10 及结果；后续启动会更新对应结果与迭代记录，失败时也保留原文和报错。新划分不能消除历史实验对样本的既有接触。

## 与 SMS 对齐的迭代流程

1. 运行指定起点版本，生成基线。
2. Analyzer 在同一次调用中先选择唯一操作类型 add/modify/delete，再输出具体分析和操作建议。
3. 每轮将 Analyzer 原文交给独立的 JSON 格式检查修复 Agent，调用一次，仅修复括号、逗号、冒号、转义等语法；已合法的 JSON 也经过这一步。只提供原文，不提供完整开发反馈，禁止改写分析或操作建议。
4. 本地严格解析修复结果，检查字段与内容保留，再执行原有业务结构校验；通过后，Patcher 输出完整 `lfs.py`，通过 AST 校验实际操作与建议一致，至少保留 3 个 LF。
5. 每版只在训练投票上拟合一次 Snorkel，CPU、500 epochs、seed 42。类别先验从固定验证集的 0/1 比例估计，同一个模型预测验证和测试投票；训练金标和测试金标不参与拟合或先验估计。
6. 保存覆盖率、全量/已覆盖指标、每类 Precision/Recall/F1、Macro-F1、混淆矩阵、训练冲突率和 LF 质量，以最新可运行版本继续。
7. LF 技术错误最多额外修复一次，修复沿用同一操作决策；JSON 格式修复失败、改写内容或业务结构不合格则直接停止，不重复调用格式 Agent，不补造缺失字段。不设质量淘汰门槛，不因指标下降回退。数据来源错误直接停止，不交给 Patcher 修复。

格式修复只允许改变 JSON 语法。对合法原文，修复后结构和值必须一致；对非法原文，全部字段名和标量值及其顺序必须保留。重复字段、NaN 等非 JSON 数值、丢弃后续 `operations` 或改写内容均被拒绝。实现见 [tools/format_checker.py](tools/format_checker.py)。

后续每次分析保存 `feedback.json`、Analyzer 原文 `analysis_raw.txt`、格式 Agent 原文 `analysis_format_raw.txt`，以及通过校验的 `analysis.json`；合法 JSON 的业务校验失败时另存 `analysis_invalid.json`。它们同时按运行批次保存在 `versions/vN/results/analysis_calls/<运行批次>/`，重跑同一版本时保留不同批次。错误记录区分 `Analyzer` 与 `Analyzer JSON 格式检查修复` 阶段，格式修复失败不会覆盖 Analyzer 原文。

当前常用参数如下（以代码为准）：

| 参数 | 值 |
| --- | ---: |
| MAX_LF_CHANGES | 5 |
| MAX_EXAMPLES_PER_GROUP | 30 |
| FEEDBACK_SEED | 42 |
| LOW_COVERAGE_THRESHOLD | 0.8 |

五组反馈分别是验证错例、验证弃权、验证正确、训练未覆盖和训练冲突，按版本和种子可复现抽样，每组最多 30 条。完整指标始终使用全部 500 条验证数据，训练样例没有金标。训练或验证 LF 覆盖率低于阈值时，Analyzer 必须给出覆盖改善和误标控制方案。

测试样本、标签、预测和指标不进入 Analyzer/Patcher，不用于选版本、调参数或决定轮数。验证金标通过类别先验参与拟合，因此结果明确记录 `gold_used_for_fit=true`、`validation_gold_usage=class_balance_only`，同时记录训练/测试金标未参与。

## 运行与产物

从项目根目录执行：

```powershell
# 交互询问起点与追加轮数。
python -B 'tasks/Finnsp/orchestrator.py'

# 只评价基线，不调用 LLM。
python -B 'tasks/Finnsp/orchestrator.py' --version v1 --iterations 0

# 从 v1 追加一轮。
python -B 'tasks/Finnsp/orchestrator.py' --version v1 --iterations 1

# 单版评价；绘制同一划分上的结果。
python -B 'tasks/Finnsp/tools/runner.py' --version v1
python -B 'tasks/Finnsp/picture/plot_metrics.py'
```

追加迭代沿用项目根目录 `.env` 的 DEEPSEEK_API_KEY、DEEPSEEK_MODEL、DEEPSEEK_BASE_URL；地址为空时默认 https://api.deepseek.com。每轮格式检查修复新增一次真实 API 调用，与 Analyzer、Patcher、Patcher 修复共用模型配置；Token 明细保留原 CSV 表头，按 `Analyzer JSON 格式检查修复` 单列阶段，终端和 `tokens.md` 增加 JSON 检查修复用量列。本地校验不调用付费模型，也不写入真实调用记录。

`versions/vN/results/` 保存指标、报告、投票、弱标签、LF 质量和 Snorkel 模型；分析归当前版本，改动归下一版。`iterations.csv` 分列记录验证和测试指标、训练统计与类别先验，自动排除其他数据划分的旧指标；绘图也只读取与当前清单一致的结果。

## 逻辑回归下游模型

已为 v4、v6、v8、v10 分别训练 TF-IDF + Logistic Regression，结果见 [对比汇总](downstream_lr_summary.md) 和 [CSV](downstream_lr_summary.csv)。四个模型各自保存在 `versions/vN/tfidf_lr_results/model.joblib`，不改写 LF 聚合结果。

参考 `SMS/versions/v15/train_logistic.py`：仅保留当前版本训练集中的非弃权硬弱标签，不使用 `p_0/p_1` 软标签；C 网格为 `[0.1, 1, 10]`，按验证集 1 类 F1 选择，同分取网格中第一个。采用 `liblinear`、`max_iter=2000`、`seed=42`，以 argmax 预测完整的 500 条测试数据，不弃权。TF-IDF 词表与 IDF、逻辑回归系数只拟合本版非弃权训练样本；下游验证标签仅选 C，测试标签仅作最终评价。

FinNSP 的中文与实体输入适配：TF-IDF 使用字符 1–2 gram，`max_features=100000`、`sublinear_tf=True`、`lowercase=True`；输入序列化为正文与候选实体列表，不包含编号、标签或概率。上游 Snorkel 已使用验证类别比例作为先验，相关来源保存在各版 `data_summary.json` 中。

共用实现为 [tools/train_logistic.py](tools/train_logistic.py)，各版本有自己的 `train_logistic.py` 入口。重新训练需要指定新的输出目录，已有非空结果目录不会被覆盖，例如：

```powershell
python -B 'tasks/Finnsp/versions/v4/train_logistic.py' --output-dir 'tasks/Finnsp/versions/v4/tfidf_lr_rerun'

# 只校验某版本输入，不训练、不写文件。
python -B 'tasks/Finnsp/tools/train_logistic.py' --version v6 --check-data
```

每版保存清洗训练集、验证/测试预测、完整分类指标、C 搜索历史、输入哈希、配置、模型和报告。本次沿用冻结划分，包含先前发现的 1 条训练—测试相同输入，以及 1 对正文去空白后相同但候选实体不同的训练—验证记录；没有额外去重或重分，详细数量记录在 `data_summary.json` 的 `overlap_audit` 中。
