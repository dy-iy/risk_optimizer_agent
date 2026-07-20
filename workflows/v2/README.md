# Risk Optimizer Agent

这个项目是一个面向加密货币新闻的风险标签规则优化 agent。它不会直接让 LLM 去给新闻打标签，而是把 LLM 放在两个可控节点里：

1. `analyzer_llm.py`：基于规则脚本的错误样本、统计指标和源码静态 trace，诊断规则哪里错。
2. `patcher_llm_v2.py`：基于诊断报告和当前规则脚本，生成下一版完整规则脚本，并做语法校验。

确定性的运行、合并、评估、切片、版本对比都由 `tools/` 下的本地工具完成。主入口是 `orchestrator.py`，负责把这些步骤串起来，形成一个 `vN -> vN+1` 的自动迭代流程。

## Quick Start

```powershell
py -3 orchestrator.py
```

程序会要求输入当前版本号和下一版本号，例如：

```text
current_version: v20
next_version: v21
```

默认数据路径：

- 输入新闻：`data/input/raw_1000_news.csv`
- 金标数据：`data/gold/crypto_news_risk_gold_1000.csv`
- 当前脚本：`versions/vN/scripts/risk_labeler_vN.py`
- 下一版脚本：`versions/vM/scripts/risk_labeler_vM.py`

### 隔离实验目录

默认仍使用根目录 `versions/`。如需重新做一轮、又不覆盖历史版本，可设置独立版本根目录：

```powershell
$env:RISK_VERSIONS_DIR = ".\experiments\my_experiment\versions"
py -3 orchestrator.py
```

`runner`、`analyzer`、`patcher`、历史指标和候选晋级都会使用该目录。输入与 Gold 仍来自项目公共 `data/`。新实验必须先准备 `v1/scripts/risk_labeler_v1.py`。

已准备好的干净实验可直接运行：

```powershell
.\experiments\restart_20260715\run.ps1
```

## Agent Workflow

完整 workflow 固定为：

```text
current rule script
  -> runner
  -> merger
  -> evaluator
  -> slicer
  -> analyzer_llm
  -> patcher_llm
  -> candidate runner
  -> candidate merger
  -> candidate evaluator
  -> candidate slicer
  -> comparator
```

### 1. Runner

入口：`tools/runner.py`

`runner` 用子进程运行当前版本规则脚本。它通过环境变量把输入输出路径传给规则脚本：

- `CSV_PATH`：输入新闻 CSV。
- `OUT_PATH`：规则脚本输出预测 CSV。

规则脚本输出通常包含：

- `新闻id`
- `risk`
- `rule_label`
- `rule_types`
- `rule_primary_type`
- 各类 `score_xxx` 风险分数列

产物：

- `versions/vN/reports/predictions/risk_labeler_vN_output.csv`
- `versions/vN/reports/predictions/risk_labeler_vN_runlog.json`

### 2. Merger

入口：`tools/merger.py`

`merger` 按 `新闻id` inner join 金标和规则预测，并重命名字段：

- 金标 `risk_score` -> `gold_risk_score`
- 规则 `risk` -> `rule_risk_score`
- 金标 `risk_label` -> `gold_risk_label`
- 规则 `rule_label` -> `rule_risk_label`
- 金标 `primary_risk_type` -> `gold_primary_risk_type`
- 规则 `rule_primary_type` -> `rule_primary_risk_type`

它还会生成辅助列：

- `score_diff`
- `label_match`
- `primary_type_match`

产物：

- `versions/vN/reports/merged/risk_labeler_vN_merged.csv`
- `versions/vN/reports/merged/risk_labeler_vN_merge_log.json`

### 3. Evaluator

入口：`tools/evaluator.py`

`evaluator` 对 merged CSV 做整体指标评估：

- 风险分数：`MAE`、`RMSE`
- 风险等级：`label_accuracy`
- 主风险类型：`primary_type_accuracy`
- label confusion matrix
- gold/rule label distribution
- gold/rule primary type distribution
- top type mismatches

产物：

- `versions/vN/reports/evals/risk_labeler_vN_eval.json`
- `versions/vN/reports/evals/risk_labeler_vN_eval_details.json`

### 4. Slicer

入口：`tools/slicer.py`

`slicer` 从 merged CSV 切出四类代表性错误样本：

