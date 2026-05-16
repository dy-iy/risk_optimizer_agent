# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.1

## 摘要

- 修复 false_negative 和 type_mismatch 恶化：大幅激活 score_volatility、补强 score_regulatory/whale/liquidation/team/infra，并引入主类别最小激活阈值和领先 margin
- 同时降低 score_hack/score_macro/score_fraud 的高估映射，引入 hack 严重性分层，收紧非威胁内容的强度，减少 overestimate 并平衡多指标
- 优化主类别竞争逻辑：设定 MAX＜0.15 强制无明显风险，0.15-0.25 区间需要领先 margin，避免弱信号带出错误类别

## 变更明细

### 1. score_volatility
- 原因: 黄金标准中大量行情波动被误判为无明显风险（type_mismatch 55 例、低估 42 例），当前 scorer 几乎完全沉默
- 动作: 扩充波动关键词（暴跌、暴涨、价格跳水、大幅回调、恐慌性下跌等），定义强市场波动标记；当存在强波动词时忽略部分日常短语抑制，给予低分激活（base 0.18-0.35）；同时保留对普通涨跌的抑制
- 风险: low
- Guardrail: 强波动标记仅用于激活，且基础分控制在 0.18-0.30 范围，不会将普通行情分析推至 high；日常短语如“反弹”仍被抑制
- 验证: 重点关注 type_mismatch 中“异常行情波动风险→无明显风险”对数量是否显著下降；监控 score_diff 低估日志中波动类文章是否开始有非零得分；验证 false_positive 增长可控

### 2. score_regulatory
- 原因: 监管法律类大量漏判为无明显风险（mismatch 30 例），且 scorer 在低估案例中几乎零触发
- 动作: 增加监管触发词（听证、CFTC、FCA、规则、授权、征询意见、政策变动等），并降低弱监管词的强度映射；结合主类别阈值防止过度抢占
- 风险: medium
- Guardrail: 新增关键词多数嵌入现有弱信号路径，只给低分（0.05-0.15）；并配合主类别 margin 逻辑，避免普通行业文章被错误归类为监管风险
- 验证: 检查 type_mismatch 中“监管与法律风险→无明显风险”数量下降，同时“无明显风险→监管”错配不增加；确认 score_diff 低估中监管案例得分提升至接近 20-30

### 3. score_hack 严重性分层与映射校准
- 原因: score_hack 同时是 FP 第一高和 FN 主要源头，高估和低估并存，需区分威胁讨论与真实攻击
- 动作: 定义高严重性指示词（被盗、窃取、实际损失、攻击成功等），有 high_sev 时允许高分（0.7+）；无 high_sev 时大幅收紧映射：金额路径上限降至 0.45，攻击证据路径降至 0.3；增强安全上下文归零规则（安全趋势、安全峰会等）
- 风险: medium
- Guardrail: 高严重性词覆盖所有 FN 案例（量子攻击、密钥恢复等），确保它们得分 0.7+；收紧路径均有明确条件，不破坏真实漏洞场景召回
- 验证: 监控 false_negative 中链上漏洞/攻击风险数量下降，overestimate 中 hack 占比减少；复查 FN 样本 news_id 222/797/12 等是否得分升至 0.7+；FP 样本中 hack 高分段应减少

### 4. score_macro 强度收紧
- 原因: 宏观冲击风险 FP 数量高，常见地缘政治讨论被误推至 medium-high
- 动作: 降低 linked 且 strong 情况下的基础分（0.45→0.30），增加会议/论坛等非冲击语境排除，保留预测和评论折扣
- 风险: low
- Guardrail: 真实重大宏观冲击（如军事冲突、制裁）仍能通过 strong 词和折扣后得分 0.25-0.35，足以映射到 25-35 风险分；配合主类别阈值压制弱信号
- 验证: 检查 FP 中宏观/政策冲击类别数量下降；gold 明显宏观冲击案例仍保持触发；macro 类 mismatch 无明显→宏观减少

