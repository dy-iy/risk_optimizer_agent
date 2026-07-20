import re
import math
import pandas as pd
import os
CSV_PATH = os.environ.get("CSV_PATH", r"./data/input/raw_1000_news.csv")
OUT_PATH = os.environ.get("OUT_PATH", r"./reports/predictions/risk_labeler_v1_output.csv")

df = pd.read_csv(CSV_PATH)

# 优先拼接标题+内容；如果没有标题列，就只用内容
if "标题" in df.columns:
    df["标题"] = df["标题"].fillna("").astype(str)
else:
    df["标题"] = ""

df["内容"] = df["内容"].fillna("").astype(str)
df["text"] = (df["标题"] + " " + df["内容"]).str.strip()

# ---------------- 基础抽取 ----------------
PCT_RE = re.compile(r'([+-]?\d+(?:\.\d+)?)\s*%')
AMT_RE = re.compile(
    r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*'
    r'(亿美元|千万美元|百万美元|万美元|美元|USDT|USDC)',
    re.IGNORECASE
)

TIME_HINTS = ["分钟", "小时", "日内", "短时间", "瞬间", "盘中", "24小时", "24h", "今晨", "今日", "当天"]

# ---------------- 全局排除 / 缓和语义 ----------------
NEG_FRAUD = ["反欺诈", "防欺诈", "反诈骗宣传", "欺诈检测", "反洗钱", "AML", "合规体系", "风控系统"]
NEG_REG_ONLY_TALK = ["呼吁", "建议", "敦促", "讨论", "区分", "澄清", "明确性", "利好", "合规", "推进", "规范", "监管明确", "积极政策",
                     "判例", "胜诉", "有利", "正面", "问卷调查", "开发者调查", "散户调查",
                     "解除禁令", "撤销禁令", "不涉及监管", "驳回", "和解"]
NEG_TICKER_COLLISION = ["BTC原油", "原油出口", "杰伊汉港", "阿塞拜疆"]

NEG_NO_RISK = [
    "不涉及安全事件", "未涉及安全事件", "无安全事件",
    "不涉及资产异常", "未涉及资产异常",
    "不涉及资产安全", "未涉及资产安全",
    "不影响用户资产", "用户资金未受影响", "未造成资金损失",
    "无资金损失", "未造成损失", "已修复", "完成修复",
    "已恢复", "恢复正常", "误报", "并非攻击", "并非被盗",
    "不存在被盗风险", "并不存在被盗风险", "并非漏洞",
    "并非安全事故", "非安全事故", "持仓", "未平仓", "浮盈", "止盈"
]

NEG_PLANNED_MAINT = [
    "例行维护", "计划内维护", "系统升级", "例行升级",
    "例行系统升级", "按计划推进", "常规维护"
]

NEG_INTERNAL_TRANSFER = [
    "内部调拨", "内部转移", "冷钱包内部调拨", "钱包归集",
    "资金归集", "地址归集", "热钱包迁移", "冷钱包迁移",
    "官方钱包迁移"
]

HEALED_REPORT = ["已修复", "完成修复", "追回", "追讨", "已经恢复",
                 "已解决", "已完成补丁", "修复程序", "补丁发布"]

NEG_HACK_DISCUSSION = ["安全研究", "漏洞赏金", "白帽", "道德黑客", "修复测试", "安全演练", "防护", "防御", "没有发现漏洞", "并非漏洞",
                        "推迟上线", "加强安全", "推出解决方案", "安全增强", "安全措施"]
PHYSICAL_ATTACK = ["扳手攻击", "物理攻击", "暴力", "抢劫", "武装"]

VOL_PREDICTION_WORDS = ["预测", "可能", "若", "如果", "预期", "分析", "报道", "历史"]  # 移除"讨论","回顾"
VOL_REALTIME_WORDS = ["突发", "刚刚", "正在", "瞬间", "盘中"]

NEG_MACRO_DIRECTION = ["解除", "取消", "终止", "放宽", "放宽限制"]

PASSIVE_LEGAL = ["裁定", "判决", "不被视为", "驳回", "和解", "澄清", "拟"]

