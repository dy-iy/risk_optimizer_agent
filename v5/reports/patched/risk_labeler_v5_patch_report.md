# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.2

## 摘要

- 收紧 score_volatility 触发条件与关键词，排除财务利好与纯行情播报，降低正面大涨的误报。
- 重构 score_hack，区分讨论性与实际攻击，引入金额与损失词触发，提升真实攻击得分，减少 FP。
- 增加 score_regulatory 的极性判断，对解除禁令等正面结果大幅降分，修正无明显风险误判为监管风险。
- 扩充 score_macro 关键词并提升基准分，解决宏观/地缘冲击漏报。
- 对 score_whale 和 score_liquidation 增加计划/流动性非风险语境抑制，降低错配。

## 变更明细

### 1. score_volatility / shared logic
- 原因: FP 中 78 个异常行情误报，绝大部分因‘暴涨’等正面词触发；财务利好被标为风险。
- 动作: 从 KW_SHOCK 移除‘大涨’‘飙升’‘狂飙’等纯正面词；新增财务利好否定词；当无负面后果时对高分打折扣。
- 风险: medium

### 2. score_hack / shared logic
- 原因: FP 中讨论性内容给到 0.95，FN 中真实攻击仅 0.3；漏掉路由器/社会工程等新型攻击。
- 动作: 扩充 CRYPTO_DOMAIN；区分提及/攻击：无损失词给 0.35，有损失词给 0.60，有金额按公式；调整 NEG_NON_CRYPTO_HACK 仅当不涉及加密域才禁用。
- 风险: low

### 3. score_regulatory / shared logic
- 原因: 37 个 gold 无明显风险被错判为监管风险，因‘禁令’‘监管’未区分解除/胜诉等正面结果。
- 动作: 将 NEG_REGULATORY_POSITIVE 检查前置，匹配到正面词且无负面行动时直接返回 0.10。
- 风险: low

### 4. score_macro / shared logic
- 原因: type_mismatch 中 11 个宏观风险漏报，score_macro 几乎不触发。
- 动作: 扩充关键词至 30+ 地缘/政策词；基础分提至 0.45，不依赖百分比。
- 风险: medium

### 5. score_whale / shared logic
- 原因: 34 个 gold 无明显风险被误判为巨鲸风险，因讨论/计划性内容得分过高。
- 动作: 增加未来计划/预期词折扣，相应内容得分乘以 0.4。
- 风险: low

### 6. score_liquidation / shared logic
- 原因: 基金/流动性产品描述误触清算/爆仓风险。
- 动作: 新增非风险流动性词（如代币化流动性），若仅有弱爆仓词则返回 0.0，否则降分。
- 风险: low

## 注意事项

- 改动对英文大小写的影响未全面处理，但中文关键词不受影响。
- score_volatility 的负面后果折扣可能抑制少量真实波动，需后续监控。
- score_hack 的无损失词判定依赖于‘损失’‘被盗’等子串，可能错过某些表达。
- 新增关键词和否定词数量有限，需根据后续分析补充。
- score_team 和 score_infra 关键词小幅扩充，但未做结构性调整。
- score_fraud、score_outage、score_solvency、score_stablecoin 因证据不足维持原状。
- 修复 NEG_REGULATORY_POSITIVE 列表末尾的字符串字面量错误，将 "胜诉"," 改为 "胜诉"

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v4\scripts\risk_labeler_v4.py
- analysis_json: D:\risk_optimizer_agent\v4\reports\analysis\risk_labeler_v4_analysis_llm.json
