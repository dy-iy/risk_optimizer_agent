# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.2

## 摘要

- 收紧 score_volatility、score_whale、score_regulatory 的触发条件以降低误报
- 扩充 score_hack 关键词及基础分以修复漏报，并添加否定语境防止误报
- 细化 score_liquidation 否定词，降低防护性描述得分
- 调整巨鲸金额阈值并添加 ETF/机构购买等排除词
- 优化波动风险捕获逻辑，增加大幅波动描述词，降低阈值

## 变更明细

### 1. score_volatility
- 原因: 大量常规市值/交易量报告被误报为波动风险，同时真实大幅波动漏报严重
- 动作: 添加 NEG_VOL_FALSE 排除词（总市值、24小时交易量、占有率等），从 NEG_POSITIVE_MOVE 中移除剧烈上涨词并扩充 KW_SHOCK/KW_VOL_MISS，在无强关键词但百分比≥30%时增加中等评分，降低百分比阈值到10%
- 风险: medium

### 2. score_whale
- 原因: ETF购买、上市公司购入等中性大额交易被误报为巨鲸风险，且小额转账触发过多
- 动作: 大幅提高金额阈值（弱信号≥5000万美元，强信号≥1000万美元），在 NEG_WHALE_FALSE 中添加 ETF、资产管理公司、贝莱德、富达、灰度、MicroStrategy、上市公司、购入等排除词，内部转移/无风险情况弱化得分
- 风险: medium

### 3. score_regulatory
- 原因: 解除禁令、允许、不视为证券等正面/中性政策新闻被高频误报
- 动作: 新增 NEG_REGULATORY_POSITIVE 列表（解除、允许、不视为、裁定…不属于证券、许可、合法化等），当这些词出现且无强负面动作时降为低分；下调弱信号分支分值
- 风险: medium

### 4. score_hack
- 原因: 真实攻击事件大量漏报或得分过低（仅0.3），且误报样本出现0.98高分
- 动作: 扩充攻击类关键词（利用、被利用、遭受攻击、攻击事件、攻破、窃取、被黑等），将有金额的加密攻击基础分提至0.85+；增加 NEG_HACK_FALSE 否定词（追回、修复、报告漏洞等）降低误报；非加密领域维持0.30
- 风险: low

### 5. score_liquidation
- 原因: 对“降低清算风险”等防护性描述给出0.85高分
- 动作: 新增 NEG_LIQ_PROTECT 列表（降低清算风险、避免清算、清算保护等），命中时返回0.0；弱化弱信号基础分
- 风险: low

## 注意事项

- 修改后需重新运行全量测试，观察误报、漏报及类别错配指标是否改善
- 金额阈值和参数（x0, scale）可根据实际数据分布进一步微调
- 主类别选择仍用 max(raw_scores)，不引入复杂加权，依赖各 scorer 分数校准
- 团队、基础设施、偿付能力等类别因证据不足未做调整，请持续监控

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v3\scripts\risk_labeler_v3.py
- analysis_json: D:\risk_optimizer_agent\v3\reports\analysis\risk_labeler_v3_analysis_llm.json
