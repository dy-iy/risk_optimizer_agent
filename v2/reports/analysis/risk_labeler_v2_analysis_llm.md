# 风险标注错误模式分析报告（LLM版）

## 样本规模

- False Positive: 219
- False Negative: 1
- Type Mismatch: 201
- Score Diff Top: 200

## LLM 总结

- score_liquidation、score_volatility、score_whale、score_hack 四个 scorer 存在严重的过宽触发问题，导致大量无明显风险的新闻被误报为高风险。
- score_hack 对非链上攻击（如医疗数据泄露、AI安全测试）的泛化误报最为突出，需要增加链上/加密领域限定词。
- score_liquidation 和 score_whale 对交易量、市值、融资等正常增长数据的错误触发，需要增加金额门槛和否定词。
- score_volatility 对价格波动、网络活动增长等中性描述过度敏感，需要收紧关键词并降低默认强度。
- score_regulatory 对监管相关新闻的误报和漏报并存，需要区分强弱触发和增加否定词。
- score_fraud 存在漏报，对团队控制代币供应量等明显 rug pull 信号未能触发。

## 主要错误模式

### 1. score_hack 对非链上攻击的泛化误报 [false_positive]

- 影响 scorer: score_hack
- 根因: score_hack 规则仅依赖关键词（如'黑客'、'攻击'、'漏洞'），未限定链上/加密领域，导致非加密领域的攻击新闻也被触发。
- 优先级: high
- 证据:
  - 误报样本中 score_hack 触发 46 次，mean_value 0.1909，max_value 0.98
  - 样本案例：医疗数据泄露（news_id 185）、AI安全测试（news_id 864）、黑客松比赛（news_id 893）均被 score_hack 误判为高风险
  - score_diff 样本中 score_hack 触发 56 次，mean_value 0.2558，max_value 0.98
- patch 建议:
  - 增加否定词：排除非加密领域（如'医疗机构'、'AI'、'软件'、'企业网络'）
  - 增加领域限定：要求同时出现'链上'、'智能合约'、'DeFi'、'代币'等加密关键词
  - 降低默认强度：对仅含通用攻击关键词的新闻，强度上限设为 0.3

### 2. score_liquidation 对交易量、市值等正常数据的误报 [false_positive]

- 影响 scorer: score_liquidation
- 根因: score_liquidation 规则将'交易量'、'涨幅'、'合约'等关键词与清算风险关联，未区分正常增长与清算事件。
- 优先级: high
- 证据:
  - 误报样本中 score_liquidation 触发 66 次，mean_value 0.2012，max_value 0.85
  - 样本案例：Cashtags 交易量达10亿美元（news_id 262）、闪迪纳入指数（news_id 820）均被 score_liquidation 误判为高风险
  - score_diff 样本中 score_liquidation 触发 50 次，mean_value 0.1766，max_value 0.85
- patch 建议:
  - 增加否定词：排除'交易量'、'涨幅'、'纳入指数'等中性描述
  - 增加金额门槛：仅当涉及具体清算金额或爆仓事件时触发
  - 收紧关键词：仅保留'爆仓'、'清算'、'强制平仓'等明确信号

### 3. score_whale 对融资、市值等正常数据的误报 [false_positive]

- 影响 scorer: score_whale
- 根因: score_whale 规则将'大额'、'市值'、'交易量'等关键词与巨鲸行为关联，未区分机构正常操作与异常大额转账。
- 优先级: high
- 证据:
  - 误报样本中 score_whale 触发 56 次，mean_value 0.2042，max_value 0.75
  - 样本案例：RWA链上总市值超3000亿美元（news_id 330）、链上银行交易量创新高（news_id 625）、Votre融资375万美元（news_id 512）均被 score_whale 误判为高风险
  - score_diff 样本中 score_whale 触发 49 次，mean_value 0.2007，max_value 0.75
- patch 建议:
  - 增加否定词：排除'融资'、'市值'、'交易量'、'牌照'等中性描述
  - 增加金额门槛：仅当涉及具体地址转账或钱包变动时触发
  - 收紧关键词：仅保留'巨鲸'、'大额转账'、'钱包转移'等明确信号

### 4. score_volatility 对价格波动、网络活动等中性描述的误报 [false_positive]

