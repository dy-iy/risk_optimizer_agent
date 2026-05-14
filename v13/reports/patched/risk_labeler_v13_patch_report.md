# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.5

## 摘要

- 收紧score_regulatory、score_macro、score_hack、score_volatility的触发条件以大幅降低误报
- 补强score_hack漏回报（增加新型攻击关键词与损失最低分），提升score_volatility、score_whale、score_team、score_liquidation、score_infra的召回与强度映射
- 主类别阈值从0.20提升至0.30以减少低置信度误报，并引入波动类加密域强制检查

## 变更明细

### 1. score_regulatory
- 原因: FP最大来源（51次触发），大量无明显风险文章因弱信号获得0.25-0.8分
- 动作: 增加假设/否定词列表，弱信号（政策讨论、中性推进）加密上限降至0.15，非加密上限0.05；仅强负面执法动作（罚款、起诉等）可获0.8；存在讨论/质疑词时高分加盖
- 风险: low

### 2. score_macro
- 原因: FP第二大来源（40次触发），对地缘/宏观话题过度敏感
- 动作: 强制要求加密市场关联（MACRO_CRYPTO_LINK）；即使强冲击词若无关联最高0.3；增加更多评论/分析排除词；弱信号无关联直接返零
- 风险: low

### 3. score_hack
- 原因: 同时存在FP（26次触发，虚高至0.95）和FN（5例严重攻击仅得0.25-0.3）
- 动作: 扩充关键词（量子攻击、社会工程攻击、私钥恢复等）；增加审计/报告语境抑制列表；对明确损失事件设置最低保底分0.6；强化非加密领域排除
- 风险: medium

### 4. score_volatility
- 原因: FP（19例）与漏归（49例真实波动→无明显风险）并存
- 动作: 增加加密域强制检查；降低百分比触发门槛（10%起给0.1）；扩充中等强度波动词（下跌、上涨、回调等）并给予0.3-0.5基础分；结合时间提示与量化变动提升召回
- 风险: medium

### 5. score_whale
- 原因: 25例真实巨鲸行为漏归为无明显风险
- 动作: 扩充行为关键词（出售、减持、大户减持等）；降低最低金额门槛至100万美元，对1M-5M低额但带行为词给予中等分；行为词触发时金额门槛进一步放宽
- 风险: low

### 6. score_team / score_liquidation / score_infra
- 原因: 这些类别几乎无触发，但gold中存在对应风险
- 动作: score_team新增项目方出货、增发、多签异常等词；score_liquidation提高强平/已清算基础分至0.6并与金额更强挂钩；score_infra新增网络中断、RPC故障、分叉争议等关键词
- 风险: low

### 7. 主类别选择逻辑
- 原因: 低分（0.20-0.30）的单个scorer仍被选为主类别，导致大量误标
- 动作: PRIMARY_TYPE_MIN阈值从0.20提升到0.30，最高分不到0.30时强制归为“无明显风险”
- 风险: low

## 注意事项

- 请在含有大量宏观/法律/漏洞讨论的真实数据集上验证score_regulatory、score_macro、score_hack的误报下降幅度
- 检查score_volatility加密域检查是否导致个别不提区块链术语的真实波动被误过滤（可后续微调CRYPTO_DOMAIN）
- score_hack的保底分0.6需确认不会在审计报告或已修复漏洞中被误触发（已配合NEG_VULN_REPORT抑制）
- 主类别阈值提升可能使一些真·轻度风险被归为无明显风险，需观察type_mismatch中gold为风险但rule无风险的变化

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v12\scripts\risk_labeler_v12.py
- analysis_json: D:\risk_optimizer_agent\v12\reports\analysis\risk_labeler_v12_analysis_llm.json
