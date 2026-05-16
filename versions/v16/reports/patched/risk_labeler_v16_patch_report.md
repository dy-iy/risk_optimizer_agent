# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v3.0

## 摘要

- score_hack 强化非攻击语境过滤并补充漏报关键词，score_regulatory 严格区分执法与讨论大幅降低非恶意评分
- score_volatility 重构触发机制解决大量漏报同时抑制日常行情，score_macro 收紧至真实地缘事件并过滤评论观点
- score_whale 提高金额门槛并优化行为词避免普通持仓报告误触发，其余 scorer 仅做最低必要调整

## 变更明细

### 1. score_hack
- 原因: 主要误报源（38 次触发）且存在漏报，安全讨论/产品发布被误判为攻击风险
- 动作: 新增 NEG_HACK_SAFETY 与 NEG_HACK_PRODUCT 否定词库，安全运营、产品上线等无实际攻击时直接归零或压至 0.2 以下；补充 KW_HACK_EXTRA 漏报关键词（严重漏洞、冻结黑客地址等）
- 风险: medium

### 2. score_regulatory
- 原因: 72 例“无明显风险”误标为监管风险，过度对讨论/评论赋分
- 动作: 重构打分流程，优先检测纯讨论/无执法行动语境（上限 0.1），强化有利结果抑制，限制非行动文章得分 ≤0.3
- 风险: medium

### 3. score_volatility
- 原因: 46 例真实异常行情漏报，同时对日常行情存在误报
- 动作: 增加暴跌/恐慌抛售等强信号独立分支，高召回；添加日常行情综述过滤，调高非百分比触发最低分并压降低风险日常波动得分
- 风险: medium

### 4. score_macro
- 原因: 误报 11 例且类型错配多，观点/预测性文章被触发
- 动作: 新增纯评论/预测过滤规则，非强冲击且无加密关联时直接返回 0，弱信号上限降至 0.2
- 风险: low

### 5. score_whale
- 原因: 17 例“无明显风险”误标为巨鲸风险，普通持仓报告被触发
- 动作: 将非行为类触发的最低金额要求提高至 10M，低于该阈值若无强行为关键词仅给 0.05 分；补充巨鲸减持/解锁等行为词
- 风险: low

## 注意事项

- 新否定词/关键词需后续通过误报/漏报回归验证，尤其 score_hack 的语义区分是否过于严格可能抑制真实事件
- score_volatility 引入的强信号分支可能对其他正常波动（如单日大跌 10%）产生过度反应，需观察阈值
- 所有修改保持 CSV 输入输出不变，PRIMARY_TYPE_MIN 仍为 0.12
- 仅对证据充分的 scorer 做了改动，score_solvency/score_fraud/score_stablecoin 等保留原逻辑
- 若英语/大小写问题，代码中统一使用 has_any 部分已做小写转换（但未引入新依赖）

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v15\scripts\risk_labeler_v15.py
- analysis_json: D:\risk_optimizer_agent\v15\reports\analysis\risk_labeler_v15_analysis_llm.json
