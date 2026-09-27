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


def negation_present(text, target_words):
    """检测文本中是否在目标词周围出现否定词"""
    # 简单实现：若 text 中出现 '未发现'、'没有'、'否认' 等否定短语，且后文包含 target 中的词
    neg_phrases = ["未发现", "没有", "否认", "并非", "不存在", "无"]
    if not any(phrase in text for phrase in neg_phrases):
        return False
    # 若存在否定词，检查是否与目标词紧邻或有逻辑关系
    # 简化：若存在任意否定词，即认为可能否定相关风险（后续在 LF 中根据场景处理）
    return True


# ---------- Label Functions ----------
def lf_01(text):
    high_phrases = [
        "黑客攻击", "漏洞攻击", "遭攻击", "攻击事件", "攻击损失",
        "被攻击", "安全漏洞", "协议漏洞", "漏洞被利用", "遭黑客",
        "资金被盗", "攻击者利用"
    ]
    if hit(text, high_phrases):
        level = "high" if not softener_present(text) else "medium"
        reason = (
            "新闻涉及加密货币项目遭黑客攻击或重大安全漏洞，直接导致资金损失或市场影响，属于高风险事件。"
            if level == "high"
            else "新闻提及安全攻击或漏洞，但存在条件性或历史性描述，潜在风险。"
        )
        return ("链上漏洞 / 攻击风险", level, reason)
    if hit(text, ["黑客", "漏洞", "攻击", "exploit", "hack"]) and hit(text, ["损失", "被盗", "盗取", "影响", "风险"]):
        level = "high" if not softener_present(text) else "medium"
        reason = (
            "新闻提及安全攻击或漏洞，并可能造成资金损失或市场影响，构成链上漏洞风险。"
            if level == "high"
            else "新闻提及安全攻击或漏洞，但语气较弱，潜在风险。"
        )
        return ("链上漏洞 / 攻击风险", level, reason)
    if hit(text, ["漏洞", "安全漏洞", "协议漏洞", "智能合约漏洞"]) and not hit(text, ["攻击", "黑客", "损失", "盗"]):
        level = "low" if softener_present(text) else "medium"
        reason = "新闻提及协议或智能合约漏洞，但未提及实际攻击或损失，存在潜在风险。"
        return ("链上漏洞 / 攻击风险", level, reason)
    return None


def lf_02(text):
    # 诈骗 / 跑路 / Rug Pull 风险
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

    # 排除与黑客攻击重叠（避免与 lf_01 重复）
    if hit(text, ["黑客攻击", "漏洞攻击", "安全漏洞"]):
        return None

    if hit(text, high_terms):
        level = "high"
        reason = "文本包含明确已发生的诈骗、跑路或Rug Pull事件关键词，涉及资产损失、指控或已确认的欺诈行为，构成高风险。"
    elif hit(text, medium_terms):
        # 若出现否定语境，降为 low 或不触发
        if negation_present(text, medium_terms):
            level = "low"
            reason = "文本提及可疑活动或诈骗警告，但存在否定语境，实际风险较低。"
        else:
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
    # 高风险词：已发生的具体法律行动
    high_strict = ["被捕", "逮捕", "监禁", "被判", "盗取", "窃取"]
    # 草案/未来语境词
    future_or_draft = ["草案", "拟", "提案", "尚未", "拟议", "将"]

    # 先检查高风险词
    for w in high_strict:
        if w.lower() in text_lower:
            # 若存在未来/草案语境，降低等级
            if hit(text, future_or_draft):
                level = "medium"
                reason = f"文本包含高风险法律关键词：{w}，但处于草案或未生效状态，风险中等。"
            else:
                level = "high"
                reason = f"文本包含高风险法律关键词：{w}，涉及刑事、欺诈或重大法律行动，风险等级为高。"
            return ("监管与法律风险", level, reason)

    # 可能需要结合上下文的高风险话题词
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

    # 中等风险词组
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
    stable_words = ["稳定币", "USDT", "Tether", "USDC", "DAI", "UST",
                    "BUSD", "FDUSD", "USD Coin", "Dai"]
    high_words = ["脱锚", "挤兑", "黑客攻击", "漏洞", "停止赎回",
                  "冻结", "崩盘", "归零", "暴跌", "攻击事件", "安全漏洞"]

    if not hit(text, stable_words):
        return None

    if hit(text, high_words):
        if softener_present(text):
            return ("稳定币异常风险", "low", "新闻提及稳定币潜在风险或警告，可能存在脱锚等风险。")
        else:
            return ("稳定币异常风险", "high", "新闻明确报道稳定币相关异常事件，如脱锚、攻击等。")
    return None


