# Risk Optimizer Agent

本仓库用大语言模型迭代文本分类的标签函数（Labeling Function，LF）。每条 LF 输出类别或 `-1`（弃权）。工作流聚合投票生成弱标签，再用训练集的覆盖和冲突统计、验证集的指标与样例指导 Analyzer 和 Patcher。弱标签也可用于训练 TF-IDF + 逻辑回归或 BERT。

## 任务入口

| 数据集与分类目标 | Benchmark | Improve |
| --- | --- | --- |
| SMS · 短信垃圾信息 | [SMS](benchmark/SMS/) | [SMS_improve](improve/SMS_improve/) |
| AGnews · 英文新闻主题四分类 | [AGnews](benchmark/AGnews/) | [AGnews_improve](improve/AGnews_improve/) |
| CHECKED · 中文新闻真实性 | [CHECKED](benchmark/CHECKED/) | [CHECKED_improve](improve/CHECKED_improve/) |
| EDOS · 英文性别歧视检测 | [EDOS](benchmark/EDOS/) | [EDOS_improve](improve/EDOS_improve/) |
| IMDb · 英文影评情感 | [IMDb](benchmark/IMDb/) | [IMDb_improve](improve/IMDb_improve/) |
| Yelp · 英文商户评论情感 | [Yelp](benchmark/Yelp/) | [Yelp_improve](improve/Yelp_improve/) |
| YouTube · 单条评论垃圾信息 | [YouTube](benchmark/YouTube/) | [YouTube_improve](improve/YouTube_improve/) |
| YouTube 评论串 · 多账号协同推广投资诈骗 | [Youtube_Collusion_Scam](benchmark/Youtube_Collusion_Scam/) | [Youtube_Collusion_Scam_improve](improve/Youtube_Collusion_Scam_improve/) |

- **FinNSP** 检测中文金融文本是否对候选实体包含负面消息。LF 接收正文和候选实体，固定验证集含 500 条标注。 [任务目录](tasks/Finnsp/) · [逻辑回归结果](tasks/Finnsp/downstream_lr_summary.md)
- **Financial PhraseBank** 将英文金融文本分为负面、中性和正面。固定用 100 条金标做开发反馈，用未标注集拟合 Snorkel，采用均匀类别先验。 [任务目录](tasks/financial_phrasebank/) · [迭代框架](tasks/financial_phrasebank/迭代框架.md)

## 快速开始

在仓库根目录运行下列 PowerShell 命令。安装目标任务的依赖文件，它会引入根目录的公共依赖。Benchmark 和 Improve 的任务依赖还包括 LangGraph。将 `python` 指向实际的实验环境。

```powershell
python -m pip install -r benchmark/IMDb/requirements.txt
python -B benchmark/IMDb/tools/data.py
python -B benchmark/IMDb/orchestrator.py --version v1 --iterations 0
```

示例先核验冻结划分，再评估已有的 `v1` 并更新 `iterations.csv`。零轮评估无需 API 密钥。可用其他任务目录替换示例路径。运行前确认所选任务有 `versions/v1/lfs.py`，或把 `v1` 换成已有版本。Git 忽略 `versions/`，从干净检出复现实验时，需从实验归档取得对应的 LF 源码。

## 调用 LLM 追加迭代

首次运行时复制配置模板到根目录的 `.env`。填写 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL`，`DEEPSEEK_BASE_URL` 默认是 `https://api.deepseek.com`。已有配置可直接使用。 [.env.example](.env.example)

```powershell
Copy-Item .env.example .env
```

配置完成后指定起始版本和追加轮数。此命令会调用 API 并记录 Token 用量。直接运行 `orchestrator.py` 可交互选择参数。

```powershell
python -B benchmark/IMDb/orchestrator.py --version v1 --iterations 1
```

### Benchmark

同一批 LF 投票分别由 Snorkel 和简单投票（`simple_vote`）聚合。Analyzer 根据简单投票的验证统计、验证样例和无金标训练样例，在新增、修改和删除中选择一种操作，每轮最多涉及 5 条 LF。Patcher 生成目标函数，代码通过本地校验后运行。

