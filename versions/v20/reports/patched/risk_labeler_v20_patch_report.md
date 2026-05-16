# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v20

## 摘要

- 修复 volatility 严重漏触发问题：放宽加密域入口并减少过度压制，让波动风险得分恢复正常。
- 修复 regulatory 被过度压制问题：限制 ONLY_TALK 范围，提升弱信号得分并降低强负面超高映射。
- 纠正 hack 误报与漏报并存的问题：增加研究/报告排除词，豁免强攻击词下的部分负面惩罚。
- 提升 fraud 弱信号得分，改善 rug/团队控制漏报。
- 限制 whale/outage 过宽触发：增加地址/金额上下文和更多排除词。

## 变更明细

### 1. score_volatility 的加密域入口和否定守卫
- 原因: CRYPTO_DOMAIN 缺失导致大量波动新闻直接归零，NEG_VOL_FORECAST/NEUTRAL/POSITIVE_MOVE 等在无强信号时仍误杀波动召回。
- 动作: 将 BTC/ETH/比特币/以太坊等主要代币加入波动加密域入口；统一豁免条件：当文本包含 KW_SHOCK/KW_VOL_MARKET_STRONG/KW_VOL_STRONG_SIGNAL/KW_VOL_MISS 中任何一个时，所有 NEG_VOL 守卫不返回 0。
- 风险: medium
- Guardrail: 仍然要求文本与加密市场相关（CRYPTO_DOMAIN 或主要代币），非加密货币新闻不会被误标；新增豁免仅影响原本应得分却因守卫归零的样本。
- 验证: 检查 type_mismatch 中'异常行情波动风险→无明显风险'的对数是否大幅下降，false_positive 波动类是否只有轻微上升。

### 2. score_regulatory 的 NEG_REG_ONLY_TALK 和弱信号得分
- 原因: NEG_REG_ONLY_TALK 将大量含草案/法案/咨询期的实质性监管新闻归零，导致 41 例监管→无风险错配；弱信号得分过低，难以达到 PRIMARY_MIN。
- 动作: 修改 ONLY_TALK 守卫：仅在无 REG_STRONG_NEGATIVE 且无立法信号（提案/草案/法案/公众咨询等）时返回 0；增加 LEGISLATIVE_SIGNALS 强弱信号分支，得分提升至 0.18-0.22；将 REG_ACTORS+强负面的基础得分从 0.75 降至 0.50。
- 风险: medium
- Guardrail: 仅对有正式程序信号的监管文本放行，纯讨论仍会被抑制；强监管事件分数降低至 50 左右，避免高估但仍保持较高风险感知。
- 验证: 监控 type_mismatch 中'监管与法律风险→无明显风险'对数是否减少，overestimate 中 regulatory 案例的 rule 分是否降至 40-50 区间。

### 3. score_hack 的研究排除及强攻击词豁免
- 原因: 研究/报告/事后分析等新闻被误判为攻击，同时真实漏洞攻击因 NEG_HACK_MITIGATION 的压低而得分不足。
- 动作: 新增 NEG_HACK_RESEARCH 词表（研究/报告/识别/计划等），无真实损失证据时直接归零；放宽 NEG_HACK_BUSINESS_EXCLUDE 的豁免词至含'黑客'；在存在攻击证据时，mitigated 降权上限从 0.30 提升至 0.40。
- 风险: medium
- Guardrail: 归零守卫由 has_real_loss 或强攻击词保护，真实漏洞攻击报道不会被误杀；mitigated 宽松限制避免压低强攻击事件。
- 验证: 检查 false_positive 中 hack 类是否减少，false_negative 中 hack 得分是否提升并超过 0.3，同时 type_mismatch 无明显增加。

### 4. score_fraud 的弱信号权重
- 原因: 团队控制/内部集中信号得分仅 0.12，低于 PRIMARY_MIN，漏报 rug pull 风险。
- 动作: 将 KW_FRAUD_EXTRA 触发得分从 0.12 提升至 0.18，使其有机会达到主类别阈值。
- 风险: low
- Guardrail: 仍保留 NEG_FRAUD 排除，防止正常团队描述误判；对单纯的'供应集中'得分仍较低，不会大幅增加误报。
- 验证: 检查 fraud 的 false_negative 是否减少，主类别中出现'诈骗 / 跑路'时 gold 是否相符。

### 5. score_whale 触发条件与 NEG_WHALE_FALSE 增强
- 原因: 普通交易（出售、清仓）无链上地址/金额证据被误判为大额风险，需增加触发门槛。
- 动作: 增加地址/钱包/哈希上下文要求：若无链上证据且金额为 0，则返回 0；扩展 NEG_WHALE_FALSE 增加'股票回购''股份回购''公司购回'等金融行为词。
- 风险: medium
- Guardrail: 有明确大额金额或链上特征的鲸鱼异动仍会触发；新增排除词仅针对公司金融行为。
- 验证: 观察 false_positive 中 whale 类是否下降，同时确保 gold 鲸鱼风险新闻仍能被触发。

### 6. score_outage 的非交易所语境抑制
- 原因: '无法交易'等词在论坛、开源讨论等环境中错误触发，误报率较高。
- 动作: 新增 NEG_OUTAGE_NON_EXCHANGE 词表（论坛/讨论/开源等），匹配时大幅降分；将'公告'加入计划维护词表，避免正常公告被误判。
- 风险: low
- Guardrail: 仅影响非紧急、非交易所运行问题场景，真实交易所停机公告仍得分。
- 验证: 检查 false_positive 中 outage 类是否减少，false_negative 无新增。

## 注意事项

- 重点关注 type_mismatch 中'异常行情波动风险→无明显风险'的对数是否从 63 显著下降至个位数。
- 检查 false_negative 总数是否从 10 开始下降，同时 false_positive 不增加超过 5 例。
- 验证 regulatory 的过度得分案例 rule 分是否从 75 降至 50 附近，减少低估和高估。
- 检查 hack 类的 FP 和 FN 是否同步改善，type_mismatch 中'hack→无风险'和'无风险→hack'的对数变化。
- 整体风险分分布均值是否更接近 gold，score_diff 的 RMSE 是否持续下降。

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\versions\v19\scripts\risk_labeler_v19.py
- analysis_json: D:\risk_optimizer_agent\versions\v19\reports\analysis\risk_labeler_v19_analysis_llm.json
