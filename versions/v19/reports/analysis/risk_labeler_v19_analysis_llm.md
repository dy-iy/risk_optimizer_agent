# 风险标注错误模式分析报告（LLM版）

## 样本规模

- False Positive: 31
- False Negative: 10
- Type Mismatch: 258
- Score Diff Top: 200

## 版本变化摘要

- 改善: false_positive_rows 42.0 -> 31.0 (delta -11.0)
- 改善: type_mismatch_rows 259.0 -> 258.0 (delta -1.0)
- 改善: score_diff_mean 13.62 -> 13.521 (delta -0.099)
- 改善: score_diff_rmse 18.5018 -> 18.3266 (delta -0.1753)
- 恶化: false_negative_rows 7.0 -> 10.0 (delta 3.0)
- 提醒: false_positive 下降但 false_negative_rows 上升，可能存在单目标优化 false_positive 导致其它指标恶化。

## LLM 总结

- v19 相比 v18 误报下降 26%，但漏报从 7 升至 10，类型错配几乎未变（258），整体低估严重（平均 rule−gold = −20.7），根源是收紧部分触发导致多个弱 scorer 召回不足。
- 核心矛盾在于 score_hack/whale/outage 触发过宽造成误报，而 score_volatility/regulatory/fraud/team 触发过弱或负面压制过强造成大量漏报和类型错配，优化必须同时提升弱 scorer 召回并精确限制强 scorer 的误报条件。

## 指标权衡诊断

- 主要改善指标: false_positive
- 恶化指标: false_negative, type_mismatch
- 可能原因: 系统通过收紧部分 scorer 触发条件降低了误报，但削弱了正确触发导致漏报和类别缺配，尤其 score_volatility/regulatory 等几乎不出分。
- 优化提醒: 不要继续单独压 false_positive，必须同时修复召回和主类别覆盖，否则 FN 和 type_mismatch 将继续恶化。

## 主要错误模式

### 1. score_hack 对非攻击新闻过度触发（研究、报告、历史事件） [false_positive]

- 影响 scorer: score_hack
- 根因: 正面词表 KW_HACK_HIGH_SEV（如'窃取'）和 CRYPTO_DOMAIN 匹配广泛，缺少对研究、报告、历史回顾等非直接攻击文本的排除，且 NEG_NON_CRYPTO_HACK guard 未完全停止 CRYPTO_DOMAIN 存在时的触发。
- 分数方向: overestimate
- risky_patch: True
- 优先级: high
- 证据:
  - news328 欧洲股市新闻含“伊朗战争”触发 score_hack=0.4，gold 无风险
  - news381 朝鲜 IT 工人参与项目研究被误判为漏洞攻击，score_hack=0.83，gold 监管风险
  - news948 AI 研究未直接攻击但 score_hack=0.75，gold 低风险
  - news167、186、489 等安全声明或讨论被误判为攻击，score_hack=0.3
- patch 建议:
  - 在 score_hack 的负面词表中增加“研究”“报告”“识别”“计划”“事后分析”等非攻击语境词，降低相应得分
  - 调整 NEG_HACK_NON_ATTACK 和 NEG_NON_CRYPTO_HACK 的 guard 逻辑，当文本包含强研究/报告信号时返回低分
  - 降低仅由新闻概括间接引用的窃取等词触发的强度映射，避免直接给高分
- 可能副作用:
  - 可能增加 false_negative，对真实攻击新闻中提及“研究”等词时会降低得分

### 2. score_whale 对普通交易/回购新闻过度触发 [false_positive]

- 影响 scorer: score_whale
- 根因: KW_WHALE_BEHAVIOUR 中'出售''清仓'等词过宽，未区分具体链上大额异常行为与常规公司/个人交易；NEG_WHALE_FALSE 覆盖不全。
- 分数方向: overestimate
- risky_patch: True
- 优先级: medium
- 证据:
  - news842 公司出售 STRC 买入 BTC 被标为大额转账风险，gold 无风险
  - news84 多军头子开仓被标为 whale risk，gold 低风险
  - news894 公司出售比特币为股票回购，whale 得分 0.47，gold 低风险
- patch 建议:
  - 增强 whale 触发所需的额外链上地址或大额数量上下文，否则降低分数
  - 在 NEG_WHALE_FALSE 中加入“股票回购”“股票”“购入”等更多公司金融词汇
  - 对仅出现“出售”而无具体地址或大额金额的文本给低分
- 可能副作用:
  - 可能削弱真实巨鲸异动检测，漏掉部分无地址但可靠来源的大额提示

### 3. score_outage 对非交易所维护文本过度触发 [false_positive]

