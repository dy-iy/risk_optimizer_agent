import json
import re
import pandas as pd

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}

SOFTENER_WORDS = [
    "如果", "可能", "一旦", "若", "倘若", "警告", "推测", "预计",
    "预测", "假设", "拟定", "草案", "拟", "未来", "疑似", "猜测", "不确定",
    "倘若", "或", "疑似", "也许", "或许"
]

OPINION_CONTEXT = [
    "何一", "Richard Teng", "论坛", "演讲", "回顾", "观点",
    "深度分析", "报告", "评论", "分析", "讨论", "专访",
    "直播", "线上会议", "专家", "分析师", "表示", "展望", "研讨",
    "研究", "采访", "访谈", "总结", "评估", "探讨"
]


# ---------- 辅助函数 ----------
def hit(text, words):
    """判断 words 中是否有任意词出现在 text 中（忽略大小写）"""
    text = str(text).lower()
    return any(word.lower() in text for word in words)


def is_opinion_text(text):
    """检测是否为观点/演讲/历史回顾等非事件性新闻"""
    return hit(text, OPINION_CONTEXT)


def softener_present(text):
    """检测文本中是否存在软化/条件性词汇"""
    return hit(text, SOFTENER_WORDS)


def has_large_amount(text):
    """检查文本中是否包含大额数字描述（如金额/数量）"""
    pattern = r'\d+\s*(万|亿|千|百)?\s*(美元|枚|个|BTC|ETH|btc|eth)'
    if re.search(pattern, text, re.IGNORECASE):
        return True
    # 额外的大额修饰词
    if hit(text, ["天量", "巨量", "大额", "大量"]):
        return True
    return False


def has_definitive_event(text):
    """检测是否存在明确已发生的事件描述（导致、损失、已被、无法等）"""
    keywords = ["导致", "损失", "已被", "无法", "已发生", "确认", "遭"]
    return hit(text, keywords)


def negation_present(text, target_words):
    """检测文本中是否在目标词周围出现否定词"""
    neg_phrases = ["未发现", "没有", "否认", "并非", "不存在", "无"]
    if not any(phrase in text for phrase in neg_phrases):
        return False
    return True


# ---------- Label Functions ----------
def lf_01(text):
    # 排除研究报告、第三方披露等未确认实际攻击的情况
    if hit(text, ["Tiger Research", "披露", "研究报告"]) and not hit(text, ["损失", "攻击损失", "已被攻击", "导致损失", "确认攻击"]):
        return None
    # 排除纯粹第三方工具漏洞或基础设施问题，且不直接造成加密资产损失
    if hit(text, ["AI工具漏洞", "Vercel基础设施", "第三方工具漏洞"]) and not hit(text, ["加密资产", "代币", "被盗", "盗取", "损失", "攻击项目", "协议损失"]):
        return None
    # 观点文本且无明确损失证据直接返回 None
    if is_opinion_text(text) and not has_definitive_event(text):
        return None

    high_phrases = [
        "黑客攻击", "漏洞攻击", "遭攻击", "攻击事件", "攻击损失",
        "被攻击", "遭黑客", "资金被盗", "攻击者利用",
        "rsETH可疑活动", "暂停合约并启动调查"
    ]
    uncertainty_words = ["疑似", "疑遭到", "可能被", "被认为"]
    if hit(text, high_phrases):
        reason_high = "新闻涉及加密货币项目遭黑客攻击或重大安全漏洞，直接导致资金损失或市场影响，属于高风险事件。"
        # 明确的损失和已发生事件（不检查大额），后辅以金额判断
        definitive_loss = hit(text, ["损失", "被盗", "盗取", "导致", "金额"]) and has_definitive_event(text)
        if hit(text, uncertainty_words):
            # 不确定性时，根据金额判断
            if definitive_loss:
                # 提取最大美元金额
                amount_pattern = r'(\d+\.?\d*)\s*万\s*美[元金]|(\d+\.?\d*)\s*亿\s*美[元金]|(\d+\.?\d*)\s*美[元金]'
                matches = re.findall(amount_pattern, text)
                max_usd = 0
                for m in matches:
                    if m[0]:
                        val = float(m[0]) * 10000
                        if val > max_usd:
                            max_usd = val
                    elif m[1]:
                        val = float(m[1]) * 100000000
                        if val > max_usd:
                            max_usd = val
                    elif m[2]:
                        val = float(m[2])
                        if val > max_usd:
                            max_usd = val
                if max_usd >= 500000:
                    return ("链上漏洞 / 攻击风险", "high", reason_high)
                else:
                    return ("链上漏洞 / 攻击风险", "medium", "新闻提及疑似的攻击事件并有损失，但金额较小或无法量化，风险中等。")
            else:
                return ("链上漏洞 / 攻击风险", "low", "新闻提及潜在或疑似的攻击事件，但未确认，风险较低。")
        else:
            if definitive_loss:
                return ("链上漏洞 / 攻击风险", "high", reason_high)
            else:
                return ("链上漏洞 / 攻击风险", "medium", "新闻提及安全漏洞，但缺少已证实的攻击损失，风险中等。")
    if hit(text, ["黑客", "漏洞", "攻击", "exploit", "hack"]) and hit(text, ["损失", "被盗", "盗取", "影响", "风险"]):
        reason = "新闻提及安全攻击或漏洞，并可能造成资金损失或市场影响，构成链上漏洞风险。"
        if softener_present(text) and not has_definitive_event(text):
            return ("链上漏洞 / 攻击风险", "medium", reason)
        return ("链上漏洞 / 攻击风险", "high", reason)
    if hit(text, ["漏洞", "安全漏洞", "协议漏洞", "智能合约漏洞"]) and not hit(text, ["攻击", "黑客", "损失", "盗"]):
        level = "low" if softener_present(text) else "medium"
        reason = "新闻提及协议或智能合约漏洞，但未提及实际攻击或损失，存在潜在风险。"
        return ("链上漏洞 / 攻击风险", level, reason)
    return None


