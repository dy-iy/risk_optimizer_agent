# Analyzer / Patcher 输入输出契约

实际 Prompt 由 [tools/analyzer.py](tools/analyzer.py) 和 [tools/patcher.py](tools/patcher.py) 构造。每个候选收到的准确反馈保存于 `rounds/rNNN/candidate_XX/versions/vN/results/feedback.json`；原始模型回复、解析/校验失败内容及纠正回复保存在同目录。

## 输入隔离

Analyzer 只接收：当前 LF 代码和名称、simple_vote 的发现集指标/类别覆盖、发现集 LF 质量、训练无金标统计、本轮策略以及当前操作案例。selection 和 test 的样本及指标不包含在反馈白名单中。

add 的 `cluster` 包含：

```json
{
  "cluster_id": "label_0_<成员ID哈希>",
  "target_label": 0,
  "mode": "cluster_diverse",
  "valid_uncovered_count": 12,
  "train_similar_uncovered_count": 80,
  "cohesion": 0.81,
  "train_support_is_label_evidence": false,
  "center_examples": [],
  "diverse_examples": [],
  "boundary_examples": [],
  "random_examples": [],
  "hard_negative_examples": [],
  "train_support_examples": []
}
```

数字仅为结构示例，不代表实测。金标案例带 `id,text,source,gold,prediction,votes,similarity_to_center`；训练案例不包含 gold。被省略的 LF 票均为弃权。

cluster_random/random 使用 random_examples；cluster_diverse 使用中心、多样和边界三组。目标金标案例来自发现集当前全弃权池；异标签反例可来自发现角色已覆盖样本。训练语义相似支持不等于实际 LF 命中或正确覆盖。

modify 的 examples 为 `valid_wrong / valid_correct / train_conflict`，每组最多 n，错误和正确只按发现集金标区分。

## 输出与校验

```json
{
  "action": "add",
  "operations": [{
    "target": "lf_shared_pattern",
    "evidence": [
      "[gold:真实ID_A] 与 [gold:真实ID_B] 共享可实现机制；[negative:真实ID_C] 需要被局部条件排除。"
    ],
    "plan": "明确匹配结构、目标标签、对未见表达的泛化方式以及相似反例的弃权边界。"
  }]
}
```

顶层及每项字段必须精确匹配。action 只能等于程序在抽样前决定的 add 或 modify。add 默认只生成一条 LF；modify 最多总控指定预算。新增名称不能已存在，修改名称必须存在，目标不可重复。

add 的 evidence 合计至少有两个不同的 `[gold:ID]`，存在反例时至少有一个 `[negative:ID]`。训练引用为 `[train:ID]`。本地检查来源和 ID 是否匹配提供的案例；这些校验只能保证引用真实，不能证明模型叙述的语义依据正确。

新增规则命中返回 cluster.target_label，其他情形弃权。Prompt 要求归纳多例共享模式，禁止一例一条规则、专有 ID/人名特判、整句正则、默认类别填覆盖和拼接单例条件。否定、引用和立场需要局部判断，不能因为整段任意位置有 not 或引号而全部弃权。

## Patcher 与候选择优

Patcher 接收 original_code、analysis 和同一 cluster，只输出目标函数。AST 校验与本地合并保留未涉及的函数、共享常量和辅助代码。

同轮多个候选均从原基线生成；candidate_context 只包含候选编号及先前方案，用来提示采用不同机制，不提供先前候选的选择集表现。Analyzer 有一次解析/业务纠正机会；Patcher 生成与实际执行共用一次修复机会。API/文件错误立即停止，其他技术错误在额度耗尽后标记该候选失败。

验收由 Python 根据选择集真实投票执行，不依赖 LLM 自评。拒绝原因保存在 acceptance.json / selection.json，不送入下一轮模型反馈。后续发现集和训练结果来自最终接受的版本。