- 影响 scorer: score_outage
- 根因: KW_OUTAGE 词表包含“维护”“无法交易”等词，缺少对非交易所实体或正常资产管理操作的过滤。
- 分数方向: overestimate
- risky_patch: True
- 优先级: medium
- 证据:
  - news7 论坛讨论中“无法交易”触发 outage=0.62，gold 无明显风险
  - news617 开源项目维护回应触发 outage=0.5，gold 无风险
  - news651 币安下架交易对正常维护公告触发 outage=0.5，gold 低风险
- patch 建议:
  - 限制 outage 触发主体为知名交易所或具体交易环境，增加上下文要求
  - 在负面词表中增加“开源”“论坛”“讨论”“币安公告”等信号以降低非紧急运维的得分
- 可能副作用:
  - 可能漏掉新兴交易所或协议的停机风险

### 4. score_hack 对真实漏洞攻击得分不足（被负面词表压制） [false_negative]

- 影响 scorer: score_hack
- 根因: NEG_HACK_BUSINESS_EXCLUDE（如'借贷协议'）、NEG_HACK_MITIGATION（'冻结'）等负面词表过度降权，将其实漏洞攻击得分压到阈值以下。
- 分数方向: underestimate
- risky_patch: True
- 优先级: high
- 证据:
  - news238 漏洞攻击损失 1840 万美元，score_hack=0.2685，低于 type_threshold，导致整体风险分 27
  - news163 KelpDAO 漏洞攻击，score_hack=0.3，但主类别被 vol 抢夺，风险分 35
  - news797 Saturn 严重漏洞，score_hack=0.35，风险分 35
- patch 建议:
  - 修改 NEG_HACK_BUSINESS_EXCLUDE 和 NEG_HACK_MITIGATION 的 guard，当出现强攻击词（如'漏洞攻击''利用漏洞''资金被盗'）时绕过或减轻降权
  - 提高 score_hack 在漏洞攻击场景下的基础映射，从当前 0.3-0.35 能产生更高风险分
- 可能副作用:
  - 可能增加 false_positive，将非攻击的商业漏洞描述误判为攻击

### 5. score_fraud 对团队控制/地毯风险信号得分不足 [false_negative]

- 影响 scorer: score_fraud
- 根因: KW_FRAUD_EXTRA 虽包含“团队控制”“内部人士”等，但映射分数太低，无法达到 PRIMARY_MIN 0.15。
- 分数方向: underestimate
- risky_patch: True
- 优先级: high
- 证据:
  - news898 团队控制 98% 供应量，gold fraud 85，score_fraud 仅 0.12，rule 无风险
  - FN 统计中 fraud 漏报 2 例
- patch 建议:
  - 提升 KW_FRAUD_EXTRA 的得分权重，使其在出现“团队控制”“钱包持有”等时至少达到 0.2
  - 增加对代币供应集中度描述的正则触发
- 可能副作用:
  - 可能增加误报，将正常的团队持有误判为 rug pull

### 6. score_volatility 严重漏触发导致大量波动风险被归为无风险 [type_mismatch]

- 影响 scorer: score_volatility
- 根因: CRYPTO_DOMAIN 条件过于严格，许多波动新闻未包含特定词；NEG_VOL_FORECAST、NEG_VOL_NEUTRAL、NEG_POSITIVE_MOVE 等负面词表过度广泛，导致即使有 KW_SHOCK 也常被 guard 返回 0。
- 分数方向: underestimate
- risky_patch: True
- 优先级: high
- 证据:
  - type_mismatch gold 异常行情波动→rule 无风险 63 例，是最大错配对
  - news8 Bitcoin 价格最大痛苦点等，score_volatility=0，CRYPTO_DOMAIN 缺失
  - news332 Meme 币市值波动，score_volatility=0，NEG_VOL_DAILY 等因素
  - news340 信任危机，NEG_POSITIVE_MOVE 等因素使 vol 为零
- patch 建议:
  - 放宽 CRYPTO_DOMAIN 检查，增加 Bitcoin/ETH 等主要代币名称作为替代入口
  - 移除 NEG_VOL_FORECAST/NEUTRAL 对包含 KW_SHOCK 或 KW_VOL_MARKET_STRONG 的过度惩罚，允许明确波动词通过
  - 降低或移除 NEG_POSITIVE_MOVE 对强烈波动负面信号（如暴跌）的误杀
- 可能副作用:
  - 可能增加 false_positive，将普通市场波动标为风险

### 7. score_regulatory 被 NEG_REG_ONLY_TALK 广泛压制导致监管风险漏判 [type_mismatch]