LEGAL_POSITIVE = ["不属于证券", "不构成利益冲突", "不予追究", "驳回起诉", "有利裁决", "不构成欺诈", "无罪", "被判无责任", "不构成"]

DRAFT_PROPOSAL_WORDS = ["草案", "征求意见", "提议", "提案", "拟"]

USD_UNIT_MULT = {
    "美元": 1.0,
    "USDT": 1.0,
    "USDC": 1.0,
    "万美元": 1e4,
    "百万美元": 1e6,
    "千万美元": 1e7,
    "亿美元": 1e8,
}


def has_any(text, kws):
    t = (text or "")
    return any(k in t for k in kws)


def extract_max_pct(text):
    mx = 0.0
    for m in PCT_RE.finditer(text or ""):
        try:
            mx = max(mx, abs(float(m.group(1))))
        except Exception:
            pass
    return mx


def extract_usd_equiv(text):
    usd = 0.0
    for m in AMT_RE.finditer(text or ""):
        raw, unit = m.group(1), m.group(2)
        raw = raw.replace(",", "")
        try:
            val = float(raw)
        except Exception:
            continue
        u = unit.upper()
        usd += val * USD_UNIT_MULT.get(u, 0.0)
    return usd


def clip01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else float(x))


def smooth_strength(x, x0, scale):
    if x <= x0:
        return 0.0
    return 1.0 - math.exp(-(x - x0) / scale)


# ---------------- 风险打分 ----------------

# 1) 合约/链上漏洞攻击
KW_HACK = ["漏洞", "攻击", "被盗", "盗取", "重入", "闪电贷", "利用漏洞", "黑客", "exploit", "hacker", "入侵",
           "铸造并抛售", "无故增发", "未授权铸造", "被利用", "窃取", "盗走"]  # 移除"安全事件"
WEAK_HACK_SIGNALS = ["安全事件", "事件"]  # 弱信号，单独处理
NEW_HACK_STRONG = ["零日", "新漏洞", "正在攻击", "未修复"]
NEW_HACK_WEAK = ["紧急", "最新攻击"]

def score_hack(text):
    usd = extract_usd_equiv(text)
    has_heal = has_any(text, HEALED_REPORT)
    has_new_strong = has_any(text, NEW_HACK_STRONG)
    has_new_weak = has_any(text, NEW_HACK_WEAK)
    has_kw_hack = has_any(text, KW_HACK)
    has_weak_signal = has_any(text, WEAK_HACK_SIGNALS)

    # 否定上下文：安全讨论，物理攻击
    if has_any(text, PHYSICAL_ATTACK):
        return 0.0
    if has_any(text, NEG_HACK_DISCUSSION):
        # 仅当无新攻击词且无大额损失时直接归零
        if not has_new_strong and usd < 100_000:
            return 0.05

    if has_heal:
        if has_new_strong:
            # 强新攻击词覆盖 heal 的抑制，继续往下打分
            pass
        elif has_new_weak:
            # 仅有弱新词且已修复
            if usd > 10_000_000:
                return 0.65
            elif usd > 1_000_000:
                return 0.40
            else:
                return 0.20
        else:
            # 已修复且无任何新攻击词
            if usd > 10_000_000:
                return 0.75
            elif usd > 1_000_000:
                return 0.55
            else:
                return 0.20

    if has_any(text, NEG_NO_RISK):
        if has_kw_hack:
            return 0.20
    if has_kw_hack:
        base = 0.80 + 0.08 * smooth_strength(usd, 50_000, 200_000)
        # 若仅匹配到弱新词
        if has_new_weak:
            base += 0.05
        return clip01(base)
    # 仅有弱触发词如"被利用""窃取"等
    if has_any(text, ["被利用", "窃取", "盗走"]):
        if usd > 100_000:
            return 0.25
        return 0.10
    # 只命中"安全事件"等极其弱的信号
    if has_weak_signal and not has_kw_hack:
        if usd > 1_000_000:
            return 0.30
        return 0.10
    return 0.0


