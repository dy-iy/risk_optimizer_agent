你负责构建 phrasebank 任务中的一个 Label Function。

三个标签各构建一个 LF。组合指标对象、变化方向与上下文证据；利润下降与成本下降可能方向不同。neutral 也需要证据；不确定则返回 None，由固定投票和训练多数类回退处理。

根据 assigned_label、lf_name 和训练正反例，只返回一个完整函数，符合 response_schema。不要输出 Markdown 围栏。