- 影响 scorer: score_regulatory
- 根因: NEG_REG_ONLY_TALK 和 NEG_REGULATORY_NEUTRAL 列表包含“草案”“讨论”等词，且 guard 条件无需 REG_STRONG_NEGATIVE 即返回 0，导致大量有实质性监管影响的新闻被归零。
- 分数方向: underestimate
- risky_patch: True
- 优先级: high
- 证据:
  - type_mismatch gold 监管→rule 无风险 41 例
  - news83 韩国数字资产基本法延迟，score_regulatory=0，NEG_REG_ONLY_TALK 匹配‘草案’‘讨论’
  - news174 英国 FCA 监管提案，score_regulatory=0，类似
  - news397 CFTC 主席批评，score_regulatory=0
- patch 建议:
  - 重新定义 NEG_REG_ONLY_TALK，只对纯讨论且无任何监管动作（如提案发布、调查进展）的文本生效
  - 提高 REG_WEAK_SIGNALS 的得分贡献，使草案/提案即使无强负面也能获得 >0.15 的 score
- 可能副作用:
  - 可能增加 false_positive，将对市场影响较小的监管讨论标为风险

### 8. score_regulatory 强度映射偏高导致少量样本严重高估 [score_diff]

- 影响 scorer: score_regulatory
- 根因: REG_STRONG_NEGATIVE 匹配后直接映射到 0.75，但 gold 认为风险等级仅为 35，说明 0.75 对应风险分过高。
- 分数方向: overestimate
- risky_patch: True
- 优先级: medium
- 证据:
  - news423 巴拉圭洗钱案 score_regulatory=0.75，gold 35，分数高估 40
  - news120 中国草案 score_regulatory=0.75，gold 35，高估 40
  - overestimate 统计中 regulatory 有 2 例得分均值 0.0626 但 max 0.75
- patch 建议:
  - 校准 score_regulatory 的分数到风险分映射，将 0.75 映射到 gold 相近的 35-40 区间
  - 考虑将监管强负面事件的最高分控制在 0.5-0.6
- 可能副作用:
  - 可能降低真实严重监管事件的风险分数，导致漏报

## Scorer 级诊断

- **score_hack**: 过宽触发与负面词表过严并存
  - 原因: FP 统计 13 例误报，主要因‘窃取’等词在非攻击新闻中触发高分；FN 统计 8 例漏报，得分大多在 0.27-0.35，被 NEG_HACK_BUSINESS_EXCLUDE 等压制到阈值以下。
  - 建议: 增加研究/报告类负面排除，同时为真实漏洞攻击场景解锁负面词表限制；提高漏洞攻击信号的基础映射分数。
  - patch 风险: 降低误报可能加剧漏报，提升漏洞得分可能增加误报，需精细平衡。
- **score_whale**: 过宽触发
  - 原因: FP 统计 5 例，‘出售’‘清仓’等普通交易被触发，gold 多为无风险或低风险，得分均值 0.0776 但 max 0.47 导致误报。
  - 建议: 增加地址/金额等链上信号要求，扩展 NEG_WHALE_FALSE 以排除公司金融行为。
  - patch 风险: 可能漏掉无地址但可靠的大额鲸鱼预警。
- **score_outage**: 过宽触发
  - 原因: FP 统计 4 例，‘维护’‘无法交易’在非交易所语境触发，且 score_macro 有时竞争，但 outage 分高误导。
  - 建议: 限定 outage 触发需与交易所/协议停机直接相关，增加主体上下文检查。
  - patch 风险: 可能漏掉重要的小交易所或协议停摆风险。
- **score_regulatory**: 强度映射偏高与负面词表过严导致漏触发
  - 原因: 高估案例中 0.75 映射到 rule 75 分但 gold 仅 35；大量监管新闻因 NEG_REG_ONLY_TALK 被归零，type_mismatch 中 41 例监管 gold→rule 无风险。
  - 建议: 降低 REG_STRONG_NEGATIVE 的分数映射，放宽 NEG_REG_ONLY_TALK 限制，提升弱信号权重。
  - patch 风险: 可能增加监管类误报，或将严重监管事件分数压低。
- **score_volatility**: 严重漏召回
  - 原因: 63 例 gold 波动风险规则无风险，几乎无触发，CRYPTO_DOMAIN 缺失和多种 NEG_VOL 列表导致 0 分。
  - 建议: 放宽 CRYPTO_DOMAIN，移除对 KW_SHOCK 等强烈词的前置否定惩罚，减少过严的 FORECAST/NEUTRAL 限制。
  - patch 风险: 可能将日常波动报道误标为风险。
