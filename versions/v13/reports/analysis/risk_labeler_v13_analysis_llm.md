# 风险标注错误模式分析报告（LLM版）

## 样本规模

- False Positive: 109
- False Negative: 3
- Type Mismatch: 209
- Score Diff Top: 200

## LLM 总结

- score_hack 误报严重：对正常机构增持、理财新闻给高攻击分，大量编为漏洞风险，同时漏报量子、漏洞、黑客等真正攻击新闻，触发阈值与关键词均需调整
- score_volatility、score_regulatory、score_whale 等多 scorer 同时存在过宽触发与召回不足，导致大量类型错配为‘无明显风险’或误标他类，强度映射也普遍偏高

## 主要错误模式

### 1. score_hack对正常增持/理财/稳定币新闻误报高危 [false_positive]

- 影响 scorer: score_hack
- 根因: score_hack 规则过度匹配‘Strategy’‘增持’‘收益生成策略’‘Galaxy 合作’等短语，将其误当作漏洞攻击词汇，缺少对正常企业行为的否定判断
- 优先级: high
- 证据:
  - 案例 news_id 981、283、315：内容为 Strategy 增持 BTC、Matador 增持 BTC、稳定币流入寻求利息，gold 均‘无明显风险’，rule 评为‘链上漏洞/攻击风险’，score_hack 高达 0.85-0.95
  - 统计：false_positive 中 score_hack 触发 39 次，均值 0.2647，最高 0.95，对应主类别‘链上漏洞/攻击风险’38 例
- patch 建议:
  - 收紧 score_hack 触发关键词，排除‘增持’‘购买’‘财库’‘ATM 股票计划’‘收益生成策略’等非攻击上下文
  - 增加否定词/规则：若新闻涉及上市公司/机构公开披露的 BTC 购买、理财合作，则抑制 score_hack 分数
  - 设置上下文门槛，要求出现‘漏洞’‘攻击’‘黑客’‘盗取’‘冻结’等明确攻击词才可给高分

### 2. score_volatility对普通行情涨跌/技术指标/分板块上涨误报高风险 [false_positive]

- 影响 scorer: score_volatility
- 根因: 规则将‘上涨’‘金叉’‘暴涨’等价格变动信号直接等同于异常风险，未区分正常市场波动与系统性/极端风险
- 优先级: high
- 证据:
  - 案例 news_id 368、686、515：内容为 AI 板块普涨、以太坊 MACD 金叉、BIO 代币暴涨 102%，gold 均‘无明显风险’，rule 评为‘异常行情波动风险’，score_volatility 0.6996-0.7499
  - 统计：false_positive 中 score_volatility 触发 24 次，均值 0.1309
- patch 建议:
  - 区分强弱触发：单一代币或子板块的行情波动，降低分数；当主要币种或整个市场出现剧烈波动并可能引发连锁反应时再给高分
  - 增加金额/幅度门槛，要求涨跌幅超过一定阈值或伴随清算、杠杆数据才标记高风险
  - 排除单纯技术指标信号（如 MACD 金叉）的单独触发

### 3. score_macro将地缘政治缓解/利好误判为宏观冲击 [false_positive]

- 影响 scorer: score_macro
- 根因: 仅靠‘地缘政治’‘霍尔木兹海峡’‘伊朗’等关键词触发，未考虑事件方向（缓和/升级）
- 优先级: high
- 证据:
  - 案例 news_id 269、749、172：内容为霍尔木兹海峡开放支持市场反弹、地缘政治碎片化提升比特币吸引力、伊朗停火延长等，gold 均‘无明显风险’，rule 评为‘宏观/政策冲击风险’，score_macro 0.5-0.5975
  - 统计：false_positive 中 score_macro 触发 15 次，主类别‘宏观/政策冲击风险’14 例
- patch 建议:
  - 增加方向判定词：若出现‘开放’‘缓解’‘停火’‘上涨推动’等利好表述，大幅降低 score_macro 或归为无明显风险
  - 收紧关键词，只有明确负面冲击（如制裁、冲突升级、封锁）才给高分

