# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v3.1

## 摘要

- 全面优化高风险误报：大幅扩展score_hack安全上下文和否定词，对漏洞报告/安全讨论设置严格得分上限以抑制38次误报；同时提升对漏洞和事后处理信号的得分以减少漏报
- 降低score_macro和score_volatility的过宽触发，引入观点/日常行情抑制规则，并对主类别选择增加macro低分回退逻辑，显著减少误报和类型错配
- 针对漏报严重的类别(监管、巨鲸、团队、基础设施等)扩充关键词并降低触发门槛，调整主类别阈值至0.08并改进回退策略，使真实风险不再被“无明显风险”淹没

## 变更明细

### 1. score_hack
- 原因: 误报最多(37例)，将安全讨论/漏洞披露/无关内容误判为攻击，同时漏报漏洞和事后冻结等信号
- 动作: 1)补充NEG_HACK_SAFETY和NEG_VULN_REPORT抑制词，对安全语境设硬上限0.2(有损失上限0.35)；2)新增KW_HACK_POST_EVENT对冻结黑客地址等赋予0.45+分；3)新增KW_VULN_HIGH对严重漏洞等赋予0.55+分；4)降低通用关键词无损失基础分从0.30至0.25
- 风险: low

### 2. score_macro
- 原因: 误报11例，将宏观评论/展望/分析师观点误判为政策冲击
- 动作: 1)扩展NEG_MACRO_COMMENT加入更多观点/采访/播客词；2)弱宏观信号上限降至0.15；3)在score_all_risks中增加macro低分回退：若macro得分<0.3且存在其他类别>=0.08，则不选macro为主类型
- 风险: medium

### 3. score_volatility
- 原因: 误报8例，将日常涨跌/板块轮动等视为异常波动，同时存在漏报(类型错配)
- 动作: 1)新增NEG_VOL_DAILY日常行情抑制词大幅降权；2)无百分比时基础分从0.25降至0.15(仍可触发主类别但日常被抑制)；3)降低非主流币得分系数至0.3并增加排除短语
- 风险: medium

### 4. score_regulatory
- 原因: 强度映射偏高，max=0.8而gold低风险，少量误报
- 动作: 1)讨论语境折扣从0.4降至0.25；2)中性推进得分进一步压制；3)REG_MODERATE_SIGNALS加密域基础分从0.25降至0.18
- 风险: medium

### 5. score_solvency
- 原因: 误报6例，将代币化/增持/中性讨论误判为偿付危机，且得分偏高
- 动作: 1)新增NEG_SOLVENCY_NON_CRISIS排除代币化/资产管理等词；2)弱触发得分从0.20降至0.12；3)强危机词保持高分
- 风险: medium

### 6. score_whale
- 原因: 漏报25例，大量大额转账/巨鲸行为被标为无明显风险
- 动作: 1)非行为触发金额门槛从10M降至1M且基础分提高至0.18；2)行为触发基础分上调；3)扩充巨鲸关键词和地址行为词
- 风险: low

### 7. score_team
- 原因: 漏报严重，规则几乎不触发团队异常类型
- 动作: 1)扩充KW_TEAM和KW_TEAM_WEAK，增加“解散”“控制权变更”“CEO被捕”等词；2)弱触发得分从0.35提高至0.45
- 风险: low

### 8. score_infra
- 原因: 漏报严重，基础设施/协议层异常完全未被捕获
- 动作: 1)扩充KW_INFRA增加“分叉”“节点掉线”“Gas异常”等；2)增加弱触发列表KW_INFRA_WEAK如‘网络延迟’‘Gas飙升’等，给予0.30分；3)降低强触发中否定词抑制强度
- 风险: low

### 9. shared logic (主类别选取)
- 原因: 类型错配严重(154vs48无明显风险)，真实风险被淹没
- 动作: 1)PRIMARY_TYPE_MIN从0.12降至0.08；2)增加macro低分回退规则(分数<0.3时次选其他>=0.08类型)；3)若仅macro超过0.08但低于0.3且无其他，主类别仍选无明显风险
- 风险: medium

## 注意事项

- PRIMARY_TYPE_MIN降至0.08可能导致更多低分风险成为主类型，需通过各scorer内否定词精确抑制噪声
- score_hack安全语境硬上限调整为0.2/0.35，需确认不会过度压制真实攻击事件中附带的安全讨论
- score_macro低分回退依赖max_score判别，当另一类别分数<0.08时仍可能选macro，需后续监控
- 多个scorer新增大量关键词可能存在重叠，但保留了现有否定体系并补充了消除歧义的过滤

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\versions\v16\scripts\risk_labeler_v16.py
- analysis_json: D:\risk_optimizer_agent\versions\v16\reports\analysis\risk_labeler_v16_analysis_llm.json