# 2) 诈骗/跑路/rug
KW_FRAUD_STRONG = ["诈骗", "骗局", "庞氏", "传销", "跑路", "rug", "rugpull", "钓鱼", "假冒", "冒充", "卷款"]
KW_FRAUD_WEAK = ["集中度", "控盘", "庄家", "团队钱包持有", "老鼠仓", "高控盘", "发行方控制大量供应",
                 "持仓高度集中", "代币分配", "供应量集中", "疑似内部"]

def score_fraud(text):
    if has_any(text, NEG_FRAUD):
        return 0.0
    if has_any(text, KW_FRAUD_STRONG):
        return 0.88
    if has_any(text, KW_FRAUD_WEAK):
        return 0.60
    return 0.0


# 3) 监管/法律风险
REG_ACTORS = ["SEC", "CFTC", "司法部", "检察", "法院", "监管", "执法", "警察", "法官", "审计", "调查机构", "税务"]
HARD_ACTIONS = ["起诉", "指控", "罚款", "制裁", "逮捕", "拘留", "认罪", "判决", "冻结", "查封", "诉讼", "传唤"]
SOFT_ACTIONS = ["警告", "法案", "立法", "监管关注", "调查通知"]
INVESTIGATION_WORDS = ["调查"]
ENFORCEMENT_ENTITIES = ["SEC", "CFTC", "司法部", "警察", "执法", "公安", "检察"]
ACTIVE_PUNISH = ["罚款", "逮捕", "起诉", "制裁", "拘留", "认罪"]  # 移除"指控"

def score_regulatory(text):
    neg_talk_strong = has_any(text, NEG_REG_ONLY_TALK)
    draft_proposal = has_any(text, DRAFT_PROPOSAL_WORDS)
    hard = has_any(text, HARD_ACTIONS)
    soft = has_any(text, SOFT_ACTIONS)
    actor = has_any(text, REG_ACTORS)
    inv = has_any(text, INVESTIGATION_WORDS)
    enforce = has_any(text, ENFORCEMENT_ENTITIES)
    passive = has_any(text, PASSIVE_LEGAL)
    active_pun = has_any(text, ACTIVE_PUNISH)
    legal_positive = has_any(text, LEGAL_POSITIVE)

    # 有利司法结果大幅降分
    if legal_positive and not active_pun:
        return 0.05

    # 如果含有草案/提议等词，给予中等分数而不归零
    if draft_proposal:
        if hard:
            base = 0.50
        elif soft and actor:
            base = 0.40
        elif actor:
            base = 0.30
        else:
            base = 0.20
        if passive:
            base *= 0.8
        return clip01(base)

    # 强否定词处理
    if neg_talk_strong:
        if hard:
            # 同时有讨论和强硬动作，给予中低分
            base = 0.35
        elif soft:
            base = 0.15 if actor else 0.0
        elif actor:
            base = 0.10
        else:
            base = 0.0
        if passive:
            base *= 0.5
        return clip01(base)

    if actor and hard:
        # 检查冻结是否是唯一硬动作，且无主动惩罚词
        if "冻结" in text and not active_pun:
            score = 0.45
        else:
            score = 0.80
        if passive:
            score *= 0.65
        return clip01(score)
    if actor and inv:
        if enforce:
            return 0.60
        else:
            return 0.25
    if actor and soft:
        return 0.45
    if actor:
        return 0.25
    return 0.0


# 4) 交易所/链/钱包运维风险
KW_OUTAGE = ["暂停提现", "暂停充提", "暂停充值", "提现暂停", "充值暂停", "维护", "钱包维护", "宕机", "系统故障", "无法交易", "停止充提", "网络拥堵"]
def score_outage(text):
    if not has_any(text, KW_OUTAGE):
        return 0.0

    if has_any(text, NEG_PLANNED_MAINT) or has_any(text, NEG_NO_RISK):
        if "暂停提现" in text or "停止充提" in text or "暂停充提" in text:
            return 0.35
        return 0.20

    if has_any(text, ["暂停提现", "暂停充提", "停止充提", "提现暂停", "充值暂停"]):
        return 0.72

    if has_any(text, ["宕机", "系统故障", "无法交易"]):
        return 0.68

    return 0.55


