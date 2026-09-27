# Risk Optimizer Agent｜加密新闻风险规则迭代项目

本项目通过“运行规则 → 评估 → 错误切分 → LLM 分析 → 局部修改 → 版本比较”改进加密货币新闻风险识别规则。交接包保留源码、数据、已有实验报告及论文框架图，建议先从 **V4** 阅读和运行。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `workflows/v4/` | V4：12 个 Label Function，多标签风险类别及 low / medium / high 等级，不输出风险分数 |
| `workflows/v4/versions/v1` 至 `v12` | 已有规则快照；较大版本号不代表效果一定更好 |
| `workflows/v4/data/crypto_news_train.csv` | V4 自带的 1000 条标注新闻 |
| `workflows/v4/artifacts/` | 固定风险词表和初始化参考样例 |
| `workflows/baseline/` | 原始评分、等级及主风险类型工作流 |
| `workflows/v2/`、`workflows/v3/` | 历史实验；V3 优先修复主风险类型错误 |
| `data/input/`、`data/gold/` | 原始输入、标注数据、来源审计文件 |
| `data/process/` | 标注、裁决、复核、校验脚本及已有中间结果 |
| `docs/` | 迭代流程、实验说明和交接验证记录 |
| `test/` | 旧版规则及评估脚本，并非完整自动化测试集 |
| `workflow_v4_论文框架图.pptx` | V4 论文框架图 |

工作流版本 V4 与其内部规则版本 v1–v12 是两个不同概念。

## 安装与快速运行（无需 API Key）

在解压后的项目根目录执行。建议 Python 3.11 或更新版本，本次本地验证使用 Python 3.14。规则运行使用 CPU，无需 PyTorch、CUDA 或 GPU。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m workflows.v4.tools.runner
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m workflows.v4.tools.runner
```

下文以 `python` 简写，请替换成虚拟环境 Python 路径或先激活环境。`requirements.txt` 是 V4 核心依赖；历史工作流还需 `python -m pip install -r requirements-legacy.txt`。依赖范围不是完整锁文件，实际验证版本见 [交接检查记录](docs/HANDOFF_CHECK.md)。

已附规则，不需要重新初始化。默认命令运行 v1；运行 v12：

```powershell
python -c "from workflows.v4.tools.runner import run_version; run_version('v12')"
```

输出会更新对应的 `workflows/v4/versions/vN/reports/`：

- `predictions/predictions.csv`：预测结果。
- `merged/merged.csv`：预测与标注对齐结果。
- `evals/category_metrics.json`：类别 Micro-F1、Macro-F1 及各类别 Precision / Recall / F1。
- `evals/level_metrics.json`：等级 Accuracy、Macro-F1、高风险 Recall / F1。
- `errors/type_mismatch.csv`、`errors/level_mismatch.csv`：错误样本。

V4 输入 CSV 使用 UTF-8，字段为 `新闻id, 时间, 内容, 风险等级, 风险类别, 原因`。等级为 `low / medium / high`，类别为 JSON 数组字符串。完整评估需要标注列。换数据可修改 `workflows/v4/tools/paths.py` 中的 `input_csv`，类别应与固定词表一致。

## LLM 自动迭代（可选）

复制模板并填写自己的密钥和实际可用的模型名：

```powershell
Copy-Item .env.example .env
```

macOS / Linux 使用 `cp .env.example .env`。V4 读取 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`；仅设置 `OPENAI_API_KEY` 不满足 V4 当前实现。代码内置模型名不代表服务端可用性已验证。

```powershell
python -m workflows.v4.orchestrator
```

按提示输入当前版本和迭代轮数。已附 v1–v12，建议另存项目副本后，从 `v12` 开始尝试一轮，生成 v13。自动迭代会将新闻样例及规则代码发送给配置的 API 服务，并产生该服务的调用费用。

Analyzer 选择 1–6 个目标函数，Patcher 局部替换并校验；候选版须通过综合质量分和关键指标下降限制，连续失败则保留当前版本并停止。詳见 [V4 说明](workflows/v4/README.md)。

只有需要重新生成首版时才执行 `python -m workflows.v4.init`：它会调用 12 次 LLM，并覆盖 `versions/v1/script_v1.py`。从旧版本启动迭代也可能覆盖已有下一版本脚本，请先另存副本。

## 历史实验与数据说明

各历史工作流入口及参数见其目录内 README，实验比较见 [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)。已有报告按原样保留，可能含原机器绝对路径，仅在数据和评估口径一致时比较结果。

**已知缺项：** 当前项目缺少历史默认文件 `data/gold/crypto_news_risk_gold_1000.csv`。本次没有擅自重建金标；运行历史流程前，需参考 [数据说明](data/gold/DATASET_CARD.md) 和 [处理说明](data/process/README.md)，核对带来源数据及中间结果，恢复默认文件或显式指定兼容金标。此缺项不影响使用独立数据的 V4。

标注包含 LLM 标注、裁决及 Codex AI 复核，不能描述为纯人工金标。V4 在同一批数据上评估、诊断和迭代，指标不能视为独立测试集上的泛化表现。

## 交接包与验证

包含源码、数据、已有实验输出和框架图；排除真实 `.env`、Git 历史、编辑器配置、临时目录、Python 缓存及旧压缩包。`.env.example` 仅含占位配置。

验证结果见 [docs/HANDOFF_CHECK.md](docs/HANDOFF_CHECK.md)。本次没有调用在线 LLM，也没有重新验证全部历史实验。