- `false_positive`：金标是 `low`，规则判成 `medium/high`。
- `false_negative`：金标是 `high`，规则判成 `low`。
- `type_mismatch`：风险等级相同，但主风险类型不同。
- `score_diff_top`：风险分数差距最大的 TopN，默认 200 条。

产物：

- `versions/vN/reports/errors/risk_labeler_vN_false_positive.csv`
- `versions/vN/reports/errors/risk_labeler_vN_false_negative.csv`
- `versions/vN/reports/errors/risk_labeler_vN_type_mismatch.csv`
- `versions/vN/reports/errors/risk_labeler_vN_score_diff_top.csv`
- `versions/vN/reports/errors/risk_labeler_vN_slice_log.json`

### 5. Analyzer LLM

入口：`analyzer_llm.py`

`analyzer_llm` 是第一个 agent 智能节点。它不修改代码，只负责把错误样本、统计指标、源码静态分析和版本历史合成诊断报告。

它会构建一个 `payload`，核心内容包括：

- 四类错误样本的规模和分布。
- 各 `score_xxx` 的触发次数、均值、最大值。
- false positive / false negative 的代表样本。
- type mismatch 的高频错配对。
- score diff 的 overestimate / underestimate 方向。
- primary type competition，即规则主类型和金标主类型的分数竞争关系。
- 当前规则脚本源码上下文：关键词表、阈值、`score_` 函数、negative keyword lists、early return guards。
- 样本级 `scorer_trace`：静态匹配哪些关键词命中、哪些否定词或 early return 可能影响分数。
- 最近两版指标，用于发现 regression。

产物：

- `versions/vN/reports/analysis/risk_labeler_vN_analysis_llm_payload.json`
- `versions/vN/reports/analysis/risk_labeler_vN_analysis_llm.json`
- `versions/vN/reports/analysis/risk_labeler_vN_analysis_llm.md`
- 可选：`versions/vN/reports/analysis/risk_labeler_vN_version_metrics.json`

### 6. Patcher LLM

入口：`patcher_llm_v2.py`

`patcher_llm` 是第二个 agent 智能节点。它读取：

- 当前完整规则脚本。
- `analysis_llm.json` 诊断报告。

然后要求 LLM 返回纯 JSON，其中最关键字段是：

- `summary`
- `changes`
- `validation_notes`
- `full_code`

`full_code` 必须是一份完整可运行的 Python 规则脚本，而不是 diff。生成后本地会做 `compile()` 语法检查：

- 如果通过，写入下一版脚本。
- 如果失败，调用 repair prompt，让 LLM 只修复语法/结构问题，再次编译。

产物：

- `versions/vM/scripts/risk_labeler_vM.py`
- `versions/vM/reports/patched/risk_labeler_vM_patch_report.json`
- `versions/vM/reports/patched/risk_labeler_vM_patch_report.md`

### 7. Candidate Evaluation

下一版代码先写入 `reports/patched/*_candidate.py` 暂存区，`orchestrator.py` 随后运行候选版本：

```text
candidate runner -> candidate merger -> candidate evaluator -> candidate slicer
```

这些产物写入下一版目录：

- `versions/vM/reports/predictions/...`
- `versions/vM/reports/merged/...`
- `versions/vM/reports/evals/...`
- `versions/vM/reports/errors/...`

### 8. Comparator

入口：`tools/comparator.py`

`comparator` 比较当前版本和候选版本。以下指标都会记录，但晋级不再使用简单多数投票：

- `score_mae`，越低越好。
- `score_rmse`，越低越好。
- `label_accuracy`，越高越好。
- `primary_type_accuracy`，越高越好。
- `false_positive_rows`，越低越好。
- `false_negative_rows`，越低越好。
- `type_mismatch_rows`，越低越好。

晋级采用 `constrained_pareto_v1`：

- `false_positive_rows`、`false_negative_rows`、`type_mismatch_rows` 任一回归，候选直接拒绝。
- `label_accuracy`、`primary_type_accuracy`、MAE、RMSE 和 matched rows 作为质量护栏；不能通过把 medium 错分为 low 等方式“刷低”错误桶。
- 三项均不回归且至少一项实际下降，才具备晋级资格。
- workflow 会从同一 Gold SHA-256 的最近版本中选择一个 `focus_metric`；焦点未下降时，即使其它指标改善也不晋级。
- 只有通过门槛后，暂存候选才会复制到 `versions/vM/scripts/risk_labeler_vM.py`；拒绝候选不会成为下一轮 baseline。
- MAE、RMSE 和 accuracy 作为诊断与辅助比较保留，不能掩盖三类目标错误数的回归。