# 5) 稳定币异常
STABLES = ["USDT", "USDC", "DAI", "FDUSD", "TUSD", "UST", "USDD", "FRAX", "PYUSD", "稳定币"]
STABLE_HINTS = ["脱锚", "锚定", "锚离", "peg", "depeg", "跌破1美元", "跌破 1 美元", "回到1美元"]
STABLE_NUM = re.compile(r'\b0\.9\d\b|\b1\.0\d\b')
def score_stablecoin(text):
    if not has_any(text, STABLES):
        return 0.0
    if has_any(text, STABLE_HINTS) or STABLE_NUM.search(text or ""):
        return 0.85
    return 0.0


# 6) 清算/爆仓
KW_LIQ_CORE = ["清算", "liquidation"]
KW_LIQ_STRONG = ["爆仓", "强平"]
KW_LIQ_EXTENDED = ["永续", "合约", "杠杆", "保证金"]
CONDITIONAL_WORDS = ["若", "如果", "可能触发", "可能清算", "可能爆仓", "假设", "可触发"]
EVENT_CONFIRM = ["实际清算", "大量爆仓", "大规模", "多头爆仓", "实际爆仓", "多头清算", "空头清算", "持仓清算", "大额爆仓", "大规模清算"]
NEG_LIQ = ["合约地址", "智能合约", "智能合约内", "合约调用", "分析", "预警", "接近", "不可能"]  # 移除"可能"
MARKET_NOISE = ["交易量", "清算量", "统计", "日均", "费用", "基金", "代币化"]
def score_liquidation(text):
    # 否定排除
    if has_any(text, NEG_LIQ):
        # 但是如果有强确认词或强动作词，仍然可能是真实事件，保留中等分数
        if has_any(text, EVENT_CONFIRM) or has_any(text, KW_LIQ_STRONG):
            usd = extract_usd_equiv(text)
            base = 0.35 + 0.20 * smooth_strength(usd, 500_000, 2_000_000) if usd > 0 else 0.35
            return clip01(base * 0.65)
        return 0.0

    # 强确认词：爆仓/强平、事件确认
    if has_any(text, KW_LIQ_STRONG) or has_any(text, EVENT_CONFIRM):
        usd = extract_usd_equiv(text)
        base = 0.45 + 0.25 * smooth_strength(usd, 500_000, 5_000_000) if usd > 0 else 0.45
        if has_any(text, CONDITIONAL_WORDS):
            base *= 0.7
        if has_any(text, MARKET_NOISE):
            base *= 0.6
        return clip01(base)

    if has_any(text, KW_LIQ_CORE):
        has_strong = has_any(text, KW_LIQ_STRONG) or has_any(text, EVENT_CONFIRM)
        if has_strong:
            usd = extract_usd_equiv(text)
            base = 0.45 + 0.25 * smooth_strength(usd, 500_000, 5_000_000) if usd > 0 else 0.45
        else:
            if has_any(text, CONDITIONAL_WORDS) and not has_any(text, EVENT_CONFIRM):
                base = 0.25
            else:
                base = 0.35
        if has_any(text, MARKET_NOISE):
            base *= 0.6
        return clip01(base)
    if has_any(text, KW_LIQ_EXTENDED):
        return 0.20
    return 0.0


# 7) 大额转账/巨鲸
KW_WHALE = [
    "巨鲸", "whale", "转入", "转出", "转入交易所", "转出交易所", "从匿名地址", "从未知地址",
    "大户", "鲸鱼地址", "大额持有者", "巨量持有", "大单转账",
    "大额转移", "大量转入", "大量转出", "大额流入", "大额流出",
    "巨鲸地址", "鲸鱼钱包", "大额异动", "链上转移", "链上大额", "鲸鱼动向",
    "平仓", "止盈", "出售", "转移", "撤出", "转存", "存入", "转至", "大额出售", "大量抛售", "将...存入"
]
WHALE_ACTION = [
    "转入", "转出", "转移", "从未知地址", "未知地址", "大额异动", "链上大额",
    "大额流出", "大额流入", "大额转入", "大额转出", "链上转移",
    "平仓", "止盈", "出售", "撤出", "转存", "存入", "转至"
]

