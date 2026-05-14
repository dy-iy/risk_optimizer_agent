# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.2

## 摘要

- 收紧 score_volatility 与 score_macro 触发条件以减少行情/宏观误报，增加否定词和加密相关性约束
- 扩充 score_hack 关键词库并提高攻击事件的基准分以解决漏报
- 细化 score_regulatory 正负面判断，避免将解除禁令等中性利好判为高风险
- 严格限定 score_whale 仅链上转移行为，增加持有声明否定词
- 调整 score_liquidation 强度映射并厘清与偿付能力边界，引入主类别二次裁决策略

## 变更明细

### 1. score_volatility
- 原因: 误报严重，大量普通行情被误判为异常波动
- 动作: 增加温和行情词（反弹/回升等）至否定列表，新增‘预测/调查/预期’等前瞻类否定词，并强制要求极值词或10%以上涨跌幅才可能触发强分
- 风险: medium

### 2. score_macro
- 原因: 对非加密经济新闻过度触发，导致宏观风险误报
- 动作: 增加加密市场相关性检查，当文本中无任何加密/区块链关键词时直接返回 0
- 风险: low

### 3. score_hack
- 原因: 对链上漏洞/攻击事件召回不足且得分偏低
- 动作: 扩充黑客攻击关键词（社会工程/量子/后门/合约暂停等），提高明确攻击事件的基础分至 0.70+，并调整无金额时的映射
- 风险: low

### 4. score_regulatory
- 原因: 将中性利好监管新闻判为高风险，造成大量错配
- 动作: 细化负面执法动作词库（罚款/起诉/禁令等），仅当出现负面动作且无正面结果时给高分；正面监管语境得分降至 0.15
- 风险: medium

### 5. score_whale
- 原因: 持仓声明、机构描述被误判为巨鲸风险
- 动作: 要求同时出现‘转入/转出/转移’等动作词或‘地址’等链上特征词；增加‘声称/持仓披露/资产组合’等否定词
- 风险: medium

### 6. score_liquidation
- 原因: 误将抵押品使用率、未实现损失等判为清算风险，且分数虚高
- 动作: 将‘接近清算/抵押品使用率/未实现损失’归入偿付能力或给予极低分；为实际清算事件调低分数上线
- 风险: medium

### 7. shared logic (主类别选择)
- 原因: 减少单一项高分导致的误判
- 动作: 当最高分来自易误判的高 FP scorer 且分数 <0.5 时，强制归为‘无明显风险’
- 风险: medium

## 注意事项

- 请重点验证 score_volatility 与 score_macro 误报是否显著下降，尤其是普通行情/经济预报类文章
- 确认 score_hack 对近期社会工程、漏洞利用事件能稳定打出 0.6 以上，且非攻击类安全提示不被拉高
- 观察 score_regulatory 对 SEC 和解、法院裁定非证券等新闻是否不再出现 0.8 高危
- 留意 score_whale 是否仍能捕捉到链上大额转账的卖出信号，同时过滤持仓声明
- 检查 score_liquidation 与 score_solvency 的分工，确保抵押品接近上限等情况归入偿付能力而非清算高分
- 验证主类别二次裁决逻辑不会将真实中低风险事件错误降级为‘无明显风险’

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v5\scripts\risk_labeler_v5.py
- analysis_json: D:\risk_optimizer_agent\v5\reports\analysis\risk_labeler_v5_analysis_llm.json
