# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.2

## 摘要

- 针对 score_hack 的误报大幅优化：增强否定语境抑制、加入减权列表、新增真实攻击关键词（社会工程攻击等）
- 修复 score_volatility 和 score_whale 的严重漏召回：扩展波动及巨鲸关键词、降低金额门槛、移除过度排除逻辑
- 收紧 score_regulatory、score_macro、score_liquidation 的触发条件，减少假阳性并优化主类别竞争
- 对 score_fraud 添加事后处理降权规则，修正历史补偿事件的高分误判

## 变更明细

### 1. score_hack
- 原因: 误报样本中 49 次误判为攻击风险（大多为非攻击内容）；唯一漏报的社会工程攻击未能识别
- 动作: 新增 NEG_HACK_DOWNWEIGHT 列表，将"用户资金未受影响"等从完全否定改为减权；补充 KW_HACK 以包含"社会工程攻击""凭证泄露"等；降低纯报告/历史语境下的分数上限
- 风险: low

### 2. score_volatility
- 原因: 误报 19 次且漏召回 58 次真实波动风险（如"最大痛苦点"）
- 动作: 扩展 KW_VOL_MISS 增加"最大痛苦点""信任危机"等词；增加对"交易量创新高"等非负面波动的抑制；调整分数映射使弱波动词得分更低
- 风险: medium

### 3. score_whale
- 原因: 漏召回 32 次真实巨鲸风险（如"巨鲸开仓"），现有排除开平仓的逻辑过度抑制
- 动作: 移除 NEG_WHALE_POSITION 的整体排除，改为减权；降低强信号金额门槛至 100 万美元起步；扩展巨鲸行为关键词如"巨鲸开仓""大户头寸"
- 风险: medium

### 4. score_regulatory
- 原因: 误判监管风险 20 次，漏判 25 次，主要因缺失具体执法动词且受调查类文章干扰
- 动作: 添加"国会批评""监管阻力""法案推进"等关键词；提高对纯调查文章的抑制（无具体执法动作时降分）
- 风险: medium

### 5. score_macro
- 原因: 将油价、AI等非直接加密宏观冲击误判为风险 15 次
- 动作: 强制要求文本中提及加密货币市场影响（如"加密市场""比特币"），否则分数限制在 0.15 以下
- 风险: low

### 6. score_fraud
- 原因: 历史赔偿新闻（如 OneCoin 赔偿）分数高达 0.88，与实际风险不符
- 动作: 新增事后处理降权规则：当出现"赔偿""追回""警方"等词时，强度上限设为 0.35
- 风险: low

### 7. score_liquidation
- 原因: 在策略分享和期货产品上线等非事件上误触较高分
- 动作: 要求有明确清算压力/金额描述（如"X亿美元""清算价"），同时强化排除产品上线和策略分析词
- 风险: medium

## 注意事项

- 修改后需用分析报告中的高误报样本（如 news_id 315, 283, 904）进行回归测试，确保 score_hack 分数降至 0.3 以下
- 检测 "Hyperliquid 巨鲸开仓"、"最大痛苦点"、"社会工程攻击" 等新案例能否被正确识别并给出合理分数
- 注意 NEG_WHALE_POSITION 移除后可能引入的普通交易噪音，后续可根据误报情况微调

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v9\scripts\risk_labeler_v9.py
- analysis_json: D:\risk_optimizer_agent\v9\reports\analysis\risk_labeler_v9_analysis_llm.json
