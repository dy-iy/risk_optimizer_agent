# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.4

## 摘要

- 降低主类别最低分阈值至0.20，弱信号亦能进入对应类型（解决大量类型错配为“无明显风险”的问题）
- 收紧 score_regulatory：引入缓和/否定词对非制裁讨论封顶0.4，中等/弱信号进一步降权
- 强化 score_macro：必须加密关联或强冲击才触发，排除纯观点评论
- score_hack：扩展攻击关键词（量子计算等），并对防御/澄清语境设定0.3分数上限
- score_volatility 提高振幅门槛并防止漏报，score_whale 引入500万美元金额门槛并强化意图识别

## 变更明细

### 1. shared logic / primary type selection
- 原因: 大量真实风险被归为“无明显风险”，漏报严重
- 动作: PRIMARY_TYPE_MIN 从 0.35 降至 0.20，使得微弱信号也能成为主类别
- 风险: medium

### 2. score_regulatory
- 原因: 误报最多（57次触发），大量普通法规讨论获高分
- 动作: 增加 NEG_REGULATORY_DISCUSS 否定缓和词，当无强负面动作时分数上限 0.4；中等/弱信号进一步削弱
- 风险: low

### 3. score_macro
- 原因: 误报多，一般宏观分析、油价观点被触发
- 动作: 强制须有加密市场关联或强冲击事件，评论观点在无强冲击时直接返回 0
- 风险: low

### 4. score_hack
- 原因: 新型攻击（量子、社会工程）漏报，且防御/澄清语境仍给极高分
- 动作: 新增攻击关键词（量子计算、社会工程攻击、内部热钱包被盗等），对防御/防范词设置上限 0.3，并降低有效主类别阈值
- 风险: medium

### 5. score_volatility
- 原因: 漏报行情波动（53条类型错配），且过度触发普通暴涨
- 动作: 提高给分幅度阈值（涨跌幅>15%才可得0.5+），同时保留强波动词高分，防止一般炒作误报
- 风险: low

### 6. score_whale
- 原因: 漏报巨鲸行为（29条错配），且将其他大额转移误判为风险
- 动作: 引入最低 500 万美元金额门槛，并优先识别抛售/转入交易所等恶意行为，常规仓位调整降权
- 风险: low

## 注意事项

- 主类别阈值降低需配合各scorer的误报抑制，否则可能引入新噪音
- score_hack 的防御词列表需持续观察，避免过度压制真实攻击新闻
- 金额门槛改动可能漏掉未提及金额但行为明确的小额恶意转账（如后续有案例需调整）
- score_infra/score_team 因证据不足暂未改动，后续需收集更多案例

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v11\scripts\risk_labeler_v11.py
- analysis_json: D:\risk_optimizer_agent\v11\reports\analysis\risk_labeler_v11_analysis_llm.json