- **score_fraud**: 漏召回
  - 原因: FN 2 例，团队控制信号得分仅 0.12 低于 PRIMARY_MIN，无法形成主类别。
  - 建议: 提升 KW_FRAUD_EXTRA 权重，确保明确欺诈信号达到 0.15 以上。
  - patch 风险: 可能增加正常集中的误报。
- **score_team**: 漏召回
  - 原因: gold 团队异常风险 12 例出现在 underestimate 中，但 score_team 完全为零，样本 news949 未能触发。
  - 建议: 补充团队异常的正面关键词（如团队调整、治理攻击、创始人异常等）和弱触发入口。
  - patch 风险: 可能增加对普通团队事务的误报。

## Scorer Trace 诊断

- **score_hack**: over_trigger
  - 代码区域: score_hack 的 NEG_NON_CRYPTO_HACK guard 及强度映射函数
  - trace 证据:
    - news328 trace: matched_positive_keyword_lists 为空但 NEG_NON_CRYPTO_HACK 匹配 AI，CRYPTO_DOMAIN 缺失，guard 预期返回 0，实际得分 0.4（可能存在其他路径）
    - news381 trace: positive 命中窃取，NEG_NON_CRYPTO_HACK 匹配 AI，CRYPTO_DOMAIN 存在绕过，得分 0.83
  - 建议: 检查并修复 guard 逻辑确保研究/报告类文本被抑制；添加语境检查，当全文为事后分析或计划描述时降低得分。
  - guardrail: 保持真实攻击新闻（如含漏洞攻击、资金被盗）不受影响。
- **score_hack**: missing_positive_trigger
  - 代码区域: NEG_HACK_BUSINESS_EXCLUDE 和 NEG_HACK_MITIGATION 的惩罚逻辑
  - trace 证据:
    - news238 trace: positive 命中 0，negative 命中借贷协议、追回、冻结，导致得分 0.2685 低于阈值
    - news797 trace: 只命中严重漏洞，但仍被冻结负面词拉低
  - 建议: 当文本同时出现强攻击词（如漏洞攻击）时，减免或忽略这些负面词的降权。
  - guardrail: 确保不把普通的业务扫描报告误判为攻击。
- **score_volatility**: missing_positive_trigger
  - 代码区域: score_volatility 的 CRYPTO_DOMAIN early return guard
  - trace 证据:
    - news8 trace: CRYPTO_DOMAIN 缺失导致直接 return 0，虽然 KW_VOL_MISS 命中多个波动词
    - news18 trace: 暴跌命中，但 CRYPTO_DOMAIN 缺失 return 0
  - 建议: 放宽 CRYPTO_DOMAIN 条件，增加 BTC/ETH 等代币名称为备选入口，避免仅因缺特定词而完全零分。
  - guardrail: 保持非加密新闻不会被误判为市场波动风险。
- **score_volatility**: matched_negative_guard
  - 代码区域: score_volatility 的 NEG_VOL_FORECAST、NEG_VOL_NEUTRAL、NEG_POSITIVE_MOVE 防御逻辑
  - trace 证据:
    - news8 trace: NEG_VOL_FORECAST 匹配‘可能’导致 return 0，尽管有波动信号
    - news340 trace: NEG_POSITIVE_MOVE 匹配‘回升’且 NEG_VOL_NEUTRAL 匹配‘持有’，multi-guard 返回 0
  - 建议: 修改这些 guard 使其在出现 KW_SHOCK 或 KW_VOL_MARKET_STRONG 时不生效，避免误杀明确波动信号。
  - guardrail: 防止将纯粹的分析评论提升为风险。
- **score_regulatory**: matched_negative_guard
  - 代码区域: score_regulatory 的 NEG_REG_ONLY_TALK guard
  - trace 证据:
    - news83 trace: NEG_REG_ONLY_TALK 匹配‘草案’‘讨论’，且无 REG_STRONG_NEGATIVE，导致 return 0
    - news174 trace: 类似，草案和提案被归零
  - 建议: 重新定义 NEG_REG_ONLY_TALK，仅在纯粹言论无任何正式程序（如咨询期、法案提交）时生效，并让 REG_WEAK_SIGNALS 有机会产生至少 0.15 的分数。
  - guardrail: 避免将无关的监管闲聊升级为风险。

## 主类别诊断

- **gold=异常行情波动风险, rule=无明显风险**: score_volatility 几乎零触发（CRYPTO_DOMAIN 缺失、NEG 列表过多），导致主类别缺失
  - 修复: 修复 score_volatility 触发条件，放宽入口并减少过度防御
  - guardrail: 可能增加波动类误报，需配套微调阈值
