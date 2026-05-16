# 脚本版本对比报告

- Baseline: **v18**
- Candidate: **v19**
- Winner: **v19**

## 核心指标对比

| Metric | Baseline | Candidate | Better |
|---|---:|---:|---|
| score_mae | 13.620000 | 13.521000 | candidate |
| score_rmse | 18.501838 | 18.326565 | candidate |
| label_accuracy | 0.865000 | 0.869000 | candidate |
| primary_type_accuracy | 0.661000 | 0.672000 | candidate |

## 错误样本对比

| Metric | Baseline | Candidate | Better |
|---|---:|---:|---|
| false_positive_rows | 42.0 | 31.0 | candidate |
| false_negative_rows | 7.0 | 10.0 | baseline |
| type_mismatch_rows | 259.0 | 258.0 | candidate |

## 结论

- v19 在风险分数 MAE 上更优。
- v19 在风险分数 RMSE 上更优。
- v19 在风险等级准确率上更优。
- v19 在主风险类别准确率上更优。
- v19 的误报更少。
- v18 的漏报更少。
- v19 的主类别错配更少。

## 计分

- Baseline score: 1
- Candidate score: 6