每轮评估报告写入 Gold 文件 SHA-256。版本历史只比较同一数据集指纹，避免更换 Gold 后把不可比的指标趋势交给分析 Agent。

产物：

- `versions/vM/reports/comparisons/vN_vs_vM_compare.json`
- `versions/vM/reports/comparisons/vN_vs_vM_compare.md`
- `versions/vM/reports/orchestrations/vN_to_vM_orchestrate.json`

## Prompt Design

Prompt 模板集中在 `prompts/`。

### Analyzer Prompt

文件：`prompts/analyzer.py`

系统角色：

- 加密货币新闻风险标注错误诊断助手。
- 目标不是单独降低 false positive，而是降低整体错误。
- 必须同时权衡 `false_positive`、`false_negative`、`type_mismatch`、`score_diff`。
- 必须基于证据，不允许编造；证据不足时要明确写“证据不足”。
- 输出必须是纯 JSON。

任务要求重点：

- 同时分析四类错误，不允许只优化误报。
- 对每个错误模式判断属于过宽触发、漏召回、主类型竞争失败、分数高估、分数低估、阈值问题或证据不足。
- `score_diff` 必须区分 overestimate 和 underestimate。
- `type_mismatch` 必须区分错误 scorer 过强、gold 对应 scorer 过弱，还是 primary type 选择逻辑问题。
- 如果有 `scorer_trace`，必须结合 trace 判断是缺少正向触发、命中负向 guard、低于阈值，还是 primary competition。
- 每条 patch 建议必须说明副作用，尤其是否可能恶化 FN、type mismatch 或 score diff。
- 如果版本历史显示 regression，优先分析 regression。

要求输出的 JSON schema 包含：

- `executive_summary`
- `metric_tradeoff_diagnosis`
- `patterns`
- `scorer_diagnosis`
- `scorer_trace_diagnosis`
- `primary_type_diagnosis`
- `patch_plan`
- `do_not_patch`
- `confidence`

### Patcher Prompt

文件：`prompts/patcher.py`

系统角色：

- 给加密货币新闻风险规则系统打补丁的 Python 工程师。
- 根据 `analysis_llm.json` 直接产出完整、可运行、结构尽量稳定的新脚本。
- 目标是降低整体错误，而不是单独压低 false positive。
- 必须尊重 `metric_tradeoff_diagnosis`、`scorer_trace_diagnosis`、`primary_type_diagnosis`、`risky_patch`、`guardrail`、`validation` 和 `do_not_patch`。
- 输出必须是纯 JSON。

任务要求重点：

- 直接生成完整新脚本，不只是修改建议。
- 尽量保留原脚本结构、函数名、输入输出接口和列名。
- 不改变 CSV 协议，除非 analysis 明确要求。
- 不引入标准库和 pandas 之外的新第三方依赖。
- 修改重点落在关键词范围、否定词、金额门槛、阈值、强弱触发、主类型逻辑、大小写归一化。
- 若 patch 只会降低 false positive 但被标记为 risky，默认不要做，除非有明确 guardrail。
- 有 regression 时优先修复 regression。
- `full_code` 必须是完整可保存为 `.py` 的脚本。

要求输出的 JSON schema 包含：

- `patch_version`
- `summary`
- `changes`
- `validation_notes`
- `full_code`

### Repair Prompt

文件：`prompts/patcher.py`

当 `full_code` 编译失败时触发。repair prompt 只允许修复语法或结构问题，尽量不改变业务逻辑。输出字段：

- `repair_summary`
- `full_code`

## Tool Inventory

| Tool | File | Responsibility |
|---|---|---|
| Runner | `tools/runner.py` | 运行版本化规则脚本，传入 `CSV_PATH` / `OUT_PATH`，保存 runlog |
| Merger | `tools/merger.py` | 合并金标和预测，生成统一评估表 |
| Evaluator | `tools/evaluator.py` | 计算分数、等级、主类型指标 |
| Slicer | `tools/slicer.py` | 切出 FP、FN、type mismatch、score diff 样本 |
| Comparator | `tools/comparator.py` | 对比 baseline 和 candidate 指标，判断 winner |
| Candidate Builder | `tools/candidate_builder.py` | 用带匹配次数前置条件的精确编辑构造可审计单变量候选，并在写入前编译检查 |
| Paths | `tools/paths.py` | 统一版本路径、产物路径和目录创建 |
| Common | `tools/common.py` | CSV/JSON IO、`.env` 加载、OpenAI-compatible chat JSON 调用、进度条 |