def score_whale(text):
    usd = extract_usd_equiv(text)
    has_kw_whale = has_any(text, KW_WHALE)
    has_action = has_any(text, WHALE_ACTION)

    # 若完全无关键词且金额未达到大额，不触发
    if not has_kw_whale and (usd < 2_000_000 or not has_action):
        return 0.0

    if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_NO_RISK):
        if usd > 0:
            return 0.15
        return 0.05

    # 基础触发分
    if not has_kw_whale:
        # 仅靠大额+动作触发
        base = 0.25
    elif not has_action:
        # 有巨鲸词无动作
        if usd > 10_000_000:
            base = 0.25
        else:
            base = 0.15
    else:
        # 有鲸鱼词且有动作
        if usd <= 0:
            base = 0.35
        else:
            base = 0.35 + 0.40 * smooth_strength(usd, 200_000, 1_500_000)
    return clip01(base)


# 8) 行情异常波动
KW_SHOCK = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "暴涨", "剧烈波动", "瞬间暴跌", "瞬间拉升"]
NEGATIVE_PRICE_MOVE = ["暴跌", "暴泻", "崩盘", "闪崩", "插针", "急跌", "重挫", "跳水", "抛售",
                       "下跌", "跌幅", "下挫", "跌超", "跌幅超过", "大幅下跌"]

def score_volatility(text):
    pct = extract_max_pct(text)
    has_time = has_any(text, TIME_HINTS)
    has_softener = has_any(text, NEG_NO_RISK)
    is_analysis = has_any(text, VOL_PREDICTION_WORDS)
    is_realtime = has_any(text, VOL_REALTIME_WORDS)

    # 增加硬性要求：必须出现具体的代币名或百分比
    if pct < 5 and not has_any(text, KW_SHOCK):
        return 0.0
    if not has_any(text, NEGATIVE_PRICE_MOVE) and not has_any(text, KW_SHOCK):
        return 0.0

    if has_any(text, KW_SHOCK):
        base = 0.50 if has_softener else 0.60
        if has_time:
            base += 0.05
        score = clip01(base + 0.25 * smooth_strength(pct, 10, 20))
        # 如果是分析文本且没有实时词，大幅降权
        if is_analysis and not is_realtime:
            score *= 0.4
        return clip01(score)

    if pct < 5:
        return 0.0

    if not has_any(text, NEGATIVE_PRICE_MOVE):
        return 0.0

    base = 0.30 + (0.05 if has_time else 0.0)
    score = clip01(base + 0.40 * smooth_strength(pct, 5, 20))
    if is_analysis and not is_realtime:
        score *= 0.4
    return clip01(score)


# 9) 项目治理 / 团队异常风险
KW_TEAM = [
    "创始人失联", "团队失联", "删除社交媒体账号", "删除账号", "官网无法访问",
    "官网无法打开", "停止运营", "停更", "项目方失联", "团队突然解散",
    "官方失联", "核心成员离职", "多签异常", "团队辞职", "核心开发者离开",
    "项目方出货", "团队砸盘", "创始人抛售", "核心成员套现",
    "项目方清仓", "团队减持", "团队抛售", "项目方出售", "创始人抛售",
    "核心成员抛售", "项目方抛售"
]
def score_team(text):
    if has_any(text, KW_TEAM):
        return 0.82
    return 0.0