- **gold=监管与法律风险, rule=无明显风险**: score_regulatory 被 NEG_REG_ONLY_TALK 压制，大量实质性监管新闻遭零分
  - 修复: 减少 NEG_REG_ONLY_TALK 的过度匹配，提升弱监管信号得分
  - guardrail: 可能将纯讨论升级为风险，需限定在正式程序信号上
- **gold=无明显风险, rule=链上漏洞/攻击风险**: score_hack 过宽触发（窃取等词误匹配），规则错误选择了 hack 为主类别
  - 修复: 增强 score_hack 的研究、报告排除逻辑，降低非直接攻击的强度
  - guardrail: 避免对真实漏洞报道降权
- **gold=大额转账/巨鲸行为风险, rule=无明显风险**: score_whale 触发不足且门槛过高，许多 gold whale 新闻未达到 PRIMARY_MIN
  - 修复: 适当降低 whale 的 PRIMARY_MIN 或提高大额信号权重
  - guardrail: 可能增加 whale 类误报

## patch 顺序

- Step 1: score_volatility
  - 动作: 放宽 CRYPTO_DOMAIN，增加 BTC/ETH 等主要代币为入口；调整 NEG_VOL_FORECAST/NEUTRAL/POSITIVE_MOVE 仅在无 KW_SHOCK/KW_VOL_MARKET_STRONG 时生效
  - 收益: 大幅减少 type_mismatch 中的波动→无风险错配，提升波动相关 FN 的召回
  - guardrail: 监控波动类 false_positive，准备微调 NEG_VOL_DAILY 等
  - 验证: 检查 type_mismatch 对 '异常行情波动风险' 的变化，确认 score_diff 低估改善
- Step 2: score_regulatory
  - 动作: 修改 NEG_REG_ONLY_TALK 逻辑，仅匹配纯讨论无程序信号；提升 REG_WEAK_SIGNALS 分数映射使其能达到 PRIMARY_MIN
  - 收益: 减少监管→无风险错配 41 例，提升监管风险召回
  - guardrail: 监控监管类 FP 是否有显著上升
  - 验证: 检查 type_mismatch 和 false_negative 中监管类指标
- Step 3: score_hack
  - 动作: 增加研究/报告/事后分析等负面排除词；当存在强攻击词时豁免部分 NEG_HACK_BUSINESS_EXCLUDE 和 NEG_HACK_MITIGATION 的惩罚
  - 收益: 同时降低误报和漏报，改善 FP/FN 平衡
  - guardrail: 分别监控 hack 类的 FP 和 FN，避免单向恶化
  - 验证: 验证 FP 中 hack 的错误是否减少，FN 中 hack 得分是否升至大于 0.3 并引致正确风险分
- Step 4: score_fraud
  - 动作: 提升 KW_FRAUD_EXTRA 的映射权重，使团队控制/内部持有等信号能得分≥0.15
  - 收益: 捕获当前完全漏掉的 rug pull/团队风险
  - guardrail: 观察 fraud 类误报，可增加负面词限制如 '公开透明团队' 等
  - 验证: 检查 fraud FN 是否减少，新增 fraud 主类别的准确率
- Step 5: score_whale 和 score_outage
  - 动作: 限制 whale 触发需有地址或大额数量上下文；outage 增加交易所/协议主体检查，调整'维护'等通用词权重
  - 收益: 进一步降低误报而不过度影响召回
  - guardrail: 监控 whale 和 outage 的 FN，若显著增加则回滚部分限制
  - 验证: FP 中 whale/outage 数量下降，FN 未见增加
- Step 6: score_regulatory 强度映射
  - 动作: 将 REG_STRONG_NEGATIVE 导致的 0.75 映射到规则风险分 40-50 而非 75
  - 收益: 纠正 overestimate，使规则风险分接近 gold 分布
  - guardrail: 确保重大监管打击仍保留较高风险分，避免被严重低估
  - 验证: 检查 score_diff overestimate 中 regulatory 案例的 rule 分是否降至合理范围

## 暂不建议修改

- **score_hack 的正面词表（如窃取）不可继续收紧**: 因为负面词表已压制真实漏洞攻击得分，收紧正面触发会进一步恶化 false_negative。
- **score_volatility 的 PRIMARY_MIN 不要提高**: 当前 vol 已极难触发，提升阈值会使更多波动风险被归为无风险。
- **score_regulatory 的 NEG_REG_FALSE (AI 相关) 不可泛化**: AI 已导致大量监管新闻误伤，进一步扩展该词表会恶化 type_mismatch。

**整体置信度**: high