候选验收使用简单投票（`simple_vote`）在验证集上的净收益。对每个真实类别计算 `(正确数 − 误标数) / 真实样本数`，再对类别等权平均。弃权仅计入分母。候选得分相对保留版的提升必须严格大于 `--acceptance-epsilon`，默认 0.01。拒绝后从保留版继续，连续拒绝 3 个有效候选时停止。候选使用当前最大版本号加一，验证集得分、验收结果及聚合器写入候选的 `results/decision.json`。

### Improve

用训练和验证集的简单投票**输出覆盖率**取较小值，覆盖率低于 0.80 视为低覆盖。验证集**已覆盖准确率**达到任务阈值时视为高准确率，IMDb 的阈值是 0.80，其余任务为 0.90。

| 输出覆盖率 | 验证集已覆盖准确率 | 本轮操作 |
| --- | --- | --- |
| 低 | 高 | 新增 LF，扩大正确覆盖 |
| 低或高 | 低 | 修改 LF，优先减少误标 |
| 高 | 高 | 有覆盖缺口时新增 LF，完全覆盖后修改 LF |
| 验证集全部弃权 | 无 | 保守新增 LF |

新增时提供验证弃权和训练未覆盖样例。修改时提供验证错例、验证正确样例和训练投票冲突样例。通过执行校验的版本直接进入下一轮。Improve 按起始版本递增编号，从旧版本追加前应确认后续同名版本是否需要保留。

## 单版评估、训练与绘图

```powershell
python -B benchmark/IMDb/tools/runner.py --version v1
python -B benchmark/IMDb/tools/impact.py --old-version v1 --new-version v2
python -B benchmark/IMDb/picture/plot_metrics.py
python -B benchmark/IMDb/tools/train_logistic.py --version v1
```

`runner.py` 更新单版结果，`iterations.csv` 由总控更新。影响分析需要两个已评估版本，比较训练和验证集上的投票与预测变化。绘图读取已有评估结果。下游训练使用非弃权训练硬弱标签，验证集用于选参或选择检查点，测试集用于最终评价。

对提供 `requirements-bert.txt` 的任务，可先校验训练输入，再用 CUDA 微调。默认读取本地预训练权重，也可用 `--model-path` 指定权重目录，或用 `--allow-download` 下载。

```powershell
python -m pip install -r improve/IMDb_improve/requirements-bert.txt
python -B improve/IMDb_improve/tools/train_bert.py --version v1 --check-data
python -B improve/IMDb_improve/tools/train_bert.py --version v1 --device cuda
```

## 数据与结果

每个任务的 `data/split.json` 记录冻结划分、样本数和校验哈希。弱标签模型在训练集或未标注集的 LF 投票上拟合，验证集或固定标注集用于开发反馈，测试样本和指标只用于评估。报告中的全量指标将弃权计为漏判，已覆盖准确率仅以有预测的样本为分母。 [指标笔记](指标笔记.md)

| 路径 | 内容 |
| --- | --- |
| `tools/task.py`（多数任务） | 标签映射、输入契约和任务说明 |
| `versions/vN/lfs.py` | 该版本的 LF 源码 |
| `versions/vN/Snorkel/`、`versions/vN/simple_vote/` | Benchmark 和 Improve 的聚合指标、弱标签、下游模型与影响记录 |
| `versions/vN/results/` | 共享投票与质量统计、反馈、分析、改动和错误记录。FinNSP 和 Financial PhraseBank 的聚合结果也保存在这里 |
| `iterations.csv`、`tokens.csv`、`tokens.md` | 版本指标及 API 用量。部分任务另有 `timing.csv` 记录运行耗时 |
| [output/](output/) | 汇总图表 |

AGnews、IMDb 和 Yelp 的上游标签与规则说明以 `source_notes.txt` 保存，冻结清单中的 `readme.txt` 是这些附件的原始文件名。 [AGnews 来源说明](benchmark/AGnews/raw/source_notes.txt) · [IMDb 来源说明](benchmark/IMDb/raw/source_notes.txt) · [Yelp 来源说明](benchmark/Yelp/raw/source_notes.txt)

Financial PhraseBank 的原始处理记录标明 CC BY-NC-SA 3.0。引用 Malo 等人（2014），*Good debt or bad debt: Detecting semantic orientations in economic texts*。
