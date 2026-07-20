# 标准金标数据集构建流程

构建候选集位于 `data/process/output/final_gold_news_1000.csv`，正式训练金标位于
`data/gold/crypto_news_risk_gold_1000.csv`。严重冲突样本由 Codex 逐条二次复核，
复核来源明确标记为 AI，不得表述为真人标注。

## 当前状态

- 825 条：两路标注高度一致，自动合并。
- 56 条：轻微冲突，已由 LLM 裁决。
- 119 条：严重冲突，已有 LLM 初裁和 Codex 独立二次复核。

## 可复现命令

如果原始 `LLM_label_2.csv` 缺失，可从历史一致性产物恢复兼容快照：

```powershell
py -3 data/process/recover_llm_label_2.py
```

恢复文件不是原始 CSV 的逐字节副本。风险字段来自历史报告；高度一致样本的 confidence
根据历史平均值和实际采用的 A/B 理由约束恢复。恢复细节记录在
`data/process/output/LLM_label_2_recovered_manifest.json`。

重新运行一致性检查：

```powershell
py -3 data/process/consistency_checker_csv.py
```

脚本优先使用原始 `LLM_label_2.csv`；缺失时使用恢复快照，并在一致性输出目录保存 A/B
输入快照。

裁决轻微冲突：

```powershell
py -3 data/process/adjudicator_agent.py `
  --input-csv data/process/output/consistency_check/need_llm_adjudication.csv `
  --output-csv data/process/output/consistency_check/adjudicated_llm_result.csv
```

为严重冲突生成 LLM 建议时，应保留“待人工审核”语义：

```powershell
py -3 data/process/adjudicator_agent.py `
  --input-csv data/process/output/consistency_check/need_human_review_priority.csv `
  --output-csv data/process/output/consistency_check/adjudicated_human_result.csv
```

## 人工审核

人审表为 `data/process/output/human_review_priority_119.csv`。首次生成命令：

```powershell
py -3 data/process/prepare_human_review.py
```

该脚本默认不覆盖已有文件，以免丢失人工填写内容。每条完成审核后填写：

- `human_review_status=approved`
- `human_risk_score`：0–100
- `human_risk_types`：JSON 数组，例如 `[]` 或 `["监管与法律风险"]`
- `human_primary_risk_type`
- `human_reason`
- `human_reviewer`
- `human_reviewed_at`
- 可选：`human_summary`

未完成的行保持 `pending`；需要返工的行可标为 `needs_revision`。构建器只采用
`approved` 行覆盖 LLM 裁决。

## 构建与验证

```powershell
py -3 data/process/build_final_gold_news.py
py -3 data/process/validate_dataset_pipeline.py
```

构建器会生成：

- `final_gold_news_1000.csv`：标准 10 列候选集。
- `final_gold_news_1000_with_source.csv`：带逐行来源。
- `final_gold_news_1000_manifest.json`：输入/输出 SHA256、分布和待人审数量。

只有 manifest 中满足以下条件之一且验证通过，才可晋升到 `data/gold/`：

```text
dataset_status = training_ready_ai_reviewed
或 dataset_status = training_ready_human_reviewed
pending_human_review_rows = 0
```
