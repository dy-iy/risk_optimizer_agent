# 风险标注错误模式分析报告（LLM版）

## 样本规模

- False Positive: 42
- False Negative: 7
- Type Mismatch: 259
- Score Diff Top: 200

## 版本变化摘要

- 改善: false_positive_rows 71.0 -> 42.0 (delta -29.0)
- 改善: score_diff_mean 14.222 -> 13.62 (delta -0.602)
- 改善: score_diff_rmse 19.3802 -> 18.5018 (delta -0.8784)
- 恶化: false_negative_rows 2.0 -> 7.0 (delta 5.0)
- 恶化: type_mismatch_rows 236.0 -> 259.0 (delta 23.0)
- 提醒: false_positive 下降但 false_negative_rows, type_mismatch_rows 上升，可能存在单目标优化 false_positive 导致其它指标恶化。

## LLM 总结

- v17→v18 优化使 false_positive 下降 29（71→42），但 false_negative 上升 5（2→7）且 type_mismatch 上升 23（236→259），存在单目标压制误报导致召回和类别错配恶化
- 核心矛盾是 score_hack/score_macro 过宽触发同时强度映射偏高，而 score_volatility/score_regulatory/score_whale 等 scorer 严重欠召回，需同时收紧过激 scorer 并补强缺失类别

## 指标权衡诊断

- 主要改善指标: false_positive
- 恶化指标: type_mismatch, false_negative
- 可能原因: 通过收紧部分触发条件（可能包括 score_hack/score_macro 的阈值或强度映射）降低了误报，但同时导致大量 gold 为 “异常行情波动风险” 和 “监管与法律风险” 的文章被判为 “无明显风险”，以及 “链上漏洞 / 攻击风险” 的高危事件评分不足，错配和漏报明显增加
- 优化提醒: 继续单独压低 false_positive 会进一步恶化 false_negative 和 type_mismatch，必须转为多指标联合优化

## 主要错误模式

### 1. score_hack 和 score_macro 对非安全/非宏观内容的过宽触发 [false_positive]

- 影响 scorer: score_hack, score_macro
- 根因: score_hack 和 score_macro 的触发关键词或上下文模式过于宽泛，且强度映射将中等触发直接映射到高 risk_score，导致大量普通新闻被误报为高风险
- 分数方向: overestimate
- risky_patch: False
- 优先级: high
- 证据:
  - false_positive 规则主类别 top1=链上漏洞/攻击风险 (11)、top2=宏观/政策冲击风险 (11)
  - false_positive 触发分数 top1=score_hack (14次, mean 0.225)、top2=score_macro (11次, mean 0.1266)
  - 样本 news_id 585/235/442 因单一 score_hack 0.75+ 将 gold 低风险推至规则 high，但内容实为 AI 安全/监管裁决/杠杆交易
  - 样本 news_id 749/5/71 因单一 score_macro 0.45+ 将 gold 无明显风险推至宏观冲击，但内容为地缘政治讨论/Web3会议/书籍讨论
- patch 建议:
  - 为 score_hack 增加上下文消歧规则（如必须包含实际漏洞利用/攻击事件，且排除单纯讨论、趋势、会议内容）
  - 降低 score_hack 和 score_macro 的中高强度映射系数（例如 0.4→0.7 区间从直接给 40-80 调整到 20-45）
  - 引入 secondary_type 阈值，当仅有一个 scorer 触发且其他 scorer 为零时，降低最终风险评分或降低主类别置信度
- 可能副作用:
  - 可能使部分真实漏洞/宏观冲击事件的评分下降，增加 false_negative（需同步补强召回）
  - 可能影响链上漏洞攻击类别召回，需与 score_hack 的召回补丁配合

### 2. 链上漏洞/攻击风险高危事件严重低估 [false_negative]