def lf_02(text):
    high_terms = [
        "被骗", "骗局", "欺诈", "rug pull", "跑路",
        "网络钓鱼", "假冒应用", "山寨应用", "代币操纵", "巨亏",
        "被控诈骗", "诈骗案", "诈骗案件", "诈骗活动",
        "庞氏骗局", "资金盘", "空气币", "虚假宣传", "冒充专家",
        "投资骗局", "加密骗局", "币圈骗局"
    ]
    medium_terms = [
        "可疑活动", "调查", "涉嫌诈骗", "涉嫌欺诈",
        "诈骗警告", "风险警示", "代币拉高出货", "honeypot",
        "rug pull 风险", "跑路风险"
    ]
    low_terms = ["诈骗", "骗局", "网络钓鱼", "假冒"]

    # 扩大排除与黑客攻击重叠的词集，但若同时出现诈骗/跑路等词则不排除
    if (hit(text, ["黑客攻击", "漏洞攻击", "安全漏洞", "攻击事件", "rsETH事件", "黑客攻击事件"]) or (hit(text, ["调查"]) and hit(text, ["黑客", "攻击", "漏洞"]))) and not hit(text, ["诈骗", "骗局", "欺诈", "rug pull", "跑路"]):
        return None

    # 若有否定语境，直接忽略
    if negation_present(text, high_terms + medium_terms + low_terms):
        return None

    if hit(text, high_terms):
        level = "high"
        reason = "文本包含明确已发生的诈骗、跑路或Rug Pull事件关键词，涉及资产损失、指控或已确认的欺诈行为，构成高风险。"
    elif hit(text, medium_terms):
        # 若仅匹配中性词（如“调查”）且未出现明确诈骗/跑路词，则不触发
        if not (hit(text, high_terms) or hit(text, low_terms)):
            return None
        level = "medium"
        reason = "文本包含可疑活动、调查或诈骗警告等关键词，表明存在潜在的诈骗/跑路风险，需警惕。"
    elif hit(text, low_terms):
        level = "low"
        reason = "文本提及诈骗相关词汇，但无具体事件或损失描述，风险较低。"
    else:
        return None

    return ("诈骗 / 跑路 / Rug Pull 风险", level, reason)


