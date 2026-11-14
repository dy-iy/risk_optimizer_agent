# FinNSP 逻辑回归下游模型对比

四个版本各自使用对应 Snorkel 硬弱标签，移除训练集的 abstain；候选 C 为 0.1、1、10，按完整 500 条验证集的 1 类 F1 选取，同分取输入顺序中第一个。

与 SMS 沿用相同筛选、C 网格、liblinear、max_iter=2000、seed=42 和 argmax 无弃权预测。中文适配使用字符 1–2 gram，max_features=100000、sublinear_tf=True；模型输入同时包含正文和候选实体。TF-IDF 词表与 IDF、逻辑回归系数均仅在非弃权训练样本上拟合。

| 版本 | 弱标签训练数 | C | 验证 1 类 F1 | 验证 Macro-F1 | 测试准确率 | 测试 1 类 F1 | 测试 Macro-F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [v4](versions/v4/tfidf_lr_results/report.md) | 2,963 | 10 | 94.92% | 94.08% | 93.20% | 93.58% | 93.18% |
| [v6](versions/v6/tfidf_lr_results/report.md) | 3,270 | 10 | 94.72% | 93.89% | 93.20% | 93.61% | 93.17% |
| [v8](versions/v8/tfidf_lr_results/report.md) | 3,231 | 10 | 94.57% | 93.67% | 93.40% | 93.81% | 93.37% |
| [v10](versions/v10/tfidf_lr_results/report.md) | 3,245 | 10 | 94.74% | 93.88% | 93.60% | 94.01% | 93.57% |

测试指标均基于完整的 500 条官方 dev；0=无针对候选实体的负面消息，1=有。各版已选模型均输出全部测试样本，不弃权。

上游 Snorkel 已使用验证集类别比例作为先验；本次下游逻辑回归使用验证金标选 C，测试金标仅用于最终评分。

数据限制：沿用确认的冻结划分，四个训练子集都包含与测试集完全相同输入的 1 条记录（训练 8a9bf6cf、测试 73e63587）。训练/验证之间另有 1 对去空白后正文相同、候选实体不同的记录。本次未重分或去重，不能将结果视为严格内容去重后的测试结果。

已核验：四个保存模型重新加载后可复现全部预测；词表和 IDF 与仅使用本版清洗训练文本重新拟合的结果完全一致；输入文件 SHA-256、训练 ID、弱标签和 C 选择均已核对。

每版的 tfidf_lr_results 保存 model.joblib、train_cleaned.csv、valid_predictions.csv、test_predictions.csv、metrics.json、data_summary.json、run_config.json、tuning_history.json 和 report.md。
