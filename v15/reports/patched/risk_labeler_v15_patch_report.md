# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.7

## 摘要

- 主类别阈值从0.20降至0.12，避免大量真实低风险被归为“无明显风险”
- score_hack：强化否定语境，增加“无损失”“安全运营”等过滤，并将多种安全合作/审计类内容直接抑制
- score_volatility：引入主流币种/市场整体性要求，对单个小币种波动大幅降权，低波动阈值下移以提升召回
- score_solvency：大幅扩充强信号词（坏账、暂停赎回等），增加弱触发分支确保类别可被选中
- score_liquidation：将最高得分限制在0.65，防止虚高
- score_regulatory、score_macro、score_whale：微调得分上下限与排除逻辑，减少误报并提升弱信号检出

## 变更明细

### 1. 主类别选取逻辑
- 原因: type_mismatch 中151例规则输出“无明显风险”，而实际金标存在风险类别，说明阈值太高导致低分风险被遗漏
- 动作: 将 PRIMARY_TYPE_MIN 从 0.20 降低至 0.12
- 风险: medium

### 2. score_hack
- 原因: 误报最大来源(37次FP)，安全公告/防护新闻被误标为高风险；需更严格否定语境和攻击证据
- 动作: 增加强否定词列表（无资金损失/安全运营/未受影响/防护等），匹配且无实际损失时直接返回0.0；扩大 NEG_VULN_REPORT 覆盖面
- 风险: low

### 3. score_volatility
- 原因: 误报较多(20次FP)，单个币种普涨被标为异常波动；而type_mismatch中57次行情风险被遗漏，需要双重调整
- 动作: 引入主流币种判断列表，非主流币种波动得分乘以0.4；保持低百分比的弱触发，以保证至少能被主类别选中
- 风险: medium

### 4. score_solvency
- 原因: FN和score_diff统计显示该scorer几乎从未触发，而金标中存在偿付能力/流动性风险，关键词严重不足
- 动作: 扩充关键词至20+（坏账、暂停赎回、准备金率、资不抵债等），并增加弱分支（含“流动性”+“风险”等给0.2）
- 风险: low

### 5. score_liquidation
- 原因: score_diff样本中分数虚高(规则85 vs 金标45)，强度映射过于激进
- 动作: 将最大可能得分限制在0.65，并微调金额阶梯阈值
- 风险: medium

### 6. score_regulatory
- 原因: 言论/讨论类被标为高分，type_mismatch中21例无明显风险被误判为监管
- 动作: 当存在讨论/质疑折扣时，强负面执法得分上限从0.45降至0.30，弱信号保持低分
- 风险: medium

### 7. score_macro
- 原因: 分析/书评/会议发言被误判为冲击风险(10次FP)
- 动作: 在 NEG_MACRO_COMMENT 基础上增加“书评”“发言”“对话”“嘉年华”等，无强冲击词时直接返回0
- 风险: low

### 8. score_whale
- 原因: type_mismatch中28次巨鲸风险被遗漏，低金额或行为弱信号未能达到原阈值
- 动作: 确保即便金额很小，只要有鲸鱼行为关键词就至少返回0.12，降低高风险门槛
- 风险: low

## 注意事项

- PRIMARY_TYPE_MIN降至0.12后，可能导致一些原本低于0.20的弱信号被选中，但配合各scorer误报收紧，整体风险可控
- score_volatility的主流币判断依赖关键词列表，可能漏掉部分小众但确实市场冲击的情况，需后续观察
- score_liquidation上限0.65可能对极端大规模清算事件略显保守，但可先上线看效果再调整
- 所有修改均保持原有函数签名和CSV输出格式，可直接替换运行

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v14\scripts\risk_labeler_v14.py
- analysis_json: D:\risk_optimizer_agent\v14\reports\analysis\risk_labeler_v14_analysis_llm.json
