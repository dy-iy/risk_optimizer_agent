# Risk Optimizer Agent｜LLM 引导的弱监督规则迭代

本仓库通过“LF 投票 → Snorkel 聚合 → 验证反馈 → LLM 分析 → 修改 LF → 下一版评估”迭代文本分类规则，并保存弱标签、评估报告、Token 用量和下游模型。当前按任务组织在 `tasks/` 下。

## 目录导航

| 路径 | 内容 |
| --- | --- |
| [tasks/AGnews](tasks/AGnews/README.md) | 英文新闻主题四分类：World / Sports / Business / Sci/Tech |
| [tasks/CHECKED](tasks/CHECKED/README.md) | 中文新闻真实性二分类：real / fake |
| [tasks/EDOS](tasks/EDOS/README.md) | 英文性别歧视二分类：not sexist / sexist |
| [tasks/financial_phrasebank](tasks/financial_phrasebank/README.md) | 英文金融情感三分类：negative / neutral / positive，固定 100 条标注预算 |
| [tasks/Finnsp](tasks/Finnsp/README.md) | 中文金融负面消息二分类，输入正文和候选实体 |
| [tasks/IMDb](tasks/IMDb/README.md) | 英文电影评论情感二分类 |
| [tasks/SMS](tasks/SMS/README.md) | 短信垃圾信息二分类：ham / spam |
| [tasks/Yelp](tasks/Yelp/README.md) | 英文评论情感二分类 |
| [tasks/YouTube](tasks/YouTube/README.md) | YouTube 评论垃圾信息二分类：ham / spam |
| [tasks/report](tasks/report/README.md) | 跨任务 Global Acc 图表和下游逻辑回归汇总 |
| [指标笔记.md](指标笔记.md) | 评估指标笔记 |
| [requirements.txt](requirements.txt) | 当前任务共用依赖，各任务依赖文件引用此文件 |
| [.env.example](.env.example) | LLM 配置模板 |

任务目录通常包含：

```text
tasks/<任务>/
├── orchestrator.py        # 评价起点、调用 LLM、生成后续版本
├── requirements.txt       # 引用根目录共用依赖
├── tools/                 # 数据、LF 执行、聚合、评估、分析与修改
├── data/                  # 固定 CSV 划分和 split.json
├── raw/                   # 部分任务保留的来源归档
├── versions/vN/
│   ├── lfs.py             # 当前版本的标签函数
│   ├── results/           # 指标、投票、弱标签、分析、模型和错误记录
│   └── tfidf_lr_results/  # 下游模型结果（部分版本）
├── picture/               # 绘图脚本及 PNG / SVG
├── iterations.csv         # 已完成版本的指标汇总（运行后生成）
└── tokens.csv / tokens.md # 真实 API 调用用量（运行后生成）
```

`versions/` 由 `.gitignore` 排除，包含 LF 源码和实验产物；只克隆 Git 仓库不会自动获得这些本地文件，交接时需单独保留。目录存在不代表评估成功，应检查 `results/metrics.json` 和 `errors.json`。较大版本号也不代表效果更好。

## 环境与依赖

从项目根目录、已安装 PyTorch / scikit-learn 的 Python 环境中执行：

```powershell
python -m pip install -r requirements.txt
```

下文的 `python` 必须指向实际实验环境；例如本机可用 `& D:/Miniconda/python.exe` 替换。系统默认 Python 可能属于另一个环境。当前 Snorkel LabelModel 使用 CPU；单独的 BERT 训练脚本可使用 CUDA，不属于核心依赖。

各任务的 `requirements.txt` 引用根目录依赖，也可按任务安装，例如 `python -m pip install -r tasks/IMDb/requirements.txt`。

## 离线校验与评价

从根目录直接执行脚本，以 IMDb 为例：

```powershell
# 核验固定数据的哈希、字段、条数和标签，不训练、不调用 LLM。
python -B tasks/IMDb/tools/data.py

# 评价已有 v1，写入该版本 results/，更新 iterations.csv，不调用 LLM。
python -B tasks/IMDb/orchestrator.py --version v1 --iterations 0

# 单独评价某版，不更新 iterations.csv。
python -B tasks/IMDb/tools/runner.py --version v10

# 从已有结果绘图。
python -B tasks/IMDb/picture/plot_metrics.py
```

多数任务支持 `--version` 和 `--iterations`；SMS 的 orchestrator 使用交互输入，单版评价可用 `python -B tasks/SMS/tools/runner.py --version v1`。请使用实际存在且包含 `lfs.py` 的版本。重跑评价会更新该版本结果；从旧版本继续迭代可能覆盖已有后续版本，需先备份相应目录。

各任务 `data/split.json` 固定来源、划分与哈希，正常运行复用现有数据，不因迁移目录重新抽样。数据缺失或变化应先核对清单；部分任务在整套数据均不存在时会触发首次导入。Financial PhraseBank 使用 `labeled / unlabeled / test`，其余任务使用 `train / valid / test`。

## LLM 自动迭代

首次配置时，将 `.env.example` 复制为根目录 `.env`，填写：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=实际可用的模型名
```

已有 `.env` 可直接复用。任务从 `tasks/<任务>/` 定位根目录配置；离线评价不需要 API Key。追加迭代示例：

```powershell
# 不带参数时交互询问起点和轮数。
python -B tasks/IMDb/orchestrator.py

# 从已有 v10 追加一轮，调用配置的 API 并产生费用。
python -B tasks/IMDb/orchestrator.py --version v10 --iterations 1
```

Analyzer 根据开发反馈选择增、改、删 LF，Patcher 输出候选规则并经过本地 AST 和执行校验。部分任务另有 JSON 格式检查 Agent。每轮操作数、反馈预算、类别先验和错误处理存在任务差异，以各任务 README 和 `orchestrator.py` 为准。

## 报告与下游模型

- 单版指标、报告、投票、弱标签和 Snorkel 模型位于 `tasks/<任务>/versions/vN/results/`。
- [Global Acc 汇总](tasks/report/README.md) 展示 SMS、YouTube、Finnsp、Financial PhraseBank 的已评估版本；重建命令为 `python -B tasks/report/plot_global_acc.py`。
- [四任务下游 LR 汇总](tasks/report/downstream_lr_summary.md) 覆盖 Yelp、IMDb、CHECKED、AGnews；[Finnsp 下游汇总](tasks/Finnsp/downstream_lr_summary.md) 单独保留。
- 下游模型用非弃权训练硬弱标签拟合 TF-IDF + Logistic Regression，验证金标选 C，测试集仅作最终评价。可先执行 `python -B tasks/IMDb/tools/train_logistic.py --version v10 --check-data` 核验输入；训练时应指定新的 `--output-dir`。

测试样本和测试指标不进入 Analyzer / Patcher。Finnsp、AGnews、CHECKED、EDOS、IMDb、Yelp 的验证类别比例用于上游 Snorkel 先验；SMS、YouTube、Financial PhraseBank 使用固定均匀先验。全量指标包含弃权，与仅已覆盖样本的准确率应分别解读。冻结划分保留的正文重叠及其他实验限制见各任务报告。

## 维护约定

保留任务目录的实际大小写，尤其是 `YouTube`、`IMDb`、`Finnsp` 和 `AGnews`。迁移任务时同步检查根目录 `.env` 定位、文档命令及汇总脚本路径。历史结果中的绝对路径是运行时审计记录，不应为了迁移而改写输入哈希或实验配置。每次工作结束清理本次产生的临时文件、测试文件和缓存；实验数据、模型与正式报告按需保留。
