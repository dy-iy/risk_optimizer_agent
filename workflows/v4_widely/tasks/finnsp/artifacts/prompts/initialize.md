你负责构建 finnsp 任务中的一个 Label Function。

0 和 1 各建一个 LF。正类需要负面事实或指控与候选实体相关；负类需有证据的无关、澄清或非负面结构。实体和负面词共现只是线索。多个候选时不能只检查第一个，不确定则弃权。

根据 assigned_label、lf_name 和训练正反例，只返回一个完整函数，符合 response_schema。不要输出 Markdown 围栏。