def lf_03(text):
    text_lower = text.lower()
    high_strict = ["被捕", "逮捕", "监禁", "被判", "盗取", "窃取"]
    future_or_draft = ["草案", "拟", "提案", "尚未", "拟议", "将"]

    for w in high_strict:
        if w.lower() in text_lower:
            if hit(text, future_or_draft):
                level = "low"
                reason = f"文本包含高风险法律关键词：{w}，但处于草案或未生效状态，风险较低。"
            else:
                level = "high"
                reason = f"文本包含高风险法律关键词：{w}，涉及刑事、欺诈或重大法律行动，风险等级为高。"
            return ("监管与法律风险", level, reason)

    broad_high = ["刑事", "诈骗", "骗局", "rug pull", "欺诈"]
    for w in broad_high:
        if w.lower() in text_lower:
            if softener_present(text):
                level = "low"
                reason = f"文本提及法律风险关键词 {w}，但带有条件或软化语气，风险较低。"
            else:
                level = "medium"
                reason = f"文本涉及可能的法律风险关键词 {w}，但未见具体已发生的重大行动，中等风险。"
            return ("监管与法律风险", level, reason)

    # 将警告/威胁组合降为低风险
    if hit(text, ["警告", "威胁"]) and hit(text, ["监管", "SEC", "政府", "央行"]):
        return ("监管与法律风险", "low", "文本包含监管机构的警告或威胁性表态，但尚未形成实际政策或行动，风险较低。")

    medium_phrases = ["金融法草案", "OFAC 制裁", "冻结 资金", "扣押",
                      "监管权力扩大", "禁止 加密", "政策收紧"]
    for p in medium_phrases:
        if p.lower() in text_lower:
            return ("监管与法律风险", "medium", f"文本包含中等风险监管关键词：{p}，涉及政策、法律草案或监管行动，风险等级为中等。")
    if "监管" in text_lower and "草案" in text_lower:
        return ("监管与法律风险", "medium", "文本同时提及监管和草案，可能涉及监管政策变动，风险等级为中等。")
    if "法案" in text_lower and "权力" in text_lower:
        return ("监管与法律风险", "medium", "文本同时提及法案和权力扩大，可能涉及监控或冻结权力，风险等级为中等。")
    if "制裁" in text_lower and "地址" in text_lower:
        return ("监管与法律风险", "medium", "文本提及制裁和地址，可能涉及OFAC制裁名单，风险等级为中等。")

    return None


def lf_04(text):
    # 防止 DeFi 借贷平台等非交易所实体误报
    if hit(text, ["借贷平台", "借贷协议", "DeFi 协议", "去中心化", "DEX"]) and not hit(text, ["中心化交易所"]):
        return None

    if hit(text, ["黑客攻击", "遭黑客攻击", "平台遭遇攻击", "被盗", "盗取",
                    "被黑客", "损失超过", "损失.*万", "损失.*亿美元",
                    "损失.*枚", "大额损失"]) and hit(text, ["交易所", "平台", "交易平台"]):
        if not hit(text, ["借贷平台", "借贷协议", "去中心化"]):
            return ("交易所与系统运维风险", "high", "交易所/平台遭遇黑客攻击导致资产损失或服务中断，风险高")
    if hit(text, ["冷钱包无法访问", "私钥未移交", "私钥.*丢失", "冷钱包.*私钥"]) and hit(text, ["交易所", "平台"]):
        if not hit(text, ["借贷平台", "借贷协议", "去中心化"]):
            return ("交易所与系统运维风险", "high", "冷钱包/私钥问题导致资产不可用，可能引发挤兑，风险高")
    if hit(text, ["异常提取", "账户余额异常", "异常大额抛售"]) and hit(text, ["平台", "交易所"]):
        if not hit(text, ["借贷平台", "借贷协议", "去中心化"]):
            return ("交易所与系统运维风险", "high", "平台出现异常提取或账户异常，可能涉及攻击或系统问题，风险高")
    if hit(text, ["停止支持.*充值提现", "停止支持.*充值.*提现", "停止支持.*充值",
                    "暂停充值提现", "暂停.*充值.*提现"]) and hit(text, ["币安", "交易所", "平台"]):
        if not hit(text, ["借贷平台", "借贷协议", "去中心化"]):
            return ("交易所与系统运维风险", "low", "交易所公告停止特定网络充提，属提前通知的运维调整，风险较低")
    if hit(text, ["暂停交易", "暂停运营", "提款.*暂停", "提款.*不可用", "暂停.*交易"]) and hit(text, ["交易所", "平台"]):
        if not hit(text, ["借贷平台", "借贷协议", "去中心化"]):
            return ("交易所与系统运维风险", "medium", "交易所/平台暂停交易或运维中断，可能影响用户资产安全")
    if hit(text, ["系统故障", "技术问题", "维护.*暂停", "维护.*停止"]) and hit(text, ["交易所", "平台"]):
        if not hit(text, ["借贷平台", "借贷协议", "去中心化"]):
            return ("交易所与系统运维风险", "medium", "交易所出现系统故障或维护，影响服务")
    return None


