# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.1

## 摘要

- 收紧 score_hack 规则，增加链上/加密领域限定词，降低通用攻击关键词的默认强度
- 收紧 score_liquidation 规则，增加否定词排除交易量、涨幅等中性描述，增加金额门槛
- 收紧 score_whale 规则，增加否定词排除融资、市值等中性描述，增加金额门槛
- 收紧 score_volatility 规则，增加否定词排除活跃地址、交易量增长等中性描述，降低默认强度
- 收紧 score_regulatory 规则，增加否定词排除非加密领域，增加领域限定词
- 增强 score_fraud 规则，增加团队控制、供应量集中等 rug pull 信号关键词
- 增强 score_volatility 和 score_regulatory 的漏召回，增加关键词覆盖价格波动、法案等信号

## 变更明细

### 1. score_hack
- 原因: 误报样本中触发46次，score_diff样本中触发56次，非链上攻击（医疗数据泄露、AI安全测试）被误判为高风险
- 动作: 增加链上/加密领域限定词列表 CRYPTO_DOMAIN，要求同时出现加密关键词才触发高分数；增加否定词列表 NEG_NON_CRYPTO_HACK 排除非加密领域；降低通用攻击关键词的默认强度至0.30
- 风险: high

### 2. score_liquidation
- 原因: 误报样本中触发66次，score_diff样本中触发50次，交易量、涨幅等正常数据被误判为清算风险
- 动作: 增加否定词列表 NEG_LIQ_FALSE 排除交易量、涨幅、纳入指数等中性描述；增加金额门槛，仅当涉及具体清算金额或爆仓事件时触发高分数；收紧关键词仅保留明确清算信号
- 风险: high

### 3. score_whale
- 原因: 误报样本中触发56次，score_diff样本中触发49次，融资、市值等正常数据被误判为巨鲸风险
- 动作: 增加否定词列表 NEG_WHALE_FALSE 排除融资、市值、交易量、牌照等中性描述；增加金额门槛，仅当涉及具体地址转账或钱包变动时触发高分数；收紧关键词仅保留明确转账信号
- 风险: high

### 4. score_volatility
- 原因: 误报样本中触发61次，score_diff样本中触发46次，网络活动增长、支持率等中性描述被误判为异常行情
- 动作: 增加否定词列表 NEG_VOL_FALSE 排除活跃地址、交易量增长、支持率等中性描述；增加金额门槛；降低默认强度至0.30
- 风险: high

### 5. score_regulatory
- 原因: 误报样本中触发29次，type_mismatch中gold='无明显风险'、rule='监管与法律风险'共17次，AI身份验证、欧盟提案等被误判为监管风险
- 动作: 增加否定词列表 NEG_REG_FALSE 排除非加密领域（如AI、谷歌、医疗）；增加领域限定词列表 REG_CRYPTO_DOMAIN 要求同时出现加密关键词
- 风险: high

### 6. score_fraud
- 原因: 漏报样本中gold='诈骗/跑路/Rug Pull风险'，但score_fraud未触发，团队控制代币供应量等明显rug pull信号未被捕获
- 动作: 增加关键词列表 KW_FRAUD_EXTRA 覆盖团队控制、供应量集中、内部人士、钱包持有等rug pull信号；增加弱触发分支
- 风险: medium

### 7. score_volatility 和 score_regulatory 的漏召回
- 原因: type_mismatch中gold='异常行情波动风险'、rule='无明显风险'共53次，gold='监管与法律风险'、rule='无明显风险'共20次
- 动作: 增加关键词覆盖价格波动、最大痛苦点、抛售、法案、草案、呼吁等信号；降低触发阈值
- 风险: high

## 注意事项

- 所有修改均基于 analysis_llm.json 中证据充分的部分，未引入不必要的重构
- CSV 输入输出协议、列名、主流程保持不变
- 未引入除标准库和 pandas 之外的新第三方包
- 修改集中在关键词范围、否定词、金额门槛、阈值、强弱触发、主类别逻辑
- score_team 和 score_infra 因证据不足未做修改

## 元信息

- model: deepseek-chat
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v2\scripts\risk_labeler_v2.py
- analysis_json: D:\risk_optimizer_agent\v2\reports\analysis\risk_labeler_v2_analysis_llm.json