- 影响 scorer: score_hack
- 根因: score_hack 当前触发强度扁平化，未能区分“讨论安全趋势/提及漏洞”与“已发生严重攻击/关键漏洞威胁”，高位触发点缺失
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - false_negative gold 主类别 top1=链上漏洞/攻击风险 (6)
  - 所有 false_negative 样本的 rule_risk_score 均为 30-35，但 gold 高达 70-85
  - 样本 news_id 222/797/12/164/687 均涉及量子攻击/协议漏洞/热钱包被盗等严重安全事件，score_hack 仅 0.3-0.35，未区分高危与普通安全新闻
  - score_hack 在 FN 中 mean_value 0.3429，max 0.35，与 FP 中部分低危样本的 0.35 相同（如 news_id 186）
- patch 建议:
  - 为 score_hack 增加严重程度分层关键词（如“被盗”“冻结”“攻击成功”“损失”“紧急修复”“9 分钟密钥恢复”），给予更强触发
  - 检查 score_hack 是否存在上限压平，允许在高确定性事件中达到 0.9+ 并将 risk_score 映射到 80+
  - 补充对“钱包”“桥”“协议”“跨链”等基础设施的实际攻击线索词的敏感度
- 可能副作用:
  - 可能提升 score_hack 对普通安全讨论的误报（需与收缩通用触发搭配）
  - 提升链上漏洞类别整体分数，可能增加 false_positive 或 type_mismatch（需配合竞争逻辑和阈值）

### 3. “异常行情波动风险”大量被压制为“无明显风险” [type_mismatch]

- 影响 scorer: score_volatility
- 根因: score_volatility 关键字或规则存在盲区，几乎完全无法触发，导致此类 gold 风险被系统忽略
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - type_mismatch 第一大对：gold=异常行情波动风险, rule=无明显风险, count=55
  - score_volatility 在 FP 中仅触发 1 次(mean 0.0144)，在 FN 中触发 0 次，在 score_diff 低估案例中几乎不触发
  - 样本 news_id 8/332/340 明显涉及币价剧烈波动、最大痛点、Meme 币暴涨，但所有 scorer 均为 0
- patch 建议:
  - 大幅扩充 score_volatility 的触发词，覆盖“暴跌”“暴涨”“波动”“回调”“挤压”“突破”“FOMO”等行情描述
  - 增加 price 变动幅度描述的模式匹配（如“下跌超 50%”“市值短时突破”“价格回落至”）
  - 考虑借用价格数据特征或标题中的金额变化数字
- 可能副作用:
  - 若过宽可能将常规行情报道误标为高风险波动（需用强度映射控制，如轻微波动给低分）
  - 可能增加 false_positive（但当前极低，适当激活利大于弊）

### 4. “监管与法律风险”被大量压制为“无明显风险” [type_mismatch]

- 影响 scorer: score_regulatory
- 根因: score_regulatory 关键词覆盖不足或触发条件过严，无法捕获法案、听证、CSRC/CFTC 等常规监管动态
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - type_mismatch 中 gold=监管与法律风险, rule=无明显风险 30 条，且 avg_rule_risk_score 低至 1.43
  - false_negative 中 score_regulatory 未触发，trigger_count=0
  - 样本 news_id 83/174/397 明确涉及韩国立法延迟、英国监管提案、CFTC 听证，但 rule 所有 scorer 为零
  - score_regulatory 在 score_diff 低估 top 中 gold count 25，几乎没有触发
- patch 建议:
  - 扩展 score_regulatory 关键词，包含“法案”“听证”“监管提案”“FCA”“CFTC”“SEC”“规则”“咨询期”“授权”等法律/政策术语
  - 建立国家/机构+监管动作的组合模式，提升触发率
  - 同时注意避免将普通行业讨论过度激活（可采用低强度映射起点）
- 可能副作用:
  - 可能增加 “无明显风险” 被误判为监管类别的 false_positive 型 type_mismatch，已在 mismatch 中体现（gold 无明显→rule 监管 32 条），需谨慎控制强度映射和主类别竞争
  - 若强度过高，会同现 score_diff overestimate

### 5. “大额转账 / 巨鲸行为风险”被判定为“无明显风险” [type_mismatch]