def lf_05(text):
    # 稳定币相关词（包含具体币种和泛指）
    stable_keywords = ["USDT", "Tether", "USDC", "DAI", "UST", "BUSD", "FDUSD", "Dai", "稳定币", "stablecoin"]
    if not hit(text, stable_keywords):
        return None
    # 移除宽泛的“暴跌”，避免代币行情误触发
    high_words = ["脱锚", "挤兑", "黑客攻击", "漏洞", "停止赎回",
                  "冻结", "崩盘", "归零", "攻击事件", "安全漏洞"]
    if hit(text, high_words):
        return ("稳定币异常风险", "high", "新闻明确报道特定稳定币异常事件，如脱锚、攻击等。")
    return None


def lf_06(text):
    liquidation_words = ["爆仓", "强制平仓", "清算", "空单挤压", "强平", "爆仓潮", "空头挤压", "挤压"]
    if not hit(text, liquidation_words):
        return None
    # 排除传统金融结算
    if hit(text, ["对账", "银行清算", "贸易结算", "结算系统", "替代中后台清算结算", "清算结算"]) and not hit(text, ["杠杆", "头寸", "爆仓", "保证金"]):
        return None
    if negation_present(text, liquidation_words):
        return None

    severe = ["大规模爆仓", "大规模清算", "无法提取", "坏账风险", "利用率达100%", "部分清算", "全部清算", "系统性风险"]
    large_amount = ["亿美元", "百万美元"]

    # 检查文本中是否含有超过2亿美元的金额
    amount_pattern = r'(\d+\.?\d*)\s*万\s*美[元金]|(\d+\.?\d*)\s*亿\s*美[元金]|(\d+\.?\d*)\s*美[元金]'
    matches = re.findall(amount_pattern, text)
    max_usd = 0
    for m in matches:
        if m[0]:
            val = float(m[0]) * 10000
            if val > max_usd:
                max_usd = val
        elif m[1]:
            val = float(m[1]) * 100000000
            if val > max_usd:
                max_usd = val
        elif m[2]:
            val = float(m[2])
            if val > max_usd:
                max_usd = val

    has_large_above_200m = max_usd >= 200_000_000

    if softener_present(text):
        # 软化语气下，除非命中 severe 且金额巨大或系统性破坏证据，否则保持 low
        if hit(text, severe):
            if has_large_above_200m or hit(text, ["无法提取", "坏账风险", "系统性风险"]):
                level = "high"
            else:
                level = "low"
        else:
            level = "low"
        reason = "文本提及可能的爆仓或清算，但带有条件或软化语气，风险等级视具体情况而定。"
    else:
        if hit(text, severe):
            if has_large_above_200m or hit(text, ["无法提取", "坏账风险", "系统性风险"]):
                level = "high"
            else:
                level = "medium"
            reason = "检测到严重爆仓/清算事件，涉及无法提取或系统性风险。"
        elif hit(text, large_amount):
            level = "medium"
            reason = "检测到大额爆仓/清算事件，存在较大市场影响。"
        else:
            level = "medium"
            reason = "检测到爆仓/清算相关关键词，存在杠杆头寸被清算风险。"
    return ("爆仓 / 清算风险", level, reason)


