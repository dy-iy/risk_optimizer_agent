# 风险标注错误模式分析报告（LLM版）

## 样本规模

- False Positive: 71
- False Negative: 2
- Type Mismatch: 236
- Score Diff Top: 200

## LLM 总结

- 规则在链上漏洞/攻击风险类别存在明显过度触发和分数高估，导致 false_positive 和错误抢占主类别
- 异常行情波动、监管法律等类别 scorer 严重漏触发，造成大量 type_mismatch 和 score_diff 低估，是当前最大问题

## 指标权衡诊断

- 主要改善指标: false_positive
- 恶化指标: type_mismatch, score_diff
- 可能原因: 系统通过收紧触发条件降低了部分误报，但导致召回不足和主类别竞争变化，异常行情、监管等类别被大量遗漏
- 优化提醒: 不要继续单独压 false_positive，必须优先增强缺失 scorer 的召回并校准分数映射

## 主要错误模式

### 1. score_hack 对非攻击新闻过度触发和高估 [false_positive]

- 影响 scorer: score_hack
- 根因: score_hack 的关键词匹配过宽（如包含AI、交易策略），且强度映射将分数放大过度
- 分数方向: overestimate
- risky_patch: True
- 优先级: high
- 证据:
  - 误报样本中 score_hack 触发 36 次，均值 0.2938，gold 类型多为“无明显风险”或“监管与法律风险”
  - 案例 news_id=585，gold=链上漏洞(20分)，rule=链上漏洞(94分)，hack=0.9362 严重高估
  - 案例 news_id=235，gold=监管法律，rule=链上漏洞，hack=0.85 压制了 regulatory=0.2
  - 案例 news_id=442，gold=爆仓/清算，rule=链上漏洞，hack=0.85
- patch 建议:
  - 收紧 score_hack 的触发关键词，过滤掉非安全漏洞类内容（如AI产品发布、AI交易）
  - 降低 score_hack 的强度映射函数，使 0.85 等高值更接近 gold 评分基准
  - 在 primary_type 选择时，若 score_hack 与其他类别分数差距不大且存在 regulatory 等信号，应避免 hack 抢占
- 可能副作用:
  - 可能增加 false_negative（漏掉真正的攻击事件）
  - 可能导致链上漏洞类别的召回下降

### 2. 异常行情波动风险未被 score_volatility 捕获而归为“无明显风险” [type_mismatch]

- 影响 scorer: score_volatility
- 根因: score_volatility 的关键词/模式缺失，完全未能识别价格波动、最大痛点、Meme 币波动等信息
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - type_mismatch 最大对：异常行情波动风险 -> 无明显风险 (56 条)
  - avg_rule_risk_score=0.64，avg_gold_risk_score=24.29，差距巨大
  - 案例 news_id=8,332,340 等 gold 为异常行情波动，但所有 scorer 为 0，rule 输出无明显风险
  - score_diff 低估集中在该类别，gold=异常行情波动 38 次低估，score_volatility 仅触发 1 次，均值 0.006
- patch 建议:
  - 大幅扩展 score_volatility 的触发规则，加入价格波动描述、爆仓关联、市场回调等文本模式
  - 提高 score_volatility 的强度映射，使捕获的波动新闻能获得足够分数
- 可能副作用:
  - 可能增加新的 false_positive（将非波动新闻误标为波动风险）
  - 可能加剧 type_mismatch 中无明显风险 -> 异常行情波动风险的错配

### 3. 监管与法律风险两个方向均存在错配，但漏召回为主 [type_mismatch]

- 影响 scorer: score_regulatory
- 根因: score_regulatory 的召回覆盖不足，许多明确监管新闻未被捕获，同时又对部分非监管内容错误触发
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - type_mismatch 对：监管与法律风险 -> 无明显风险 (33 条)，无明显风险 -> 监管与法律风险 (33 条)
  - 监管->无风险平均 rule_risk_score=2.33，gold=24.39，严重低估
  - 案例 news_id=83,174,397 等 gold=监管法律，所有 scorer 为 0，rule 输出无明显风险
  - score_diff 低估中 gold=监管 18 次，score_regulatory 在低估集触发 0 次
  - 同时明显存在无风险被误标为监管的情况 (33 条)，说明 score_regulatory 也有过宽触发
- patch 建议:
  - 增强监管关键词库（加入各国监管机构、法案、裁决等），提高召回
  - 对目前已造成误报的 pattern 增加负面过滤，如仅提到合规或稳定币正面描述时不触发
  - 在 primary_type 选择时，结合上下文降低 regulatory 的无意义高挥发性
- 可能副作用:
  - 增加召回可能带来更多 false_positive（无明显风险被错标为监管）
  - 需要平衡误报与漏报

