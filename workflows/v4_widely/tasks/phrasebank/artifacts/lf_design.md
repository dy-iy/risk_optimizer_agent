# Financial PhraseBank：投资者视角三分类情感：LF 构建方案

三个标签各构建一个 LF。组合指标对象、变化方向与上下文证据；利润下降与成本下降可能方向不同。neutral 也需要证据；不确定则返回 None，由固定投票和训练多数类回退处理。

## 公共接口

- 函数为 lf(record)，输入仅包含 text 和 inputs，无 gold、样本 ID 或来源元信息。
- 命中返回 labels、reason；Crypto 另含 level；不命中返回 None。
- 样例来自 train，不通过 ID 或全文记忆作答。
- 生成后先运行并评估，再交给 Analyzer 定位问题；Patcher 只替换选中的现有 LF。
- 不允许修改聚合器、数据加载和评分工具。

## 分析重点

检查情感混淆矩阵，区分正负颠倒与 neutral 边界错误。结合 LF 命中精度、未覆盖及冲突判断指标方向、否定和比较是否处理错误。不要只靠增加 neutral 预测提高 Accuracy。

## 接受条件

Macro-F1 严格提升，另报 Accuracy 和每类 P/R/F1。仅使用 cp1252 编码的 AllAgree；四个一致性子集重叠，不合并。默认文本分组划分 train/test 为 80/20。

代码或执行失败的候选不接受；版本选择只看 train；本次迭代决策完成后，test 仅用于各脚本的报告评估。
