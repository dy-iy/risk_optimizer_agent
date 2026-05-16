# 规则脚本 Patch 报告（LLM版）

**Patch 版本**: v2.1-patch

## 摘要

- 主类别选取取消硬阈值，最高风险分>0即输出对应风险类型，消除209条类型缺失
- 收紧score_volatility触发条件，增加否定上下文，减少访谈/观点类误报
- 双向修复score_hack：新增商业无关词排除以减少误报，扩展真实攻击关键词并提高有损失事件的分数映射
- 对score_whale增加产品/协议描述类上下文的依赖限制，降低无关内容误触

## 变更明细

### 1. 主类别选取逻辑 (score_all_risks)
- 原因: type_mismatch的209条大部分因硬阈值将低分风险归为“无明显风险”，导致大量真实风险事件类型丢失
- 动作: 移除对HIGH_FP_SCORERS的二次裁决及SECONDARY_THRESHOLD限制，改为仅当最高分<=0时输出无明显风险，否则输出最高分对应类型
- 风险: medium

### 2. score_volatility
- 原因: 误报最多的类别，大量访谈/观点/非市场内容被错误标记为异常行情波动
- 动作: 新增NEG_VOL_NON_MARKET列表（访谈、AMA、AI趋势等），当文本包含此类且无强波动关键词时直接返回0.0
- 风险: low

### 3. score_hack
- 原因: 误报对收购/信贷基金等无害商业新闻给出极高分数；漏报对真实漏洞攻击/损失事件评分过低甚至不触发
- 动作: 新增NEG_HACK_BUSINESS_EXCLUDE列表排除非安全类商业用语；扩展真实攻击关键词（漏洞攻击、坏账、量子攻击等）；在有确认攻击和损失金额>1M时突破NEG_HACK_FALSE的0.2限制，给予高评分
- 风险: medium

### 4. score_whale
- 原因: 部分误报将DeFi产品介绍等非行为性描述判定为巨鲸行为
- 动作: 在score_whale中增加对产品/平台推广类文本的检查，当缺少链上地址/交易哈希特征且包含产品描述词时排除或降分
- 风险: low

## 注意事项

- patch未修改CSV输入输出协议、列名及基础数据流，保持向后兼容
- 主类别选取变更可能导致低分风险类型曝光，需观察后续是否引入新的误报类型偏移
- score_hack的高金额突破逻辑依赖金额提取准确性，需确保‘损失超XX万美元’能被AMT_RE正确捕获
- 建议用历史误报/漏报样本集回归测试，重点验证score_volatility和score_hack的修正效果

## 元信息

- model: deepseek-v4-pro
- syntax_ok: True
- source_script: D:\risk_optimizer_agent\v6\scripts\risk_labeler_v6.py
- analysis_json: D:\risk_optimizer_agent\v6\reports\analysis\risk_labeler_v6_analysis_llm.json
