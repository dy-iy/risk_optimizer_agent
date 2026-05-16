# 脚本版本对比报告

- Baseline: **v17**
- Candidate: **v18**
- Winner: **v18**

## 核心指标对比

| Metric | Baseline | Candidate | Better |
|---|---:|---:|---|
| score_mae | 14.222000 | 13.620000 | candidate |
| score_rmse | 19.380248 | 18.501838 | candidate |
| label_accuracy | 0.843000 | 0.865000 | candidate |
| primary_type_accuracy | 0.662000 | 0.661000 | baseline |

## 错误样本对比

| Metric | Baseline | Candidate | Better |
|---|---:|---:|---|
| false_positive_rows | 71.0 | 42.0 | candidate |
| false_negative_rows | 2.0 | 7.0 | baseline |
| type_mismatch_rows | 236.0 | 259.0 | baseline |

## 结论

- v18 在风险分数 MAE 上更优。
- v18 在风险分数 RMSE 上更优。
- v18 在风险等级准确率上更优。
- v17 在主风险类别准确率上更优。
- v18 的误报更少。
- v17 的漏报更少。
- v17 的主类别错配更少。

## 计分

- Baseline score: 3
- Candidate score: 4