### 4. 大额转账/巨鲸行为未被 score_whale 足够捕获 [type_mismatch]

- 影响 scorer: score_whale
- 根因: score_whale 对于大额持仓、代币解锁等类型新闻缺乏触发规则，同时对于已触发的鲸鱼出售新闻强度映射过高
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - type_mismatch 对：大额转账 -> 无明显风险 (20 条), avg_rule_risk_score=0.85, gold=22.25
  - 案例 news_id=482,119,521 等 gold=大额转账，所有 scorer 为 0
  - score_diff 低估中 gold=大额转账 11 次，score_whale 在低估集触发 0 次
  - false_positive 中 score_whale 也偏高（6次触发），部分过标
- patch 建议:
  - 扩展 score_whale 的触发规则，纳入代币解锁、大户转移、交易所资产移动等信号
  - 适当降低当前触发时的强度映射，减少高估
- 可能副作用:
  - 可能增加 false_positive，导致无明显风险被标为巨鲸风险
  - 高估方面的改善可能降低一部分 score_diff

### 5. 多项 scorer 强度映射导致系统性低估 [score_diff]

- 影响 scorer: score_volatility, score_regulatory, score_whale, score_team, score_infra, score_fraud
- 根因: 多个风险类别 scorer 的触发覆盖和强度映射严重不足，导致规则整体风险分数严重偏低
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - score_diff 整体低估 126 条，overestimate 74 条，mean_rule_minus_gold=-4.81
  - 低估集中类型：异常行情波动 38、监管法律 18、链上漏洞 16、大额转账 11 等
  - 低估案例 news_id=18、949、343 等，规则输出 0 或极低分，gold 评分中高风险
  - 低估触发分数中 score_volatility 均值 0.006，score_regulatory 0.0166，几乎不起作用
- patch 建议:
  - 统一上调 underestimation 集中的 scorer 基础触发率，如 score_volatility、score_regulatory
  - 调整这些 scorer 的强度映射，使触发后的贡献能与 gold 风险评分匹配
- 可能副作用:
  - 可能增加 overestimate 和 false_positive
  - 需要配合类别竞争逻辑防止错误抢占

### 6. score_hack 高强度映射导致系统性高估 [score_diff]

- 影响 scorer: score_hack
- 根因: score_hack 的原始分数到最终风险分数的映射过于激进，导致甚至低风险 hint 新闻也被判定为高风险
- 分数方向: overestimate
- risky_patch: True
- 优先级: high
- 证据:
  - overestimate 集中 score_hack 触发 40 次，均值 0.3767，最严重 case 高达 0.95
  - 案例 news_id=585、442、235 等 rule_risk_score 高出 gold 60 分以上
  - overestimate 的 rule primary type 有 38 条是“链上漏洞/攻击风险”
  - score_diff direction_summary overestimate 74 条中，score_hack 贡献了绝大多数
- patch 建议:
  - 将 score_hack 对最终 risk_score 的贡献权重降低（例如 0.7×）
  - 或引入饱和映射，使 0.85 以上分数的风险贡献不再线性增长
  - 结合文本内容类型，对非直接攻击新闻进行降权
- 可能副作用:
  - 可能使真实攻击新闻的评分下降，导致新的低估
  - 需要监控 false_negative 中的链上漏洞事件是否仍被充分捕获

## Scorer 级诊断

- **score_hack**: 过宽触发 + 强度映射偏高
  - 原因: 误报中触发次数最高（36次），且高分值 case 导致 rule_risk_score 严重高估；案例表明许多非攻击新闻被误判为漏洞风险
  - 建议: 收紧触发关键词（过滤AI、交易策略等），降低强度映射或引入 cap 机制
  - patch 风险: 可能增加 false_negative，尤其 true hack 新闻可能漏报
- **score_volatility**: 漏召回 + 强度映射偏低
  - 原因: type_mismatch 最大错配和 score_diff 低估都与几乎零触发相关；gold 异常行情波动风险大量存在但未触发
  - 建议: 大幅扩充波动相关关键词和语义模式，提升触发后的分数贡献
  - patch 风险: 可能引入少量 false_positive，需通过精准 pattern 控制
- **score_regulatory**: 漏召回为主，同时存在误报
  - 原因: 监管法律类新闻大量被归为“无明显风险”，但同时又有无风险内容被误标为监管风险
  - 建议: 增加监管召回并细化负面排除，提升触发率同时减少误报
  - patch 风险: 召回增强可能增加 false_positive
- **score_whale**: 漏召回 + 强度映射偏高
  - 原因: 巨鲸类新闻大量漏标，已触发的却容易造成过高风险评分
  - 建议: 扩展触发规则并降低强度映射
  - patch 风险: FP 可能增加，需平衡
