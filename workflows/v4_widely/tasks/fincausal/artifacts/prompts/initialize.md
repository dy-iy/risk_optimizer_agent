你负责构建 fincausal 任务中的一个 Label Function。

0 和 1 各构建一个 LF。正类识别原因与结果间联系；负类识别有依据的非因果结构。不把所有缺少 because 的句子判 0，也不把单个连接词命中当作充分条件。不确定则弃权。

根据 assigned_label、lf_name 和训练正反例，只返回一个完整函数，符合 response_schema。不要输出 Markdown 围栏。