- 影响 scorer: score_whale
- 根因: score_whale 规则缺失或触发条件过于严格（如只捕获特定链上转账事件），未覆盖新闻描述中的巨鲸行为
- 分数方向: underestimate
- risky_patch: False
- 优先级: medium
- 证据:
  - type_mismatch gold=大额转账/巨鲸风险, rule=无明显风险 count=20
  - false_negative 中 score_whale 触发 0 次，mean_value 极低
  - 样本 news_id 482/119/521 涉及大型持有者出售、代币解锁、鲸鱼持仓，全部 scorer 为零
- patch 建议:
  - 增加“鲸鱼”“巨鲸”“大户”“大型持有者”“代币解锁”“上亿枚”等新闻常见巨鲸行为词
  - 降低 score_whale 触发门槛，对弱信号给予低分即可
- 可能副作用:
  - 可能使部分常规转账新闻被误标为鲸鱼风险，需控制低分阈值
  - 可能增加 false_positive 大额转账类别，但当前该类别 FP 仅 3 例，可接受

### 6. “无明显风险”被错误标记为特定风险类别（监管/漏洞/宏观） [type_mismatch]

- 影响 scorer: score_regulatory, score_hack, score_macro
- 根因: 主类别选择逻辑在无显著风险时仍会选取触发最高的 scorer 对应的类别，缺乏“无明显风险”的兜底阈值或最小触发强度要求
- 分数方向: overestimate
- risky_patch: False
- 优先级: high
- 证据:
  - type_mismatch gold=无明显风险→rule=监管与法律 32 条, avg_rule_risk_score 13.94
  - gold=无明显风险→rule=链上漏洞/攻击风险 18 条, avg 29.28
  - gold=无明显风险→rule=宏观/政策冲击 13 条, avg 18.46
  - 这些案例 rule 端分数不高但被选为主类别，显示主类别选择逻辑在低分竞争时易受个别 scorer 微弱触发影响
- patch 建议:
  - 引入最小 primary_type 激活阈值：若所有 scorer 输出均低于某阈值（如 0.3），则规则主类别强制为 “无明显风险”
  - 调整 primary_type 竞争机制，要求获胜类别对应的 scorer 值必须显著高于其他竞争者（如领先 >0.1），否则退回无明显风险
  - 同时降低 score_regulatory 和 score_macro 在低触发时的强度映射，减少弱信号带出错误类别
- 可能副作用:
  - 可能导致部分弱风险文章被判为无明显风险而漏召回（small FN increase）
  - 若阈值过高，会压制真正低分但应标注的类别（可通过保留低分但提高类别为真来缓解）

### 7. score_hack 强度映射严重高估 [score_diff]

- 影响 scorer: score_hack
- 根因: score_hack 的数值到 risk_score 的映射系数过高，单一生效即能主导总分，且未对内容实质威胁做细粒度判断
- 分数方向: overestimate
- risky_patch: False
- 优先级: high
- 证据:
  - overestimate 规则主类型 top 为链上漏洞/攻击风险 23 例，占所有高估的 42.6%
  - overestimate 触发 score 中 score_hack 27 次触发，mean 0.2814，将 gold 低危推至 rule 高危
  - 样本 news_id 585: score_hack=0.819 → rule_risk_score=82 vs gold=20
  - 同时 false_negative 案例显示 score_hack 无法区分高危，呈现扁平化
- patch 建议:
  - 降低 score_hack 对 risk_score 的贡献权重或整体映射斜率，尤其是 0.4-0.7 区间的映射值
  - 引入其他 scorer 的协同验证，单独触发 score_hack 时施加折扣系数，避免单信号过度放大
  - 结合上述严重性分层，让高严重性文本能打出高分，低严重性文本压在中低分
- 可能副作用:
  - 可能进一步降低高危漏洞事件的得分，加剧 false_negative，必须与严重性分层补丁同时上线
  - 影响类别竞争，可能导致部分漏洞文章被其他类别错误吞并

### 8. score_fraud 高估导致诈骗类别分数过度膨胀 [score_diff]

