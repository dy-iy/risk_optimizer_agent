# 脚本版本对比报告

- Baseline: **v16**
- Candidate: **v17**
- Winner: **v17**

## 核心指标对比

| Metric | Baseline | Candidate | Better |
|---|---:|---:|---|
| score_mae | 14.606000 | 14.222000 | candidate |
| score_rmse | 20.092138 | 19.380248 | candidate |
| label_accuracy | 0.833000 | 0.843000 | candidate |
| primary_type_accuracy | 0.661000 | 0.662000 | candidate |

## 错误样本对比

| Metric | Baseline | Candidate | Better |
|---|---:|---:|---|
| false_positive_rows | 84.0 | 71.0 | candidate |
| false_negative_rows | 2.0 | 2.0 | tie |
| type_mismatch_rows | 230.0 | 236.0 | baseline |

## 结论

- v17 在风险分数 MAE 上更优。
- v17 在风险分数 RMSE 上更优。
- v17 在风险等级准确率上更优。
- v17 在主风险类别准确率上更优。
- v17 的误报更少。
- v16 的主类别错配更少。

## 计分

- Baseline score: 1
- Candidate score: 5