def lf_07(text):
    # 扩展触发地址词，增加项目方、团队等可能持有大额的主体
    if not ("0x" in text or "巨鲸" in text or "鲸鱼" in text or "大户" in text or "地址" in text or "项目方" in text or "团队" in text or "基金会" in text):
        return None
    if is_opinion_text(text):
        return None

    action_words = ["转账", "转移", "出售", "抛售", "开仓", "平仓", "买入", "卖出", "增持", "减持",
                    "空单", "多单", "建仓", "清仓", "移出", "转入", "开立", "创建", "建立"]
    negative_actions = ["出售", "抛售", "减持", "清仓", "移出", "转入交易所"]

    if not hit(text, action_words):
        return None
    if not has_large_amount(text):
        return None

    # 排除减仓空单、平仓等无明确抛售风险的动作
    if hit(text, ["减仓空单", "平仓"]) and not hit(text, ["出售", "抛售", "减持"]):
        pass  # 继续检查，不直接排除，大额操作仍可能给低风险

    if hit(text, ["黑客", "非法", "盗取", "窃取"]):
        return ("大额转账 / 巨鲸行为风险", "high", "涉及非法或黑客相关的大额转账，可能严重冲击市场。")
    if hit(text, ["项目方", "创始人", "团队"]) and hit(text, ["出售", "抛售", "释放"]):
        return ("大额转账 / 巨鲸行为风险", "high", "项目方或团队出现大额抛售或代币释放行为，可能造成严重抛压。")

    # 任何大额操作（包括开仓、平仓、买入等）均视为潜在市场波动风险，等级为 low
    if hit(text, negative_actions):
        return ("大额转账 / 巨鲸行为风险", "low", "新闻提到巨鲸地址的大额操作行为（如抛售/转账至交易所），可能引发市场波动。")
    else:
        return ("大额转账 / 巨鲸行为风险", "low", "新闻提到巨鲸或大额地址的加密资产操作，可能影响市场。")


def lf_08(text):
    if is_opinion_text(text):
        return None
    # 排除仅涉及借贷利率变化的描述，且不包含币价/市场关键词
    if hit(text, ["借贷利率", "借款利率"]) and not hit(text, ["币", "行情", "价格", "市场", "代币"]):
        return None
    if hit(text, ["利率", "利用率"]) and not hit(text, ["币", "行情", "价格", "市场"]):
        return None
    words = ["暴跌", "暴涨", "闪崩", "崩盘", "跳水", "雪崩", "砸盘",
             "断崖式下跌", "腰斩", "翻倍", "十倍", "百倍", "千倍",
             "万倍", "跌超", "涨超", "下挫", "急跌", "急涨", "飙升",
             "大跌", "大涨", "剧烈波动", "空头挤压", "挤压", "集中到期",
             "大规模清算", "暴跌超", "暴涨超"]
    if hit(text, words):
        if hit(text, ["借贷利率", "借款利率"]) and not hit(text, ["币", "行情", "价格"]):
            return None
        if softener_present(text):
            return ("异常行情波动风险", "medium", "文本包含潜在的或预期中的行情波动描述，但尚未确认发生。")
        return ("异常行情波动风险", "high", "文本包含极端行情描述词，表明价格可能异常波动")
    return None


def lf_09(text):
    high_phrases = ["无法找回", "锁死", "无限期", "特权地址", "链下管理",
                    "跑路", "Rug Pull", "严重漏洞",
                    "无法继续处理", "漏洞仍未修复"]
    # 移除宽泛的“项目方”，剩余词需结合明确动作才触险
    medium_phrases = ["停止运营", "终止服务", "团队控制", "集中度高",
                      "供应量集中", "高度集中", "退出", "关闭", "团队钱包",
                      "抛售"]
    if hit(text, high_phrases):
        return ("项目治理 / 团队异常风险", "high", "检测到团队异常高风险关键词（如资金锁定、无法找回、特权地址等）")
    elif hit(text, medium_phrases):
        # 必须同时出现负面动作才触发，否则忽略
        action_keywords = ["停止运营", "关闭", "终止", "抛售", "退出"]
        if hit(text, action_keywords):
            if "退出" in text and not any(w in text for w in ["停止运营", "项目方", "团队", "终止"]):
                return None
            return ("项目治理 / 团队异常风险", "medium", "检测到团队异常中风险关键词（如停止运营、高集中度、抛售等）")
        else:
            return None
    return None