# 10) 偿付能力 / 储备 / 流动性风险
KW_SOLV = [
    "偿付能力", "兑付", "挤兑", "储备不足", "储备透明度不足", "流动性危机",
    "流动性不足", "资不抵债", "现金流压力", "负债", "财务困境", "无法兑付",
    "冷钱包无法访问", "私钥未移交", "私钥丢失", "无法访问", "资产冻结",
    "利用率100%", "无法提款", "坏账风险", "坏账", "兑付恐慌", "储备缺口",
    "流动性干涸", "资金池枯竭", "紧急停提", "兑付危机",
    "TVL骤降", "TVL下降", "资金缺口", "偿付缺口", "挤兑风险",
    "流动性枯竭", "坏账率攀升", "资金池见底", "流动性骤降",
    "流动性压力", "削减"
]
STRONG_SOLV = ["挤兑", "资不抵债", "储备金亏空", "崩盘", "无法兑付", "偿付缺口", "流动性枯竭", "坏账风险", "坏账率攀升"]

def score_solvency(text):
    if not has_any(text, KW_SOLV):
        return 0.0

    has_strong = has_any(text, STRONG_SOLV)
    is_discussion = has_any(text, ["央行", "研究人员", "报告", "评估", "讨论"])

    if has_any(text, STABLES) or has_any(text, ["发行方", "储备资产"]):
        if has_strong:
            base = 0.88
        else:
            base = 0.60
    else:
        if has_strong:
            base = 0.78
        else:
            base = 0.45

    if is_discussion and not has_strong:
        base *= 0.5

    return clip01(base)


# 11) 基础设施 / 协议层异常风险
KW_INFRA = [
    "跨链桥异常", "跨链桥故障", "预言机异常", "预言机失灵", "停止出块",
    "分叉异常", "共识失败", "RPC故障", "节点故障", "主网故障", "网络停止",
    "出块暂停", "节点大面积故障", "出块停滞", "主网阻塞", "协议挂起", "RPC大面积超时", "链上暂停",
    "协议暂停", "网络拥塞", "共识故障", "出块延迟"
]
def score_infra(text):
    if has_any(text, KW_INFRA):
        if has_any(text, NEG_NO_RISK):
            return 0.25
        return 0.75
    return 0.0


# 12) 宏观 / 政策冲击风险
KW_MACRO = [
    "美元走强", "美元指数走强", "油价飙升", "美债收益率走高", "加息预期",
    "风险偏好下降", "避险情绪升温", "宏观利空", "政策收紧", "禁令",
    "战争", "军事冲突", "地缘政治", "利率决议", "央行加息", "央行降息",
    "国债危机", "金融市场恐慌"
]
def score_macro(text):
    if has_any(text, KW_MACRO):
        # 检查是否解除禁令等正向
        if "禁令" in text and has_any(text, NEG_MACRO_DIRECTION):
            return 0.0
        pct = extract_max_pct(text)
        base = 0.35
        return clip01(base + 0.20 * smooth_strength(pct, 3, 10))
    return 0.0


# ---------------- 风险配置 ----------------
RISK_SCORERS = {
    "score_hack": score_hack,
    "score_fraud": score_fraud,
    "score_regulatory": score_regulatory,
    "score_outage": score_outage,
    "score_stablecoin": score_stablecoin,
    "score_liquidation": score_liquidation,
    "score_whale": score_whale,
    "score_volatility": score_volatility,
    "score_team": score_team,
    "score_solvency": score_solvency,
    "score_infra": score_infra,
    "score_macro": score_macro,
}

RISK_NAME_MAP = {
    "score_hack": "链上漏洞 / 攻击风险",
    "score_fraud": "诈骗 / 跑路 / Rug Pull 风险",
    "score_regulatory": "监管与法律风险",
    "score_outage": "交易所与系统运维风险",
    "score_stablecoin": "稳定币异常风险",
    "score_liquidation": "爆仓 / 清算风险",
    "score_whale": "大额转账 / 巨鲸行为风险",
    "score_volatility": "异常行情波动风险",
    "score_team": "项目治理 / 团队异常风险",
    "score_solvency": "偿付能力 / 储备 / 流动性风险",
    "score_infra": "基础设施 / 协议层异常风险",
    "score_macro": "宏观 / 政策冲击风险",
}

# 命中阈值：用于 rule_types
TYPE_THRESHOLD = 0.30