- 影响 scorer: score_fraud
- 根因: score_fraud 对“诈骗”一词的触发过于强烈，直接映射到极高的 risk_score，未区分新闻级别严重程度
- 分数方向: overestimate
- risky_patch: False
- 优先级: medium
- 证据:
  - 样本 news_id 536 (gold=25, rule=88), news_id 483 (gold=35, rule=88) 均因 score_fraud=0.88 拉高
  - overestimate 规则主类型 fraud 5 例
  - false_positive 中 fraud 有 5 例，且触发 mean 0.0895
- patch 建议:
  - 调整 score_fraud 映射，0.8+ 区间需有更强的语义支持（如已确认大规模骗局、具体金额损失），普通案件报告应压缩到 0.4-0.6
  - 避免单一 “诈骗” 关键词直接推至 0.88
- 可能副作用:
  - 可能会降低重大骗局的得分（需通过词组分层保留最高档）
  - 可能影响诈骗类别的 FN（目前 FN 极少）

### 9. 大量类别 scorer 欠拟合导致全局低估 [score_diff]

- 影响 scorer: score_volatility, score_regulatory, score_whale, score_liquidation, score_team, score_infra
- 根因: 多个 scorer 关键词缺失或规则过于严格，导致 gold 有明确风险时系统完全无触发
- 分数方向: underestimate
- risky_patch: False
- 优先级: high
- 证据:
  - underestimate 146 例占总 score_diff 的 73%，gold 异常行情波动风险 42、监管 25、链上漏洞 22、团队 12、鲸鱼 12
  - 这些 scorer 在 underestmate trigger 中几乎为 0
  - score_diff mean_rule_minus_gold = -12.47，系统整体低估
- patch 建议:
  - 批量扩容 score_volatility/score_regulatory/score_whale/score_liquidation/score_team/score_infra 的触发词库，基于样本案例进行针对性补全
  - 对这些 scorer 设置最低激活基准，即使弱信号也给个低分，避免完全零分
  - 提升这些 scorer 的强度映射，确保当有合理触发时，能贡献到最终 risk_score
- 可能副作用:
  - 可能增加对应类别的 false_positive 和 type_mismatch，需配合主类别阈值策略
  - 提升整体风险分数水平线，可能使部分 borderline 文件变为误报

## Scorer 级诊断

- **score_hack**: 过宽触发 + 强度映射偏高 + 顶层区分度不足
  - 原因: FP 中频繁对无风险或低风险内容触发 0.35-0.819；FN 中对真实严重攻击只给 0.35；高估案例占主导；主类别竞争中常将无明显风险判为漏洞类别
  - 建议: 收紧通用关键词触发，引入严重性分层（高危害事件给 0.8+，趋势讨论给 0.2 以下），降低中段映射系数
  - patch 风险: 若只收紧不补强高危触发，会恶化漏洞类 FN
- **score_macro**: 过宽触发 + 强度映射偏高
  - 原因: FP 中 top2 类别，样本显示对地缘政治讨论、Web3 愿景等非冲击内容给出 0.45-0.51，直接推至 medium-high
  - 建议: 精简触发词，排除“地缘政治”“碎片化”“技术共和国”等泛泛讨论，降低强度映射
  - patch 风险: 可能漏掉真正的宏观政策冲击，但当前 macro 类 FN 极少，风险可控
- **score_fraud**: 强度映射偏高
  - 原因: FP 样本显示 0.55 即给 55 分，0.88 给 88 分，gold 评分多为 25-35
  - 建议: 重新校准强度映射，0.5→30, 0.8→50 更合理
  - patch 风险: 可能降低重大诈骗报道的分数，但当前偏低 gold 允许下调
- **score_volatility**: 漏召回（几乎完全沉默）
  - 原因: type_mismatch 波动类 55 例被判为无明显风险，score_volatility 几乎不触发
  - 建议: 全面扩充触发词和模式，必须激活该 scorer
  - patch 风险: 可能误触发普通价格变动新闻，需用低强度映射控制
