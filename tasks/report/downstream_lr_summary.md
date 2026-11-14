# 四个数据集最新版本的逻辑回归下游模型

版本快照：2026-11-14。按版本号数字选择最新版本：Yelp v10、IMDb v10、CHECKED v15、AGnews v10；四个版本均已实际训练。AGnews v10 的重新训练指标与原有结果完全一致。

参考 [Finnsp 下游框架](../Finnsp/tools/train_logistic.py)：只保留对应版本训练集的非弃权硬弱标签；TF-IDF 词表、IDF 与逻辑回归系数仅拟合这些训练样本。验证金标从 C=[0.1,1,10] 中选参，同分取输入顺序中第一个；选定后评价完整测试集，测试金标仅用于最终评分。

英文 Yelp、IMDb、AGnews 使用词 1–2 gram，中文 CHECKED 使用字符 1–2 gram；最多 100,000 特征，sublinear_tf=True、lowercase=True。逻辑回归使用 liblinear、max_iter=2000、seed=42、class_weight=None；AGnews 四分类使用 One-vs-Rest，其余为二分类。所有下游预测均为 argmax，不弃权。

二分类按 1 类 F1 选择 C（Yelp/IMDb 为 Positive，CHECKED 为 fake）；AGnews 按 Macro-F1 选择。上游 Snorkel 已用验证集类别比例作先验，该使用情况保存在各版 data_summary.json 中。

## 最新版本结果

| 数据集 | 版本报告 | 保留训练数 | 移除弃权数 | C | 验证选参指标 | 验证选参得分 | 测试准确率 | 测试 Macro-F1 |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| Yelp | [v10](../Yelp/versions/v10/tfidf_lr_results/report.md) | 23,201 | 7,199 | 1 | Positive F1 | 89.13% | 89.82% | 89.68% |
| IMDb | [v10](../IMDb/versions/v10/tfidf_lr_results/report.md) | 14,344 | 5,656 | 1 | Positive F1 | 88.38% | 86.24% | 86.20% |
| CHECKED | [v15](../CHECKED/versions/v15/tfidf_lr_results/report.md) | 1,192 | 293 | 0.1 | fake F1 | 0.00% | 97.41% | 49.34% |
| AGnews | [v10](../AGnews/versions/v10/tfidf_lr_results/report.md) | 58,671 | 37,329 | 10 | Macro-F1 | 82.29% | 83.80% | 83.68% |

CHECKED v15：三个候选 C 的验证 fake F1 均为 0，按同分规则选 C=0.1。已选模型对完整验证集 212 条、测试集 425 条全部预测 real；测试集中 real/fake 为 414/11，fake 的 Precision、Recall 和 F1 均为 0。因此 97.41% 的测试准确率反映多数类占比，不能据此认为该模型能识别 fake。

## 与上游聚合的测试集指标比较

上游准确率与 Macro-F1 均取 all_samples（包含弃权），下游对同一完整测试集输出全部类别；上游覆盖样本上的指标不混入下表。

| 数据集 | 测试数 | 上游输出覆盖率 | 上游全量准确率 | 上游全量 Macro-F1 | LR 输出覆盖率 | LR 准确率 | LR Macro-F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Yelp | 3,800 | 79.18% | 67.76% | 75.18% | 100.00% | 89.82% | 89.68% |
| IMDb | 2,500 | 72.80% | 62.16% | 71.94% | 100.00% | 86.24% | 86.20% |
| CHECKED | 425 | 85.18% | 81.18% | 48.89% | 100.00% | 97.41% | 49.34% |
| AGnews | 12,000 | 61.17% | 53.29% | 65.74% | 100.00% | 83.80% | 83.68% |

## 冻结划分与正文重叠

沿用现有 data/split.json，不重分或额外去重。以下数量是验证/测试记录中，正文已出现在本版非弃权训练集的条数；已有内容重叠会限制指标的独立性。

| 数据集 | 验证正文完全相同 | 测试正文完全相同 | 验证正文去空白后相同 | 测试正文去空白后相同 |
| --- | ---: | ---: | ---: | ---: |
| Yelp | 0 | 0 | 0 | 0 |
| IMDb | 6 | 7 | 6 | 7 |
| CHECKED | 1 | 0 | 1 | 0 |
| AGnews | 10 | 9 | 10 | 10 |

## 文件与复现

每版 tfidf_lr_results/ 保存 model.joblib、train_cleaned.csv、valid_predictions.csv、test_predictions.csv、metrics.json、data_summary.json、run_config.json、tuning_history.json 和 report.md。model.joblib 包含 TF-IDF 与 LR，直接接受正文字符串列表；概率列按 run_config.json 的 probability_class_order 排列。

已核验四个保存模型重新加载后可复现全部验证/测试预测；清洗训练 ID、弱标签、输入 SHA-256、C 选择、概率列与指标均匹配；仅用清洗训练正文重新拟合 TF-IDF，词表与 IDF 完全一致。本次未保留临时训练目录或测试脚本。

从项目根目录只校验输入：

```powershell
python -B 'tasks/Yelp/versions/v10/train_logistic.py' --check-data
python -B 'tasks/IMDb/versions/v10/train_logistic.py' --check-data
python -B 'tasks/CHECKED/versions/v15/train_logistic.py' --check-data
python -B 'tasks/AGnews/versions/v10/train_logistic.py' --check-data
```

重新训练时移除 --check-data，并用 --output-dir 指定新目录；已有非空结果不会被训练入口覆盖。

[CSV 汇总](downstream_lr_summary.csv) 保留原始精度，并记录模型、指标和报告的相对路径。
