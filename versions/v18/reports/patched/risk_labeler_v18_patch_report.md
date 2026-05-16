# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v3.2

## 摘要

- 收紧 score_hack 的非攻击过滤并整体下调强度，修复其抢占主类别和高估问题
- 大幅增强 score_volatility 与 score_regulatory 的召回能力，填补波动与监管类严重漏召
- 优化主类别竞争规则，避免 hack 随意压制监管/波动，并针对性提升巨鲸、团队、基础设施等弱类别召回
- 微调 score_macro / score_outage / score_whale 等模块，兼顾降低误报与补齐缺失信号

## 变更明细

### 1. score_hack
- 原因: 在误报中触发过多且分数严重高估，抢占监管/波动主类别
- 动作: 新增多组强烈否定词（AI交易/产品/策略、量化等）；降低各个分支的基础分数（hack分数降至原来的0.7~0.8倍）；对安全语境上限进一步压缩；在金额巨大时采用更缓的增长；KW_VULN_HIGH 分数从0.55降至0.4
- 风险: medium

### 2. score_volatility
- 原因: gold 异常行情波动大量漏召，type_mismatch 最严重
- 动作: 大规模扩充关键词库 KW_VOL_NEW；降低 NEG_VOL_DAILY 的完全抑制，改用扣分因子；放开极端情况下 NEG_VOL_FORECAST 的压制；无百分比基础分提升至0.22
- 风险: low

### 3. score_regulatory
- 原因: 监管法律类新闻既存在大量漏召回又有部分误报
- 动作: 扩充参与触发的高频负面词（如诉讼、起诉、和解、执法行动等），同时新增纯正面/中性许可类语句的力度抑制；提高加密域下中等信号的基线
- 风险: medium

### 4. score_whale
- 原因: 巨鲸行为大量未触发，已触发部分分数偏高
- 动作: 增加代币解锁等多组触发入口，降低行为触发基础分（0.45→0.35），降低金额门槛加分因子
- 风险: low

### 5. score_macro
- 原因: 无风险宏观讨论被误标为宏观风险
- 动作: 在缺少加密关联且非强冲击时得分上限压缩为0.1；增加评论/观点严格排除规则
- 风险: low

### 6. score_outage
- 原因: 常规运维公告触发过高
- 动作: 对含计划维护、公告等明显中性语境的文本得分大幅压低
- 风险: low

### 7. score_team
- 原因: 项目治理/团队异常漏召
- 动作: 补充创始人/核心成员被捕、高层动荡等弱信号触发词，保持基础分数适中
- 风险: low

### 8. score_infra
- 原因: 基础设施异常漏召
- 动作: 添加网络分区、验证者退出等关键词，适度下调基础分
- 风险: low

### 9. primary_type selection
- 原因: score_hack 总是抢占监管/波动的主类别
- 动作: 在 score_all_risks 中加入竞争规则：当 score_hack 得分最高但存在 score_regulatory >= 0.18 且文本包含强执法词时，优先选监管；同时允许相同命中的其他 classes 在相近分数下被提升为 primary
- 风险: medium

## 注意事项

- 必须观察 false_negative 中链上攻击是否增多，若增多可适当回调 score_hack 下限
- score_volatility 扩展后需监控无风险新闻被误标的比例，必要时微调 NEG_VOL_DAILY 扣分因子
- 主类别仲裁规则基于新 pattern，若出现跨类别争夺可微调阈值
- 所有分数缩放修改保持函数 clip01 和 smooth_strength，无结构性变动

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\versions\v17\scripts\risk_labeler_v17.py
- analysis_json: D:\risk_optimizer_agent\versions\v17\reports\analysis\risk_labeler_v17_analysis_llm.json