def lf_10(text):
    # 排除纯物理犯罪 / 非金融风险事件
    if hit(text, ["绑架", "勒索", "抢劫", "人质", "凶杀", "暴力袭击"]) and not hit(text, ["流动性", "偿付", "储备", "资金池", "赎回"]):
        return None
    # 积极缓解语境排除
    if hit(text, ["推出", "解决方案", "赎回协议"]) and hit(text, ["赎回", "流动性"]):
        return None

    # 检测 TVL 下降组合：需要同时出现风险词或下降百分比超过30%才触发
    tvl_risk = False
    if ("TVL" in text or "tvl" in text) and "下降" in text:
        # 进一步检查是否有风险词或百分比证据
        if hit(text, ["挤兑", "暂停", "危机", "风险", "提款", "赎回"]) or re.search(r'(下降|减少|流失)\s*\d{2,}%', text):
            tvl_risk = True
        else:
            # 仅有 TVL下降但无其他佐证，等级降为 low
            return ("偿付能力 / 储备 / 流动性风险", "low", "新闻提及TVL下降，但未伴随其他风险信号，风险较低。")
    
    # 增加撤资匹配
    if hit(text, ["大规模撤资", "资金外逃", "数十亿美元资金逃离", "撤资数十亿美元", "资金逃离"]):
        if softener_present(text) and not has_definitive_event(text):
            return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻提及大规模撤资或资金外逃，但语气不确定。")
        return ("偿付能力 / 储备 / 流动性风险", "high", "新闻明确报道大规模撤资或资金外逃，流动性风险高。")
    
    high_severe = ["无法提取资金", "无法提款", "无法赎回", "流动性枯竭", "偿付能力危机", "资不抵债", "挤兑",
                   "暂停提现", "暂停提款", "暂停赎回", "暂停存款", "储备不足", "清算失败", "清算困难", "坏账",
                   "大规模赎回", "大量赎回", "赎回潮", "无法处理提款", "资产减值", "债务削减", "价值减记",
                   "暂无法处理提款", "提款暂无法处理", "暂无法处理"]
    high_general = ["坏账风险", "大规模资金外流", "数十亿美元资金逃离", "资金外逃", "TVL下降", "大规模撤资", "资产减值", "减记"]
    medium_terms = ["流动性紧张", "流动性压力", "流动性危机", "储备下降",
                    "偿付能力受质疑", "可能违约", "流动性问题",
                    "提款限制", "赎回限制", "提款延迟", "赎回延迟", "利用率上升",
                    "削减", "减值", "面临危机"]

    # 高利用率检测 (95%~100%)
    if re.search(r'利用率.*?((9[5-9]|100)\s*%)', text):
        if hit(text, ["导致", "引发", "无法", "暂停", "危机", "挤兑", "风险", "问题"]):
            return ("偿付能力 / 储备 / 流动性风险", "high", "新闻明确提及资金池利用率达到极高水平并引发风险，流动性问题严重。")
        else:
            return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻提及资金池利用率极高，存在潜在流动性压力。")

    # 暂无法处理提款等特殊短语 (正则匹配)
    if re.search(r'暂无法处理.*提款|提款.*暂无法处理|暂无法.*提现|提现.*暂无法', text):
        if softener_present(text) and not has_definitive_event(text):
            return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻提及提款处理暂缓，可能影响流动性。")
        return ("偿付能力 / 储备 / 流动性风险", "high", "新闻明确报道提款处理暂停，流动性风险高。")

    if hit(text, high_severe):
        return ("偿付能力 / 储备 / 流动性风险", "high", "新闻明确提及严重的流动性或偿付能力风险，如资金无法提取、大规模撤资、资金池利用率过高或坏账问题。")
    if hit(text, high_general):
        if softener_present(text) and not has_definitive_event(text):
            return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻提及流动性风险，但语气软化或尚未发生。")
        return ("偿付能力 / 储备 / 流动性风险", "high", "新闻明确提及较大的流动性或偿付能力风险。")
    if hit(text, medium_terms):
        if softener_present(text) and not has_definitive_event(text):
            return ("偿付能力 / 储备 / 流动性风险", "low", "新闻提及流动性压力迹象，但不确定性较高。")
        return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻提及流动性或偿付能力方面的压力，如流动性紧张、提款限制等。")
    if tvl_risk:
        return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻明确报道TVL下降并伴有风险信号，可能预示偿付压力或流动性危机。")
    return None