- **score_regulatory**: 漏召回为主，兼有轻微过宽
  - 原因: 30 例 gold 监管法律被判为无明显风险（零触发），同时 32 例 gold 无风险被判为监管（低分触发），说明触发阈值和关键词精度需要提升
  - 建议: 扩充监管关键词并提高触发率，同时设定最小触发强度要求或竞争阈值，避免弱信号主导类别
  - patch 风险: 若只扩招不回撤弱信号，可能增加无明显风险→监管的 mismatch
- **score_whale**: 漏召回
  - 原因: 20 例 gold 鲸鱼风险被判为无明显风险，完全未触发
  - 建议: 增加新闻类鲸鱼行为关键词，降低触发门槛
  - patch 风险: 可能增加 false_positive 大额转账风险
- **score_liquidation**: 漏召回
  - 原因: 低估 gold 中爆仓/清算风险 7 例，trigger_count 极低
  - 建议: 扩充清算、爆仓相关关键词
  - patch 风险: 可能触发普通杠杆讨论，低强度映射可控
- **score_team**: 漏召回
  - 原因: 低估 gold 项目治理/团队异常 12 例，完全未触发
  - 建议: 建立团队异常、治理风险的关键词库
  - patch 风险: 新增类别误报风险，但当前为零，可逐步添加
- **score_infra**: 漏召回
  - 原因: 低估 gold 基础设施/协议层异常 7 例未触发
  - 建议: 添加基础设施故障、协议异常等词
  - patch 风险: 低风险，需监控
- **score_solvency**: 证据不足
  - 原因: FP 中有少量触发，但样本未展示；overestimate 有 5 例偿付能力，可能强度映射偏高，但案例不足
  - 建议: 收集具体案例再决定
  - patch 风险: 暂不处理
- **score_outage**: 过宽触发
  - 原因: FP 样本显示交易所下架、开源项目回应等被误判为 outage，score_outage 0.5 直接给中等风险
  - 建议: 收紧 outage 定义，排除正常运营公告
  - patch 风险: 可能错过真实系统故障，需谨慎
- **score_stablecoin**: 证据不足
  - 原因: 触发极少，但有 1 例 FP 和 3 例 type_mismatch 稳定币→无明显风险，可能需扩充
  - 建议: 暂时观察，优先处理高频错误
  - patch 风险: 暂不处理

## 主类别诊断

- **gold=异常行情波动风险, rule=无明显风险**: gold scorer 过弱 -- score_volatility 几乎完全失效
  - 修复: 激活并增强 score_volatility，使其能在行情波动新闻中触发低到中分，从而让主类别有机会胜出
  - guardrail: 避免行情类文章全部给出高危，需用低分激活即可
- **gold=无明显风险, rule=监管与法律风险**: rule scorer 过强 + 主类别选择逻辑缺乏最低强度阈值
  - 修复: 降低 score_regulatory 对弱监管词的强度映射，并引入类别胜出阈值，避免微弱 signal 带出错误类别
  - guardrail: 避免把真正应标注监管的弱信号文章退回无明显风险
- **gold=监管与法律风险, rule=无明显风险**: gold scorer 过弱
  - 修复: 大幅增强 score_regulatory 的触发词和模式，使之能在监管新闻中激活
  - guardrail: 平衡触发，防止倒向 gold=无明显风险, rule=监管的错配
- **gold=大额转账 / 巨鲸行为风险, rule=无明显风险**: gold scorer 过弱 -- score_whale 未触发
  - 修复: 扩充 score_whale
  - guardrail: 避免过度触发
- **gold=无明显风险, rule=链上漏洞 / 攻击风险**: rule scorer 过强 + 主类别选择逻辑问题
  - 修复: 收紧 score_hack 通用触发并调整主类别激活阈值
  - guardrail: 防止真实漏洞被压制
- **gold=无明显风险, rule=宏观 / 政策冲击风险**: rule scorer 过强
  - 修复: 收紧 score_macro
  - guardrail: 防止漏掉重大宏观冲击