### 4. score_whale类别正确但强度映射偏高 [false_positive]

- 影响 scorer: score_whale
- 根因: score_whale 对大额转账直接给高分，未考虑是否具有明显市场风险（如转入交易所、未知钱包等上下文）
- 优先级: medium
- 证据:
  - 案例 news_id 84、724、86：大额开多、巨额 USDT 转移、鲸鱼出售 ETH，gold 25 分，rule 73-75 分，类别均为‘大额转账/巨鲸行为风险’但评分差距大
  - 统计：false_positive 中 score_whale 触发 14 次，mean 0.0789
- patch 建议:
  - 调整分数映射：常规大额转账（尤其金标也仅评低风险）降低基础分，引入目标地址类型、金额占比等因子
  - 区分‘大额转账’与‘风险转账’，增加风险信号（如已知恶意地址、大额转入中心化交易所可能抛售）才提升分数

### 5. score_hack遗漏量子威胁、协议漏洞、黑客冻结等关键攻击新闻 [false_negative]

- 影响 scorer: score_hack
- 根因: score_hack 关键词或语义缺失‘量子计算’‘漏洞’‘特权地址截留’‘冻结黑客地址’等表述，且触发阈值可能 > 0.25（当前 0.25 分仍不触发）
- 优先级: high
- 证据:
  - 案例 news_id 222、797、373：内容为量子计算威胁比特币、Saturn 漏洞、Tether 冻结黑客地址，gold 高危，rule 评‘无明显风险’，无任何 scorer 触发
  - 统计：false_negative 仅 3 例，全是‘链上漏洞/攻击风险’，score_hack 均值 0.25 但 trigger_count 0
- patch 建议:
  - 补充‘量子计算’‘漏洞’‘截留资金’‘冻结’‘黑客地址’等攻击相关词，确保对应新闻能产生非零分
  - 降低 score_hack 触发阈值至 0.2 或更低，使现有弱信号可被捕捉
  - 增加句法模式：如‘存在严重漏洞’‘攻击者在……’等强模式

### 6. 多个scorer对真实风险内容召回不足导致大量错配为‘无明显风险’ [type_mismatch]

- 影响 scorer: score_volatility, score_regulatory, score_whale, score_macro
- 根因: 对应 scorer 触发条件过严或依赖强信号，仅捕捉极端案例，导致一般但真实的风险文章无法被激活
- 优先级: high
- 证据:
  - type_mismatch 对 ‘异常行情波动风险’→‘无明显风险’ 56 例，‘监管与法律风险’→‘无明显风险’ 31 例，‘大额转账/巨鲸行为风险’→‘无明显风险’ 29 例，‘宏观/政策冲击风险’→‘无明显风险’ 13 例
  - 案例 news_id 8、340、874（行情风险）未触发 score_volatility；401、869、887（监管风险）未触发 score_regulatory；482、119、357（巨鲸风险）未触发 score_whale
- patch 建议:
  - 放宽 score_volatility 触发，增加‘恐慌情绪’‘期权偏斜’‘最大痛苦点’等弱信号
  - 放宽 score_regulatory，增加‘申请不采取行动函’‘SEC’‘银行家协会质疑稳定币’等弱监管信号
  - 放宽 score_whale，增加‘大型持有者准备出售’‘加密货币大户’‘巨鲸持仓’等描述
  - 放宽 score_macro，适度恢复‘地缘政治’等触发但区分方向

### 7. score_regulatory对无关内容误触发监管风险 [type_mismatch]

- 影响 scorer: score_regulatory
- 根因: score_regulatory 关键词（如‘监管’）过于宽泛，匹配了讨论类、展望类内容而未判断实际监管动作
- 优先级: medium
- 证据:
  - type_mismatch 中 gold‘无明显风险’→rule‘监管与法律风险’ 21 例
  - 案例 news_id 46：内容为以太坊巨鲸关注、未来展望，被误标监管风险，score_regulatory 0.3