- 影响 scorer: score_volatility
- 根因: score_volatility 规则将'增长'、'涨幅'、'波动'等关键词与异常行情关联，未区分正常增长与异常波动。
- 优先级: high
- 证据:
  - 误报样本中 score_volatility 触发 61 次，mean_value 0.172，max_value 0.7461
  - 样本案例：Cardano网络活动增长（news_id 794）、Bukele支持率（news_id 102）、Arthur Hayes投资（news_id 220）均被 score_volatility 误判为高风险
  - score_diff 样本中 score_volatility 触发 46 次，mean_value 0.1441，max_value 0.7461
- patch 建议:
  - 增加否定词：排除'活跃地址'、'交易量增长'、'支持率'等中性描述
  - 增加金额门槛：仅当涉及具体价格百分比或波动幅度时触发
  - 降低默认强度：对仅含通用波动关键词的新闻，强度上限设为 0.3

### 5. 异常行情波动风险被误判为无明显风险 [type_mismatch]

- 影响 scorer: score_volatility
- 根因: score_volatility 规则对价格波动、市场预测等新闻的触发阈值过高或关键词覆盖不足，导致漏召回。
- 优先级: high
- 证据:
  - type_mismatch 中 gold='异常行情波动风险'、rule='无明显风险' 共 53 次
  - 样本案例：Bitcoin价格波动（news_id 2）、Bitcoin最大痛苦点（news_id 8）、Bitcoin可能抛售（news_id 620）均被规则判为无明显风险
- patch 建议:
  - 增加关键词：'价格波动'、'最大痛苦点'、'抛售'、'回调'、'挤压'
  - 降低触发阈值：对含'Bitcoin'、'价格'、'波动'等组合的新闻，降低触发分数门槛

### 6. 监管与法律风险被误判为无明显风险 [type_mismatch]

- 影响 scorer: score_regulatory
- 根因: score_regulatory 规则对监管法案、银行协会呼吁等新闻的触发条件过严，关键词覆盖不足。
- 优先级: high
- 证据:
  - type_mismatch 中 gold='监管与法律风险'、rule='无明显风险' 共 20 次
  - 样本案例：北卡罗来纳银行协会呼吁禁止稳定币收益（news_id 129）、参议员计划发布Clarity Act草案（news_id 818）均被规则判为无明显风险
- patch 建议:
  - 增加关键词：'法案'、'草案'、'呼吁'、'禁止'、'反对'、'银行协会'
  - 增加弱触发分支：对含'稳定币'、'监管'、'法案'等组合的新闻，设置较低触发分数

### 7. 无明显风险被误判为大额转账/巨鲸行为风险 [type_mismatch]

- 影响 scorer: score_whale
- 根因: score_whale 规则对'市值'、'交易量'等关键词的触发条件过宽，未区分正常市场数据与异常转账。
- 优先级: high
- 证据:
  - type_mismatch 中 gold='无明显风险'、rule='大额转账/巨鲸行为风险' 共 40 次
  - 样本案例：RWA链上总市值超3000亿美元（news_id 330）、链上银行交易量创新高（news_id 625）均被规则判为巨鲸风险
- patch 建议:
  - 增加否定词：排除'市值'、'交易量'、'融资'、'牌照'等中性描述
  - 增加金额门槛：仅当涉及具体地址转账或钱包变动时触发

### 8. 诈骗/跑路/Rug Pull风险漏报 [false_negative]

- 影响 scorer: score_fraud, score_hack
- 根因: score_fraud 和 score_hack 规则未覆盖'团队控制'、'供应量集中'等 rug pull 信号。
- 优先级: medium
- 证据:
  - 漏报样本仅1条，gold='诈骗/跑路/Rug Pull风险'，rule='无明显风险'
  - 样本案例：RAVE币团队控制超98%供应量（news_id 898），所有 scorer 均未触发
- patch 建议:
  - 增加关键词：'团队控制'、'供应量集中'、'内部人士'、'钱包持有'
  - 增加弱触发分支：对含'代币'、'供应量'、'团队'等组合的新闻，设置较低触发分数

## Scorer 级诊断

- **score_hack**: 过宽触发
  - 原因: 误报样本中触发46次，score_diff样本中触发56次，且样本案例显示非链上攻击（医疗数据泄露、AI安全测试）被误判为高风险。
  - 建议: 增加链上/加密领域限定词，排除非加密领域的攻击新闻；降低通用攻击关键词的默认强度。