# 主类别 close competition 优先级（数值越高越优先）
PRIMARY_PRIORITY = {
    "监管与法律风险": 10,
    "偿付能力 / 储备 / 流动性风险": 8,
    "链上漏洞 / 攻击风险": 7,
    "项目治理 / 团队异常风险": 6,
    "诈骗 / 跑路 / Rug Pull 风险": 5,
    "交易所与系统运维风险": 4,
    "稳定币异常风险": 3,
    "基础设施 / 协议层异常风险": 2,
    "爆仓 / 清算风险": 1,
    "异常行情波动风险": 1,
    "大额转账 / 巨鲸行为风险": 1,
    "宏观 / 政策冲击风险": 0,
}


def score_to_label(score_01: float) -> str:
    if score_01 >= 0.70:
        return "high"
    if score_01 >= 0.40:
        return "medium"
    return "low"


def score_all_risks(text: str) -> dict:
    if has_any(text, NEG_TICKER_COLLISION):
        zero_scores = {k: 0.0 for k in RISK_SCORERS.keys()}
        zero_scores["risk"] = 0
        zero_scores["rule_label"] = "low"
        zero_scores["rule_types"] = ""
        zero_scores["rule_primary_type"] = "无明显风险"
        return zero_scores

    raw_scores = {name: fn(text) for name, fn in RISK_SCORERS.items()}
    sorted_scores = sorted(raw_scores.items(), key=lambda kv: kv[1], reverse=True)
    max_score_name = sorted_scores[0][0]
    max_score_01 = sorted_scores[0][1]
    second_score_01 = sorted_scores[1][1] if len(sorted_scores) > 1 else 0.0

    # 命中的所有类型
    hit_types = []
    for score_name, score_val in raw_scores.items():
        if score_val >= TYPE_THRESHOLD:
            hit_types.append(RISK_NAME_MAP[score_name])

    # 输出分数字段
    result = {name: round(val, 4) for name, val in raw_scores.items()}

    # 多数 scorer 触发降权因子
    active_scorer_count = sum(1 for v in raw_scores.values() if v > 0.0)
    if active_scorer_count >= 3 or max_score_01 >= 0.80:
        factor = 1.0
    elif active_scorer_count == 2:
        factor = 0.85
    else:
        factor = 0.70

    risk_100 = int(round(max_score_01 * factor * 100))

    # 确定主类别
    if max_score_01 < 0.35:
        if not (max_score_01 >= 0.30 and extract_usd_equiv(text) > 1_000_000 and "attack" in max_score_name.lower()):
            primary_type_name = "无明显风险"
        else:
            primary_type_name = RISK_NAME_MAP[max_score_name]
    elif max_score_01 >= 0.25:
        primary_type_name = RISK_NAME_MAP[max_score_name]
        if (max_score_01 - second_score_01) < 0.05:
            second_name = sorted_scores[1][0]
            second_type = RISK_NAME_MAP[second_name]
            if PRIMARY_PRIORITY.get(second_type, 0) > PRIMARY_PRIORITY.get(primary_type_name, 0):
                primary_type_name = second_type
    else:
        primary_type_name = "无明显风险"

    result["risk"] = risk_100
    result["rule_label"] = score_to_label(max_score_01)
    result["rule_types"] = "|".join(hit_types)
    result["rule_primary_type"] = primary_type_name

    return result


# 计算全部输出
score_df = df["text"].apply(lambda x: pd.Series(score_all_risks(x)))
df_out = pd.concat([df, score_df], axis=1)

# 输出字段
base_cols = [c for c in ["新闻id", "时间", "标题", "内容", "链接"] if c in df_out.columns]
score_cols = list(RISK_SCORERS.keys()) + ["risk", "rule_label", "rule_types", "rule_primary_type"]

out_df = df_out[base_cols + score_cols]
out_df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

print("Saved:", OUT_PATH)
print("\n总分分布：")
print(out_df["risk"].describe())

print("\n风险等级分布：")
print(out_df["rule_label"].value_counts(dropna=False))

print("\n主风险类别分布：")
print(out_df["rule_primary_type"].value_counts(dropna=False).head(20))