- patch 建议:
  - 收紧 score_regulatory 触发词，要求出现具体监管事件（如‘调查’‘罚款’‘禁令’‘SEC 起诉’等）
  - 增加否定上下文：若讨论‘未来期待监管’‘缺乏监管框架’等非行动语句，降低分数或抑制

### 8. score_liquidation对真实清算新闻评分严重偏高 [score_diff]

- 影响 scorer: score_liquidation
- 根因: score_liquidation 对所有清算相关新闻都给接近满分，未区分金额、影响域（加密/非加密）
- 优先级: medium
- 证据:
  - 案例 news_id 115、276：ETH 清算预警、原油爆仓，gold 45 分，rule 85 分，score_liquidation 0.85
  - 统计：score_diff_top 中 score_liquidation 触发 30 次，mean 0.1237
- patch 建议:
  - 调整分数映射按清算金额分级，小规模清算降低分数
  - 明确加密资产清算才给重要权重，原油等传统资产清算降权或不归为加密主风险

## Scorer 级诊断

- **score_hack**: 过宽触发 + 漏召回
  - 原因: 误报中将正常公司行为、稳定币需求等判为漏洞攻击，同时漏报量子攻击、漏洞、黑客冻结等真正攻击内容
  - 建议: 收紧关键词排除增持/理财等非风险场景，降低触发阈值至0.2并补充漏洞、黑客、量子等关键词
- **score_volatility**: 过宽触发 + 漏召回
  - 原因: 将板块普涨、技术指标视为高风险，但对真正的行情异常波动新闻（如最大痛苦点、恐慌情绪）未能触发
  - 建议: 区分强弱波动：需加入市场层面、幅度等判据，同时增加‘恐慌’‘期权偏斜’等弱信号以提升召回
- **score_regulatory**: 过宽触发 + 漏召回
  - 原因: 对中性监管讨论误触发，但实际监管申请、银行质疑等新闻未触发
  - 建议: 收紧至具体监管行动（调查、罚款、诉讼），同时增加弱监管信号提升召回
- **score_macro**: 过宽触发
  - 原因: 对地缘政治缓解、停火等利好仍视为宏观冲击
  - 建议: 增加方向判断，利好/缓和降分，保留负面升级高分
- **score_whale**: 强度映射偏高 + 漏召回
  - 原因: 评分过高导致误报高分，同时对‘大型持有者准备出售’等描述未能触发
  - 建议: 降低常规大额转账基础分，增加风险上下文判断，并放宽对一般巨鲸行为的触发
- **score_liquidation**: 强度映射偏高
  - 原因: 对清算新闻统一给极高分，忽略规模差异
  - 建议: 按清算金额或影响范围分级映射分数

## patch 顺序

- Step 1: score_hack
  - 动作: 关键词去误报（排除‘增持’‘ATM 计划’‘Galaxy 合作’等），补充‘量子计算’‘漏洞’‘截留’‘冻结’等漏报词，并将触发阈值降至 0.2
  - 收益: 大幅降低 false_positive 中的漏洞误报，消除 false_negative，并减少 score_diff 偏差
- Step 2: score_volatility 与 score_regulatory
  - 动作: 增加弱信号以提升行情和监管风险的召回；同时对行情增加幅度/规模过滤，对监管收紧至具体执法动作
  - 收益: 显著减少类型错配，将大量 gold 有风险但判为‘无明显风险’的案例修正
- Step 3: score_whale 与 score_liquidation 强度映射
  - 动作: 调整分数映射规则，使评分与金标 low-medium 对齐，避免一律给高分
  - 收益: 减小 score_diff，降低因评分过高导致的虚警，提升主风险得分的可信度
- Step 4: score_macro
  - 动作: 增加方向判据，对利好立场降权或抑制，仅保留负面冲击高分
  - 收益: 减少宏观类误报，进一步降低 false_positive

**整体置信度**: high