def lf_06(text):
    # 爆仓 / 清算风险
    liquidation_words = ["爆仓", "强制平仓", "清算", "空单挤压", "强平", "爆仓潮"]
    context_words = ["杠杆", "头寸", "爆仓", "仓位", "保证金", "抵押", "借贷"]

    if not hit(text, liquidation_words):
        return None

    # 排除传统金融清算语境
    if hit(text, ["对账", "银行清算", "贸易结算", "结算系统"]) and not hit(text, ["杠杆", "头寸", "爆仓", "保证金"]):
        return None

    # 否定语境检测
    if negation_present(text, liquidation_words):
        return None

    if softener_present(text):
        level = "low"
        reason = "文本提及可能的爆仓或清算，但带有条件或软化语气，实际发生概率较低。"
    else:
        if hit(text, ["亿美元", "百万美元", "利用率达100%", "无法提取",
                       "坏账风险", "大规模爆仓", "大规模清算", "部分清算", "全部清算"]):
            level = "high"
            reason = "检测到严重爆仓/清算事件，涉及大额资金或系统性风险。"
        else:
            level = "medium"
            reason = "检测到爆仓/清算相关关键词，存在杠杆头寸被清算风险。"
    return ("爆仓 / 清算风险", level, reason)


def lf_07(text):
    # 巨鲸行为检测
    if not ("0x" in text or "巨鲸" in text or "鲸鱼" in text or "大户" in text):
        return None
    if is_opinion_text(text):
        return None

    action_words = ["转账", "转移", "出售", "抛售", "开仓", "平仓", "买入", "卖出", "增持", "减持",
                    "空单", "多单", "建仓", "清仓", "移出", "转入（交易所）"]
    amount_words = ["万美元", "亿美元", "万枚", "亿枚", "巨量", "天量", "大额", "大量"]
    negative_actions = ["出售", "抛售", "减持", "清仓", "移出", "转入交易所"]

    if not hit(text, action_words):
        return None
    if not hit(text, amount_words):
        return None

    # 若涉及黑客、非法等高风险
    if hit(text, ["黑客", "非法", "盗取", "窃取"]):
        return ("大额转账 / 巨鲸行为风险", "high", "涉及非法或黑客相关的大额转账，可能严重冲击市场。")
    if hit(text, ["项目方", "创始人", "团队"]) and hit(text, ["出售", "抛售", "释放"]):
        return ("大额转账 / 巨鲸行为风险", "high", "项目方或团队出现大额抛售或代币释放行为，可能造成严重抛压。")

    # 一般巨鲸操作，若动作负面，则 medium；否则 low
    if hit(text, negative_actions):
        return ("大额转账 / 巨鲸行为风险", "medium", "新闻提到巨鲸地址的大额操作行为（如抛售/转账至交易所），可能引发市场波动。")
    else:
        return ("大额转账 / 巨鲸行为风险", "low", "新闻提到巨鲸地址的大额转账，未见明显负面意图，风险较低。")


def lf_08(text):
    if is_opinion_text(text):
        return None
    words = ["暴跌", "暴涨", "闪崩", "崩盘", "跳水", "雪崩", "砸盘",
             "断崖式下跌", "腰斩", "翻倍", "十倍", "百倍", "千倍",
             "万倍", "跌超", "涨超", "下挫", "急跌", "急涨", "飙升",
             "大跌", "大涨", "剧烈波动"]
    if hit(text, words):
        return ("异常行情波动风险", "high", "文本包含极端行情描述词，表明价格可能异常波动")
    return None


def lf_09(text):
    high_phrases = ["无法找回", "锁死", "无限期", "特权地址", "链下管理",
                    "跑路", "Rug Pull", "严重漏洞",
                    "无法继续处理", "漏洞仍未修复"]
    medium_phrases = ["停止运营", "终止服务", "团队控制", "集中度高",
                      "供应量集中", "高度集中", "退出", "关闭", "团队钱包",
                      "项目方", "抛售"]
    # 移除 "坏账风险"（归属 lf_10），并限制 "退出" 的误匹配
    if hit(text, high_phrases):
        return ("项目治理 / 团队异常风险", "high", "检测到团队异常高风险关键词（如资金锁定、无法找回、特权地址等）")
    elif hit(text, medium_phrases):
        # 若仅匹配到 "退出" 且无 "项目方"/"停止运营" 上下文，降为 low 或不触发
        if "退出" in text and not any(w in text for w in ["停止运营", "项目方", "团队", "终止"]):
            return None
        return ("项目治理 / 团队异常风险", "medium", "检测到团队异常中风险关键词（如停止运营、高集中度、抛售等）")
    return None


