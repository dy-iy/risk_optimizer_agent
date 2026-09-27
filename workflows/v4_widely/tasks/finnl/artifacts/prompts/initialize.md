你负责构建 finnl 任务中的一个 Label Function。

确认后的每个标签各建一个 LF，组合主题词、行为与语境证据，处理合理共现和背景误触发。命中标签取并集，全部弃权返回空集合，不强制选唯一标签。

根据 assigned_label、lf_name 和训练正反例，只返回一个完整函数，符合 response_schema。不要输出 Markdown 围栏。
