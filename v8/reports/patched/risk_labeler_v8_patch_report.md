# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.1

## 摘要

- score_hack：移除单独“漏洞”词，增加强攻击动作关键词，新增否定语境和报告排除，降低仅讨论漏洞的得分；新增社会工程、热钱包等术语覆盖漏报
- score_macro：拆分为强/弱信号，仅强负面事件给高分，普通宏观讨论降分，增加预期/概率否定词
- score_volatility：扩展波动相关关键词（最大痛苦点、期权到期等），强化交易量/非价格排除，降低无强冲击词的百分比触发分数
- score_regulatory：新增中性表达排除列表，弱化无负面行动的监管讨论得分，限制仅负面行动词汇触发中高分
- score_whale：扩充抛售/清仓/解锁等巨鲸行为词，提高转账类动作的金额门槛，对冲无风险误报
- score_team / score_infra：基于反馈系统性地扩展关键词，覆盖团队异常和基础设施异常场景
- score_liquidation：增加讨论/产品上线排除词，抑制非实际强平事件的误报
- 主类别选取引入最低0.30阈值，避免低分噪声成为主要风险类型

## 变更明细

### 1. score_hack
- 原因: 误报触发最多（49次），对“漏洞”一词反应过强，同时漏报新型社会工程攻击
- 动作: 将KW_HACK重构为强攻击动作词列表，移除单独的“漏洞”；新增NEG_HACK_PAST、NEG_VULN_REPORT否定列表；仅当存在攻击动作词或金额损失时给分，仅含“漏洞”不触发；添加“社会工程攻击”“热钱包被盗”“内部攻击”“AI攻击”
- 风险: medium

### 2. score_macro
- 原因: 对降息概率、油价分析等宏观讨论给出偏高评分，导致误报36次
- 动作: 将KW_MACRO拆分为强信号（危机/冲击/战争等）和弱信号（利率/油价等）；弱信号基础分降为0.20；新增否定词NEG_MACRO_FORECAST削弱预期类文本
- 风险: medium

### 3. score_volatility
- 原因: 普通市场新闻被误标，但真实波动风险（如最大痛苦点）漏报
- 动作: 扩充KW_VOL_MISS（期权到期、轧空、信任危机等）；扩大NEG_VOL_FALSE覆盖交易量/地址数等非价格指标；仅百分比≥15%但无强冲击词时降低基础分
- 风险: medium

### 4. score_regulatory
- 原因: 官员中性讲话被误标（gold无风险→规则监管39例），但真实负面行动未增强
- 动作: 新增NEG_REGULATORY_NEUTRAL排除推动监管、框架等中性表述；弱信号+主体组合分数从0.35降至0.20；确保仅负面行动（起诉/罚款）给出0.80
- 风险: low

### 5. score_whale
- 原因: 普通大额转账误报，但抛售、解锁等巨鲸行为漏报（gold巨鲸→无风险23例）
- 动作: 新增KW_WHALE_BEHAVIOUR（准备出售/抛售/清仓/大额解锁等）；弱信号金额门槛从1M提高至10M，强信号无金额门槛降至0.15；保留对真实转账的适度响应
- 风险: medium

### 6. score_team / score_infra
- 原因: 两类完全未触发，导致项目治理和基础设施异常风险系统性漏报
- 动作: score_team扩充“CEO离职”“团队内讧”“项目方出货”等；score_infra扩充“手续费异常”“预言机故障”“跨链桥暂停”等；沿用现有框架
- 风险: medium

### 7. score_liquidation
- 原因: 对KOL分享策略、期货上市等非事件文本触发，导致误报
- 动作: 新增NEG_LIQ_DISCUSSION排除策略/分析类描述，NEG_LIQ_PRODUCT_LAUNCH排除产品上线新闻
- 风险: low

### 8. shared logic
- 原因: 低分（<0.30）导致无关文章被强加一个风险主类别
- 动作: 主类别选取增加最低阈值0.30，低于该值强制设为‘无明显风险’
- 风险: low

## 注意事项

- 阈值0.30可能使部分真实但得分偏低的弱风险被忽略，需后续观察漏报；但当前误报远多于漏报，收益大于风险
- score_hack 新增许多关键词，虽未改变接口，但可能仍需验证部分专业术语的编码/大小写匹配
- 所有修改基于 analysis_llm.json 中的统计和模式，未使用软标签微调，仍属于规则修补
- 保留 CSV 输入输出协议和所有函数名，兼容已有流水线

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v7\scripts\risk_labeler_v7.py
- analysis_json: D:\risk_optimizer_agent\v7\reports\analysis\risk_labeler_v7_analysis_llm.json