def lf_10(text):
    high_words = ["坏账风险", "无法提取资金", "无法提款", "无法赎回",
                  "利用率为100%", "利用率达100%", "利用率达到100%",
                  "流动性枯竭", "偿付能力危机", "资不抵债",
                  "大规模资金外流", "数十亿美元资金逃离", "资金外逃",
                  "挤兑", "暂停提现", "暂停提款", "暂停赎回",
                  "暂停存款", "储备不足", "清算失败", "清算困难",
                  "坏账", "资金池利用率100%", "大规模赎回", "大量赎回", "赎回潮",
                  "无法处理提款"]  # 新增
    medium_words = ["流动性紧张", "流动性压力", "流动性危机", "储备下降",
                    "偿付能力受质疑", "可能违约", "流动性问题",
                    "提款限制", "赎回限制", "提款延迟", "赎回延迟"]
    if hit(text, high_words):
        # 若存在软化词，降级为 medium
        if softener_present(text):
            return ("偿付能力 / 储备 / 流动性风险", "medium", "新闻提及流动性风险，但语气软化或尚未发生。")
        return ("偿付能力 / 储备 / 流动性风险", "high",
                "新闻明确提及严重的流动性或偿付能力风险，如资金无法提取、大规模撤资、资金池利用率过高或坏账问题。")
    elif hit(text, medium_words):
        # 若存在软化词，降级为 low
        if softener_present(text):
            return ("偿付能力 / 储备 / 流动性风险", "low", "新闻提及流动性压力迹象，但不确定性较高。")
        return ("偿付能力 / 储备 / 流动性风险", "medium",
                "新闻提及流动性或偿付能力方面的压力，如流动性紧张、提款限制等。")
    return None


def lf_11(text):
    words = ['暂停合约', '暂停服务', '合约攻击', '网关攻击', '桥漏洞',
             '漏洞', '利用漏洞', '协议攻击', '异常活动', '社会工程攻击',
             '劫持', '中断服务', '节点宕机', '网络拥堵', '验证节点',
             '提案超时', '出块停止', '高利用率', '资金外流']
    if not hit(text, words):
        return None
    matched = [w for w in words if w in text]
    if matched == ["漏洞"] and not hit(text, ["攻击", "利用", "盗取", "暂停合约", "资金外流"]):
        return ("基础设施 / 协议层异常风险", "low", "新闻提及漏洞，但未关联实际攻击或异常。")
    high_words = ['合约攻击', '桥漏洞', '未授权铸造', '暂停合约', '大规模铸造',
                  '资金外流', '高利用率', '盗取', '获利']
    medium_words = ['暂停服务', '劫持', '社会工程攻击', '异常活动']
    reason_str = '、'.join(matched[:3])
    if any(w in text for w in high_words):
        level = 'high'
    elif any(w in text for w in medium_words) or "漏洞" in matched:
        level = 'medium'
    else:
        level = 'low'
    return ('基础设施 / 协议层异常风险', level, f'新闻内容涉及{reason_str}')


def lf_12(text):
    macro_words = ["地缘政治", "战争", "核谈判", "制裁", "封锁", "霍尔木兹",
                   "石油危机", "能源供应链", "谈判失败", "地缘紧张",
                   "政策冲击", "利率决议", "关税", "贸易战", "全球供应链",
                   "美股期货", "全球市场波动", "利率决议预期", "亚太市场"]  # 扩展
    if not hit(text, macro_words):
        return None
    # 若存在软化词，降为低风险或忽略
    if softener_present(text):
        # 仅提及风险可能性，定为 low
        return ("宏观 / 政策冲击风险", "low", "文本提及宏观或政策冲击的潜在可能，未确认发生。")
    # 直接冲击或已发生的事件
    if hit(text, ["战争", "制裁", "封锁", "关税", "危机"]):
        return ("宏观 / 政策冲击风险", "high", "文本确认重大宏观/政策冲击事件已发生，可能强烈影响市场。")
    else:
        return ("宏观 / 政策冲击风险", "medium", "文本提及宏观或政策冲击事件，如地缘政治紧张、贸易冲突、能源危机等，可能影响市场风险偏好并冲击加密资产。")


LFS = [lf_01, lf_02, lf_03, lf_04, lf_05, lf_06,
       lf_07, lf_08, lf_09, lf_10, lf_11, lf_12]


def predict(text):
    is_opinion = is_opinion_text(text)
    results = []
    for lf in LFS:
        result = lf(text)
        if result is not None:
            # 若文本为观点型，对所有风险等级降级
            if is_opinion:
                category, level, reason = result
                new_level = level
                if level == "high":
                    new_level = "medium"
                elif level == "medium":
                    new_level = "low"
                # low 保持不变
                results.append((category, new_level, reason))
            else:
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