### 5. score_fraud 强度映射校准
- 原因: 诈骗类单次触发即给 0.88 导致严重高估（gold 25-35 对上 rule 88）
- 动作: 严重诈骗词（诈骗/庞氏/跑路）基础分降至 0.45，钓鱼等降至 0.25；事后追回再折扣；fraud_extra 降至 0.12
- 风险: low
- Guardrail: 真实大型骗局可叠加其他信号（金额等）达到 0.5-0.6，体现较高风险但不过分；诈骗类 FN 极少，风险可控
- 验证: 观测 score_diff overestimate 中 fraud 案例的差距（rule-gold）缩小；FP 中诈骗类高分段减少

### 6. 主类别仲裁逻辑（primary_type）
- 原因: 低分微触发导致无明显文章被错误标为特定风险（监管/漏洞/宏观），需要更强的最低激活阈值和领先 margin
- 动作: 引入规则：最大分 <0.15 时强制“无明显风险”；最大分在 0.15-0.25 且与第二名差距 <0.08 时退回无明显风险；保留原竞争逻辑但仅在高信度时启用
- 风险: medium
- Guardrail: 只有真正存在合理风险信号的类别才能胜出，并确保弱真实风险不会被错杀；需验证关键弱风险类别（如 low 级监管提醒）仍有机会标注
- 验证: 全面检查 type_mismatch 中 gold 无明显→规则各类的数量下降；同时 gold 真实类别的召回不显著恶化；false_negative 轻微上升可接受

### 7. score_whale 补强
- 原因: 20 例黄金巨鲸风险被判为无明显风险，scorer 几乎不触发
- 动作: 新增强鲸鱼行为关键词（鲸鱼、巨鲸、大户、筹码异动等），当命中时跳过 NEG_WHALE_FALSE 的强排除，给予低至中分（0.1-0.25）
- 风险: low
- Guardrail: 明确行为词受控，非行为描述仍受 NEG_WHALE_FALSE 约束；内部转账等否定词仍然生效
- 验证: 监测 gold 巨鲸风险文章的 score_whale 是否激活，mismatch 对“大额转账→无明显”减少

### 8. score_liquidation 补强
- 原因: 爆仓清算类存在低估，trigger_count 极低
- 动作: 当强清算信号或金额出现时忽略 NEG_LIQ_FALSE 中的通用词阻拦，确保清算事件不被埋没；扩充少量关键词
- 风险: low
- Guardrail: 仅在有强信号（清算金额、已清算等）时放行，避免误触普通交易量讨论
- 验证: 检查低估案例中 liquidation 类是否得分，mismatch 中相关方向改善

### 9. score_team / score_infra 轻微补强
- 原因: 团队异常和基础设施异常存在漏报
- 动作: 增加少量团队异常词（核心团队变动、内斗等）和基础设施故障词（验证者离线、L1中断等）；适当降低团队类基础分以控制高估
- 风险: low
- Guardrail: 新词仍在否定词保护范围内，且 infrastructure 原有规则已有一定覆盖
- 验证: 观察对应黄金类别的召回率提升

## 注意事项

- 重点测量 type_mismatch_rows 总数以及‘异常行情波动→无明显’、‘监管→无明显’、‘无明显→监管’、‘无明显→漏洞/宏观’的变动；务必确认 patch 后这些错配大幅下降
- 监控 false_negative_rows 尤其是链上漏洞/攻击风险的数量是否从 7 降低到 3 以下
- 检查 score_diff overestimate 中‘链上漏洞/攻击风险’类别的高分情况是否收缩，mean_rule_minus_gold 向零靠近
- 注意 score_volatility 新增触发后是否导致大量日常行情文章被误标为非明显风险类别，可抽样检查新增触发的文章实际内容
- 主类别 margin 阈值（0.15,0.08）需要在下一轮根据验证集调整，观测各类真实低风险文本是否仍能正确归类

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\versions\v18\scripts\risk_labeler_v18.py
- analysis_json: D:\risk_optimizer_agent\versions\v18\reports\analysis\risk_labeler_v18_analysis_llm.json
