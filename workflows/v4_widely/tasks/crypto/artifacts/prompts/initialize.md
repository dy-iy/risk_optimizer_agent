你负责构建 crypto 任务中的一个 Label Function。

每个风险类别一个 LF。先判断该类别事件是否成立，再给出等级和原因；否定、条件句在相关 LF 内处理。新建规则聚合采用类别并集与最高等级；未命中返回空类别和 low。导入旧版本时保留只读辅助函数与原聚合逻辑。

根据 assigned_label、lf_name 和训练正反例，只返回一个完整函数，符合 response_schema。不要输出 Markdown 围栏。