def lf_11(text):
    words = ['暂停合约', '暂停服务', '合约攻击', '网关攻击', '桥漏洞',
             '利用漏洞', '协议攻击', '异常活动', '社会工程攻击',
             '劫持', '中断服务', '节点宕机', '网络拥堵',
             '提案超时', '出块停止', '高利用率', '资金外流',
             '安全漏洞', '敏感数据泄露', '数据泄露', '未授权访问']
    if not hit(text, words):
        return None
    matched = [w for w in words if w in text]
    high_words = ['合约攻击', '桥漏洞', '未授权铸造', '暂停合约', '大规模铸造',
                  '资金外流', '高利用率', '盗取', '获利']
    medium_words = ['暂停服务', '劫持', '社会工程攻击', '异常活动']
    reason_str = '、'.join(matched[:3])
    if any(w in text for w in high_words):
        level = 'high'
    elif any(w in text for w in medium_words):
        level = 'medium'
    else:
        level = 'low'
    # 若无明确利用或损失证据，降级为 low
    if level in ['high', 'medium'] and not (has_definitive_event(text) or hit(text, ["损失", "盗取", "获利", "影响", "导致", "攻击成功"])):
        level = 'low'
    return ('基础设施 / 协议层异常风险', level, f'新闻内容涉及{reason_str}')


def lf_12(text):
    macro_words = ["地缘政治", "战争", "核谈判", "制裁", "封锁", "霍尔木兹",
                   "石油危机", "能源供应链", "谈判失败", "地缘紧张",
                   "政策冲击", "利率决议", "关税", "贸易战", "全球供应链",
                   "美股期货", "全球市场波动", "利率决议预期", "亚太市场"]
    if not hit(text, macro_words):
        return None
    if softener_present(text):
        return ("宏观 / 政策冲击风险", "low", "文本提及宏观或政策冲击的潜在可能，未确认发生。")
    if hit(text, ["战争", "制裁", "封锁", "关税", "危机"]):
        return ("宏观 / 政策冲击风险", "high", "文本确认重大宏观/政策冲击事件已发生，可能强烈影响市场。")
    else:
        return ("宏观 / 政策冲击风险", "medium", "文本提及宏观或政策冲击事件，如地缘政治紧张、贸易冲突、能源危机等，可能影响市场风险偏好并冲击加密资产。")


LFS = [lf_01, lf_02, lf_03, lf_04, lf_05, lf_06,
       lf_07, lf_08, lf_09, lf_10, lf_11, lf_12]


def predict(text):
    results = []
    for lf in LFS:
        result = lf(text)
        if result is not None:
            results.append(result)
    if not results:
        return "low", [], "未命中风险 LF"
    categories = list(dict.fromkeys(r[0] for r in results))
    level = max((r[1] for r in results), key=LEVEL_ORDER.get)
    reason = "；".join(r[2] for r in results)
    return level, categories, reason


def label_csv(input_csv, output_csv):
    data = pd.read_csv(input_csv, encoding="utf-8-sig")
    predictions = data["内容"].apply(predict)
    data["风险等级"] = predictions.apply(lambda v: v[0])
    data["风险类别"] = predictions.apply(
        lambda v: json.dumps(v[1], ensure_ascii=False)
    )
    data["原因"] = predictions.apply(lambda v: v[2])
    data[["新闻id", "时间", "内容", "风险等级", "风险类别", "原因"]].to_csv(
        output_csv, index=False, encoding="utf-8-sig"
    )