- **score_liquidation**: 过宽触发
  - 原因: 误报样本中触发66次，score_diff样本中触发50次，且样本案例显示交易量、涨幅等正常数据被误判为清算风险。
  - 建议: 增加否定词排除中性描述，增加金额门槛，收紧关键词仅保留明确清算信号。
- **score_whale**: 过宽触发
  - 原因: 误报样本中触发56次，score_diff样本中触发49次，且样本案例显示融资、市值等正常数据被误判为巨鲸风险。
  - 建议: 增加否定词排除中性描述，增加金额门槛，收紧关键词仅保留明确转账信号。
- **score_volatility**: 过宽触发
  - 原因: 误报样本中触发61次，score_diff样本中触发46次，且样本案例显示网络活动增长、支持率等中性描述被误判为异常行情。
  - 建议: 增加否定词排除中性描述，增加金额门槛，降低默认强度。
- **score_regulatory**: 过宽触发
  - 原因: 误报样本中触发29次，type_mismatch中gold='无明显风险'、rule='监管与法律风险'共17次，且样本案例显示AI身份验证、欧盟提案等被误判为监管风险。
  - 建议: 增加否定词排除非加密领域（如AI、谷歌），增加领域限定词。
- **score_fraud**: 漏召回
  - 原因: 漏报样本中gold='诈骗/跑路/Rug Pull风险'，但score_fraud未触发，且样本案例显示团队控制代币供应量等明显rug pull信号未被捕获。
  - 建议: 增加关键词覆盖团队控制、供应量集中等信号，增加弱触发分支。
- **score_team**: 证据不足
  - 原因: 所有统计中score_team触发次数均为0，但漏报样本中gold包含'项目治理/团队异常风险'，且type_mismatch中gold='项目治理/团队异常风险'、rule='无明显风险'共6次。
  - 建议: 证据不足，需进一步分析样本以确定是否需增加规则。
- **score_infra**: 证据不足
  - 原因: 所有统计中score_infra触发次数均为0，但type_mismatch中gold='基础设施/协议层异常风险'、rule='无明显风险'共3次。
  - 建议: 证据不足，需进一步分析样本以确定是否需增加规则。

## patch 顺序

- Step 1: score_hack
  - 动作: 增加链上/加密领域限定词，排除非加密领域的攻击新闻；降低通用攻击关键词的默认强度至0.3。
  - 收益: 减少医疗数据泄露、AI安全测试等非链上攻击的误报，预计减少误报约30%。
- Step 2: score_liquidation
  - 动作: 增加否定词排除'交易量'、'涨幅'、'纳入指数'等中性描述；增加金额门槛，仅当涉及具体清算金额或爆仓事件时触发。
  - 收益: 减少Cashtags交易量、闪迪纳入指数等正常数据的误报，预计减少误报约25%。
- Step 3: score_whale
  - 动作: 增加否定词排除'融资'、'市值'、'交易量'、'牌照'等中性描述；增加金额门槛，仅当涉及具体地址转账或钱包变动时触发。
  - 收益: 减少RWA市值、链上银行交易量等正常数据的误报，预计减少误报约20%。
- Step 4: score_volatility
  - 动作: 增加否定词排除'活跃地址'、'交易量增长'、'支持率'等中性描述；增加金额门槛；降低默认强度至0.3。
  - 收益: 减少Cardano网络活动、Bukele支持率等中性描述的误报，预计减少误报约20%。
- Step 5: score_regulatory
  - 动作: 增加否定词排除非加密领域（如AI、谷歌）；增加领域限定词。
  - 收益: 减少AI身份验证、欧盟提案等非加密监管新闻的误报，预计减少误报约10%。
- Step 6: score_fraud
  - 动作: 增加关键词覆盖'团队控制'、'供应量集中'、'内部人士'、'钱包持有'等rug pull信号；增加弱触发分支。
  - 收益: 捕获RAVE币等团队控制代币供应量的漏报案例，预计减少漏报。
- Step 7: score_volatility 和 score_regulatory 的漏召回
  - 动作: 增加关键词覆盖'价格波动'、'最大痛苦点'、'抛售'、'法案'、'草案'、'呼吁'等；降低触发阈值。
  - 收益: 减少异常行情波动风险和监管与法律风险的漏召回，预计减少type_mismatch约30%。

**整体置信度**: high