## Version Layout

```text
versions/
  vN/
    scripts/
      risk_labeler_vN.py
    reports/
      predictions/
      merged/
      evals/
      errors/
      analysis/
      patched/
      comparisons/
      orchestrations/
```

`tools/paths.py` 统一生成所有路径。每次 `vN -> vM` 会确保两个版本目录都存在。

## Configuration

LLM 配置来自 `.env` 或环境变量：

- `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`
- `DEEPSEEK_BASE_URL`，默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL`，默认 `deepseek-chat`

Analyzer 相关：

- `LLM_SAMPLE_ROWS_PER_BUCKET`，默认 `12`
- `LLM_TEXT_LIMIT`，默认 `220`

Patcher 相关：

- `PATCHER_LLM_TEMPERATURE`，默认 `0.2`
- `PATCHER_LLM_MAX_RETRIES`，默认 `2`
- `PATCHER_SOURCE_CODE_LIMIT`，默认 `80000`
- `PATCHER_ANALYSIS_LIMIT`，默认 `50000`

`orchestrator.py` 顶部还有固定运行参数：

- `TIMEOUT = 300`
- `MODEL = "deepseek-v4-pro"`
- `SAMPLE_ROWS = 12`
- `TEXT_LIMIT = 220`
- `ENABLE_PATCHER = True`
- `PATCH_TEMPERATURE = 0.2`

## Running Individual Stages

Analyzer 可单独运行：

```powershell
py -3 analyzer_llm.py `
  --false-positive-csv versions\v20\reports\errors\risk_labeler_v20_false_positive.csv `
  --false-negative-csv versions\v20\reports\errors\risk_labeler_v20_false_negative.csv `
  --type-mismatch-csv versions\v20\reports\errors\risk_labeler_v20_type_mismatch.csv `
  --score-diff-top-csv versions\v20\reports\errors\risk_labeler_v20_score_diff_top.csv `
  --analysis-json versions\v20\reports\analysis\risk_labeler_v20_analysis_llm.json `
  --analysis-markdown versions\v20\reports\analysis\risk_labeler_v20_analysis_llm.md `
  --llm-payload-json versions\v20\reports\analysis\risk_labeler_v20_analysis_llm_payload.json `
  --source-script versions\v20\scripts\risk_labeler_v20.py
```

Patcher 可单独运行：

```powershell
py -3 patcher_llm_v2.py `
  --source-script versions\v20\scripts\risk_labeler_v20.py `
  --analysis-json versions\v20\reports\analysis\risk_labeler_v20_analysis_llm.json `
  --output-script versions\v21\scripts\risk_labeler_v21.py `
  --patch-report-json versions\v21\reports\patched\risk_labeler_v21_patch_report.json `
  --patch-report-markdown versions\v21\reports\patched\risk_labeler_v21_patch_report.md
```

Comparator 可单独运行：

```powershell
py -3 tools\comparator.py `
  --baseline-eval-json versions\v20\reports\evals\risk_labeler_v20_eval.json `
  --candidate-eval-json versions\v21\reports\evals\risk_labeler_v21_eval.json `
  --baseline-slice-log-json versions\v20\reports\errors\risk_labeler_v20_slice_log.json `
  --candidate-slice-log-json versions\v21\reports\errors\risk_labeler_v21_slice_log.json `
  --compare-json versions\v21\reports\comparisons\v20_vs_v21_compare.json `
  --compare-markdown versions\v21\reports\comparisons\v20_vs_v21_compare.md `
  --baseline-name v20 `
  --candidate-name v21
```

## Design Notes

- LLM 不直接改当前文件；patcher 写入下一版 `versions/vM/scripts/risk_labeler_vM.py`。
- 每个阶段都有 JSON/CSV 产物，便于回放和人工审查。
- Analyzer 的 `scorer_trace` 是静态启发式 trace，不是精确运行时分支追踪。
- Comparator 的 winner 是指标投票式判断；如果打平，会优先用 `label_accuracy`、`score_mae`、`primary_type_accuracy` 打破平局。
- 版本化脚本是主要资产，历史 reports 是每轮实验的证据链。