## patch 顺序

- Step 1: score_volatility
  - 动作: 大幅扩充触发词库（暴跌、暴涨、价格波动、回调、挤压等）并设置低强度映射（0.1~0.4），保证行情类新闻不再完全沉默
  - 收益: 直接减少 gold 异常行情波动风险→无明显风险 mismatch 55 例，并改善 underestimation 42 例
  - guardrail: 不要导致普通行情分析被标为高风险，控制 score 贡献在 low 区域
  - 验证: type_mismatch_rows 中 (异常行情波动→无明显风险) 数量，false_negative 行情相关增幅，score_diff 低估 reduction
- Step 2: score_regulatory
  - 动作: 增加监管法律关键词（法案、听证、FCA、CFTC、规则制定等），并降低弱信号时的类别抢占能力（引入 primary_activation_threshold）
  - 收益: 减少 gold 监管→无明显风险 30 例，并改善低估监管案例；同时控制无明显→监管的 32 例错配
  - guardrail: 新触发不能将普通行业文章推至监管类别，需验证 false_positive 和 type_mismatch (无明显→监管) 不上升
  - 验证: type_mismatch 两方向变化，score_diff 低估中监管案例数，FN 中监管法律风险出现
- Step 3: score_hack 强度映射与严重性分层
  - 动作: 对 score_hack 引入严重性等级：低危讨论/趋势≤0.3，实际攻击/漏洞利用>0.7，并降低中段 mapping，同时保证真正高危事件能触及 0.9 并获得 high risk_score
  - 收益: 同时减少 hack 类 FP（top1）和 FN（6 例），改善 overestimate 和 underestimation
  - guardrail: 必须确保量子攻击、协议冻结等 FN 案例得分提升，不能恶化为更严重漏报
  - 验证: false_negative_rows (hack), false_positive_rows (hack), score_diff over/underestimate hack 分布
- Step 4: score_macro 收紧
  - 动作: 缩小触发范围，排除泛化讨论和愿景类文章，降低强度映射，使单纯宏观讨论映射到 20-30 分而非 45+
  - 收益: 减少 macro FP 和 gold无明显→macro mismatch 13 例，改善 overestimate
  - guardrail: 宏观政策冲击真实事件（如监管打压、关税冲击）仍需高触发
  - 验证: FP macro 数量，type_mismatch 无明显→宏观 数量
- Step 5: score_fraud 强度映射
  - 动作: 调低 fraud 映射曲线，0.5→30, 0.8→50 左右，并防止单一关键词触发极高分
  - 收益: 减少 high overestimate fraud 案例
  - guardrail: 重大骗局报道仍应获得 moderate-high 分数
  - 验证: score_diff overestimate 中 fraud 案例分数差
- Step 6: 主类别竞争逻辑
  - 动作: 引入最小 primary_type 激活阈值和类别领先 margin 要求，当无明显风险条件满足时直接输出无明显风险
  - 收益: 同时减少多类型 mismatch（无明显被错配为各类别），尤其是低分主导的现象
  - guardrail: 不能将真正有弱风险但应标注的文章抹掉，阈值需通过验证集调优
  - 验证: type_mismatch_rows 全面下降，false_negative 不显著增加
- Step 7: score_whale / score_liquidation / score_team / score_infra 补强
  - 动作: 根据样本扩充关键词，确保 gold 对应类别文章能被探测到 (低分激活)
  - 收益: 减少低估和 type_mismatch 中这些类别被判为无明显风险的情况
  - guardrail: 监控对应类别的 false_positive 增量，但预期可控
  - 验证: type_mismatch 中各 gold 类别对应规则类别无明显风险的数量减少

## 暂不建议修改

- **score_hack 单纯打压分数而不做严重性分层**: 会导致已存在的严重漏洞漏报加剧，false_negative_rows 急升
- **score_regulatory 仅提升触发而不调整主类别阈值**: 会加剧无明显风险→监管类的 type_mismatch

**整体置信度**: high