- **score_macro**: 过宽触发
  - 原因: FP 中触发 11 次，部分案例将无风险宏观讨论标为宏观风险且分数高估
  - 建议: 收紧宏观负面关键词要求，避免仅讨论趋势就触发
  - patch 风险: 可能遗漏真正的宏观政策负面冲击
- **score_outage**: 过宽触发
  - 原因: 交易所正常公告被标为运维风险，案例 news_id=7,617,651 均为过度反应
  - 建议: 限制触发条件为实际中断、攻击或重大变更，排除常规运营公告
  - patch 风险: 可能降低真实运维事件的召回
- **score_team**: 漏召回
  - 原因: 项目治理/团队异常在 type_mismatch 和低估中大量存在但几乎不触发
  - 建议: 补充团队异常、治理危机等关键词
  - patch 风险: 可能增加 FP
- **score_infra**: 漏召回
  - 原因: 基础设施/协议层异常风险未被捕获
  - 建议: 增加基础设施故障、协议漏洞等触发
  - patch 风险: FP 可能小幅增加
- **score_fraud**: 证据不足
  - 原因: FP 中少量触发，但偷漏与误报均不突出
  - 建议: 暂时观察，后续针对性调整
  - patch 风险: 无

## 主类别诊断

- **gold=异常行情波动风险, rule=无明显风险**: score_volatility 过弱，完全无法触发，导致无任何风险信号
  - 修复: 增强 score_volatility 召回，确保波动类新闻至少能被捕获并赋予>0 的分数
  - guardrail: 避免将无明显波动新闻误分到异常行情类别
- **gold=监管与法律风险, rule=无明显风险**: score_regulatory 召回不足，大量监管新闻未被覆盖
  - 修复: 扩充监管关键词，提高 score_regulatory 的触发率
  - guardrail: 防止正常合规新闻被错标为监管风险
- **gold=监管与法律风险, rule=链上漏洞/攻击风险**: score_hack 过强且过宽，抢占了本应由监管类别主导的主类别
  - 修复: 在 primary_type 选择时引入竞争规则，当 regulatory 分数>0.15 且 hack 已不是唯一安全语境时，强制优先考虑监管
  - guardrail: 不能导致真实的漏洞新闻被误归为监管

## patch 顺序

- Step 1: 增强 score_volatility, score_regulatory, score_whale, score_team, score_infra 的召回
  - 动作: 为这些 scorer 添加缺失的关键词库和语义规则，确保其对应 gold 风险类型新闻至少能被触发
  - 收益: 大幅减少 type_mismatch 中的‘->无明显风险’错配，降低 score_diff 低估
  - guardrail: 同时监控 false_positive 的增加，确保新规则不引入大量误报
  - 验证: 检查 type_mismatch 中这些对的减少量，以及 score_diff 低估案例的 rule_risk_score 提升
- Step 2: 校准 score_hack 的触发精度和强度映射
  - 动作: 收紧对非攻击类内容的关键词匹配；降低 score_hack 到最终风险分数的映射权重（如 0.7×）或实施非线性缩放
  - 收益: 减少 false_positive 和 overestimate，防止其抢占其他类别主类别
  - guardrail: 确保 false_negative 数量不增加，真实攻击新闻仍能获得足够风险评分
  - 验证: 检查 false_positive 中 score_hack 的触发次数和 overestimate 案例评分变化；监控 false_negative 链上漏洞事件
- Step 3: 改进主类别选择逻辑
  - 动作: 引入类别间竞争仲裁：当最高分 scorer 与第二高分 scorer 分数接近，且第二高分对应 scorer 与文本语义更一致时，选择第二高分类别；为监管、波动等类别设置保护机制，避免 hack 随意抢占
  - 收益: 减少 type_mismatch，提升高风险类别分配准确率
  - guardrail: 避免规则过于复杂导致其他类别错配加重
  - 验证: 检查 top_mismatch_pairs 的变化，尤其是跨类别抢占的案例
- Step 4: 收紧 score_macro 和 score_outage 的触发条件
  - 动作: 对 score_macro 增加负面语境要求（如下跌、危机）；对 score_outage 排除常规公告，仅对中断、攻击等负面事件触发
  - 收益: 降低这两个 scorer 的 false_positive 和 overestimate
  - guardrail: 确保真正的宏观政策冲击和交易所异常仍能被捕获
  - 验证: 对比 false_positive 中这两个类别的误报数量

## 暂不建议修改

- **不要继续单独压低 score_hack 的整体阈值以降低 false_positive**: 因为可能导致已经稀少的 false_negative 增加，同时无助于解决 type_mismatch 和低估，反而可能让链上漏洞新闻进一步被忽略

**整体置信度**: high
