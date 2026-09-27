import re
import math
import pandas as pd
import os
CSV_PATH = os.environ.get("CSV_PATH", r".\data\input\raw_1000_news.csv")
OUT_PATH = os.environ.get("OUT_PATH", r".\reports\predictions\risk_labeler_v1_output.csv")

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
NEG_REG_ONLY_TALK = ["呼吁", "建议", "提议", "敦促", "讨论", "区分", "澄清", "拟", "征求意见"]
NEG_TICKER_COLLISION = ["BTC原油", "原油出口", "杰伊汉港", "阿塞拜疆"]

NEG_NO_RISK = [
    "不涉及安全事件", "未涉及安全事件", "无安全事件",
    "不涉及资产异常", "未涉及资产异常",
    "不涉及资产安全", "未涉及资产安全",
    "不影响用户资产", "用户资金未受影响", "未造成资金损失",
    "无资金损失", "未造成损失", "已修复", "完成修复",
    "已恢复", "恢复正常", "误报", "并非攻击", "并非被盗",
    "不存在被盗风险", "并不存在被盗风险", "并非漏洞",
    "并非安全事故", "非安全事故"
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
KW_HACK = ["漏洞", "攻击", "被盗", "盗取", "重入", "闪电贷", "利用漏洞", "黑客", "exploit", "hacker", "入侵"]
def score_hack(text):
    NEG_HACK_CONTEXT = [
        "漏洞报告", "争议", "批评", "讨论", "研究", "ETH Rangers", "绑架", "入室抢劫",
        "防御工具", "抗", "测试...技术", "可能构成风险", "构成系统性风险", "医疗机构", "患者数据", "市场份额",
        "黑客松", "竞赛", "安全研究所", "财政部", "寻求访问", "评论", "预防性", "暂停...市场",
        "检测", "推出", "识别", "安全工具", "提升安全", "防御",
        "对比", "紧急控制", "Vercel", "基础设施", "警方", "内政部", "扳手攻击", "预防性暂停",
        "分析", "回顾", "地位不如", "未受影响", "监控", "披露", "提议"
    ]
    HARM_INDICATORS = ["损失", "被盗", "窃取", "获利", "受害者", "受影响"]
    if has_any(text, NEG_HACK_CONTEXT):
        if not has_any(text, HARM_INDICATORS):
            return 0.0
        else:
            return 0.2
    if has_any(text, NEG_NO_RISK):
        if has_any(text, KW_HACK):
            return 0.20
    INFRA_ATTACK_KW = ["跨链桥", "域名劫持", "DNS", "网关", "桥接", "OFT跨链桥", "域名注册商", "中继器"]
    if has_any(text, KW_HACK):
        usd = extract_usd_equiv(text)
        if usd <= 0:
            base_score = 0.70
        else:
            base_score = clip01(0.90 + 0.08 * smooth_strength(usd, 50_000, 200_000))
        if has_any(text, INFRA_ATTACK_KW):
            base_score = min(1.0, base_score * 1.2)
        return base_score
    return 0.0


# 2) 诈骗/跑路/rug
KW_FRAUD = ["诈骗", "骗局", "庞氏", "传销", "跑路", "rug", "rugpull", "钓鱼", "假冒", "冒充", "卷款"]
def score_fraud(text):
    if has_any(text, NEG_FRAUD):
        return 0.0
    if has_any(text, KW_FRAUD):
        return 0.88
    return 0.0


# 3) 监管/法律风险
REG_ACTORS = ["SEC", "CFTC", "司法部", "检察", "法院", "监管", "执法", "警察", "法官", "审计", "调查机构", "税务", "人民日报", "新华社", "警方", "检察院", "宪法法院"]
REG_ACTIONS = ["起诉", "指控", "调查", "罚款", "制裁", "传唤", "逮捕", "拘留", "认罪", "和解", "判决", "冻结", "查封", "禁令", "诉讼", "提案", "草案", "起草", "警告", "批评", "通知", "将出台", "批准", "要求", "呼吁", "宣布"]
def score_regulatory(text):
    NEG_REG_POSITIVE = ["不属于证券", "驳回", "撤销", "否认指控", "补贴计划", "警告...风险", "裁定不属于", "不被视为",
                        "规则修订", "试点", "解除禁令", "开发者调查", "演讲", "降低门槛",
                        "申请", "提交", "不采取行动函", "要求履行", "推动监管", "审核", "确认",
                        "受监管环境", "合规"]
    NEG_REG_WARNING_ONLY = ["参议员", "议员", "律所", "可能威胁", "可能构成风险", "失去特权", "讨论"]
    STRICT_REG_ACTIONS = ["起诉", "罚款", "执法", "刑事调查", "判处", "冻结", "禁令"]
    MILD_REG_ACTIONS = ["警告", "批评", "威胁金融稳定", "非法金融活动", "调查", "逮捕", "传讯", "搜查"]
    DRAFT_LEGISLATION_KW = ["提案", "草案", "法案", "监管提案", "征求意见", "起草"]
    OPINION_KW = ["CEO", "表示", "称", "指出", "认为", "评论", "警告称"]
    if has_any(text, OPINION_KW) and not has_any(text, STRICT_REG_ACTIONS) and not has_any(text, MILD_REG_ACTIONS):
        return 0.0
    if has_any(text, ["SEC"]) and has_any(text, NEG_REG_ONLY_TALK) and not has_any(text, STRICT_REG_ACTIONS):
        return 0.0
    if has_any(text, NEG_REG_WARNING_ONLY) and not has_any(text, STRICT_REG_ACTIONS):
        return 0.0
    if has_any(text, REG_ACTORS) and has_any(text, MILD_REG_ACTIONS):
        if has_any(text, NEG_REG_POSITIVE):
            return 0.0
        return 0.45
    if has_any(text, REG_ACTORS) and has_any(text, STRICT_REG_ACTIONS):
        if has_any(text, NEG_REG_POSITIVE):
            return 0.0
        return 0.80
    if has_any(text, REG_ACTORS) and has_any(text, DRAFT_LEGISLATION_KW):
        return 0.40
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
KW_LIQ = ["爆仓", "强平", "清算", "liquidation"]
def score_liquidation(text):
    NEG_HYPOTHETICAL = ["若突破", "一旦突破", "如果突破", "若跌至", "一旦跌至", "如果跌至", "若跌破", "一旦跌破",
                        "如果", "一旦", "若", "假如"]
    NEG_MITIGATION = ["降低清算风险", "避免清算", "防范清算", "减少清算", "防止爆仓"]
    NEG_LIQ_FINANCIAL = ["清算交易量", "基金清算", "清算了", "交易量清算", "流动性基金", "货币市场工具",
                        "资产清算", "国家托管", "未认领"]
    NEG_LIQ_MARKET_REPORT = ["清算强度", "Coinglass 数据", "永续", "保证金交易升级", "CoinGlass"]
    if has_any(text, NEG_LIQ_MARKET_REPORT) and not has_any(text, ["爆仓", "强平", "崩盘", "大规模清算"]):
        if not has_any(text, ["爆仓", "强平", "大规模清算"]) and extract_usd_equiv(text) < 50_000_000 and "亿美元" not in text:
            return 0.0
    if has_any(text, NEG_HYPOTHETICAL) and not has_any(text, TIME_HINTS):
        return 0.0
    if (has_any(text, NEG_MITIGATION) or has_any(text, NEG_LIQ_FINANCIAL)) and not has_any(text, ["爆仓", "强平"]):
        return 0.0
    if not has_any(text, KW_LIQ):
        return 0.0
    # 个人交易策略（开设杠杆、建仓）若未提及大规模市场清算或爆仓则抑制
    if has_any(text, ["开设", "建仓", "杠杆仓位", "多单", "空单"]) and not has_any(text, ["亿美元", "大规模清算", "市场清算", "全市场", "爆仓", "强平"]):
        return 0.0
    usd = extract_usd_equiv(text)
    if usd <= 0:
        return 0.55
    return clip01(0.55 + 0.30 * smooth_strength(usd, 500_000, 2_000_000))


# 7) 大额转账/巨鲸
KW_WHALE = ["巨鲸", "whale", "转入", "转出", "转入交易所", "转出交易所", "从匿名地址", "从未知地址", "抛售", "清仓", "出售", "减持", "大额转账", "鲸鱼抛售"]
def score_whale(text):
    EXTRA_WHALE_KW = ["鲸鱼", "未知钱包", "大额转移", "转移到交易所", "转入交易所",
                     "提取保证金", "紧急撤资", "远古巨鲸", "平仓", "大额转移",
                     "止盈", "平多", "平空", "大额平仓", "巨鲸短时平仓", "撤资"]
    if not has_any(text, KW_WHALE) and not has_any(text, EXTRA_WHALE_KW):
        return 0.0
    TRANSFER_ACTIONS = ["转出", "转入", "转移", "抛售", "出售", "减持", "清仓", "鲸鱼抛售", "提现", "汇款",
                        "转账", "转至", "存入", "取款", "汇出", "平仓", "止盈", "提现", "止盈"]
    NEG_WHALE_NORMAL_OPS = ["融资", "回购", "策略", "公开透明", "根据...计划",
                           "Saylor", "外汇市场", "稳定币市场", "论述", "分析",
                           "CoW Swap", "HyperLiquid 存入", "存入",
                           "Aave", "Compound", "Hex Trust", "Lido", "Maker"]
    NEG_WHALE_TRADING_STRATEGY = ["开多", "开空", "增持", "持仓", "撤单", "挂单",
                                 "错失收益", "交易者错失", "减仓", "平仓"]
    if has_any(text, NEG_WHALE_NORMAL_OPS):
        if not has_any(text, ["未知钱包", "Whale Alert"]):
            return 0.0
    if has_any(text, NEG_WHALE_TRADING_STRATEGY) and not has_any(text, ["攻击", "异常", "黑客", "被盗", "洗钱"]):
        return 0.0
    # 专家观点/预测且无具体转账动作，抑制
    if has_any(text, ["观点", "表示", "分析", "预测", "承诺", "计划"]) and not has_any(text, ["转移", "转账", "巨鲸", "转入", "转出"]) and not has_any(text, ["攻击", "异常", "黑客", "被盗", "洗钱"]):
        return 0.05
    if not has_any(text, TRANSFER_ACTIONS):
        return 0.05

    if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_NO_RISK):
        usd = extract_usd_equiv(text)
        if usd > 0:
            return 0.15
        return 0.05

    usd = extract_usd_equiv(text)
    if usd <= 0:
        return 0.25
    return clip01(0.35 + 0.40 * smooth_strength(usd, 200_000, 1_500_000))


# 8) 行情异常波动
KW_SHOCK = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "暴涨", "剧烈波动", "瞬间暴跌", "瞬间拉升", "大涨", "飙升", "跌幅", "未实现损失"]
def score_volatility(text):
    NEG_VOLATILITY_CONTEXT = [
        "绑架", "股东减持", "安全研究所", "分析报告", "入室抢劫",
        "网络攻击模拟", "内政部", "犯罪", "警方", "法律", "政策",
        "减持", "交易量占比", "市场份额", "赎回协议", "协议推出",
        "月度报告", "季度报告", "年度报告", "警方通报", "概率"
    ]
    if not has_any(text, KW_SHOCK):
        return 0.0
    if has_any(text, NEG_VOLATILITY_CONTEXT) and not has_any(text, KW_SHOCK):
        return 0.0

    def is_market_roundup(txt):
        MARKET_ROUNDUP_KW = [
            "板块", "代币大涨", "行情数据显示", "市值突破",
            "AI板块", "Meme币", "24小时涨幅超", "普涨",
            "板块上涨", "板块普遍", "多个代币", "主流币"
        ]
        return has_any(txt, MARKET_ROUNDUP_KW)

    ANALYST_KW = [
        "MACD金叉", "周线MACD", "专家观点", "交易员表示", "分析师指出",
        "股东减持", "交易量占比", "市场份额",
        "分析师预测", "分析师预计", "分析师表示", "CoinCodex", "分析文章"
    ]
    if has_any(text, ANALYST_KW) and not has_any(text, KW_SHOCK):
        return 0.0

    NEG_PCT_MARKET_SHARE = ["占有率", "市占率", "市场份额", "市值占比"]
    if has_any(text, NEG_PCT_MARKET_SHARE):
        return 0.0

    if is_market_roundup(text) and not has_any(text, KW_SHOCK):
        return 0.0

    BULL_MARKET_WORDS = ["大涨", "飙升", "涨幅超", "板块上涨", "创新高", "攀升", "普涨", "拉升"]
    pct = extract_max_pct(text)
    if has_any(text, BULL_MARKET_WORDS) and not has_any(text, KW_SHOCK) and pct < 20:
        return 0.0
    has_time = has_any(text, TIME_HINTS)
    has_softener = has_any(text, NEG_NO_RISK)

    if has_any(text, KW_SHOCK):
        base = 0.50 if has_softener else 0.60
        if has_time:
            base += 0.05
        return clip01(base + 0.25 * smooth_strength(pct, 10, 20))

    if pct < 8:
        return 0.0

    base = 0.30 + (0.05 if has_time else 0.0)
    return clip01(base + 0.40 * smooth_strength(pct, 8, 20))


# 9) 项目治理 / 团队异常风险
KW_TEAM = [
    "创始人失联", "团队失联", "删除社交媒体账号", "删除账号", "官网无法访问",
    "官网无法打开", "停止运营", "停更", "项目方失联", "团队突然解散",
    "官方失联", "核心成员离职", "多签异常", "项目方", "创始团队", "核心团队",
    "创始人", "高管", "团队套现", "代币解锁", "治理攻击", "治理提案",
    "投票", "开发者抛售", "内部人士", "CEO离职", "DAO投票争议"
]
def score_team(text):
    NEG_TEAM_NORMAL = [
        "解锁计划", "代币解锁", "即将解锁", "路线图", "创始人表示", "创始人认为", "表示",
        "硬分叉", "升级", "BIP", "EIP", "BEP", "治理提案", "性能优化",
        "投票", "会议", "演讲", "讨论", "宣布", "采访", "听证会",
        "专访", "访谈", "分享", "透露", "拒绝投资", "解锁", "周解锁"
    ]
    STRONG_RISK_TEAM = ["抛售", "跑路", "内斗", "套现", "失联", "解散", "异常", "攻击",
                        "停止运营", "关闭业务", "关门", "解散委员会", "裁减"]
    # 纯观点/评论且无强风险词，直接抑制
    if has_any(text, ["表示", "认为", "讨论", "评论"]) and not has_any(text, STRONG_RISK_TEAM):
        return 0.0
    if has_any(text, KW_TEAM):
        if has_any(text, NEG_TEAM_NORMAL) and not has_any(text, STRONG_RISK_TEAM):
            return 0.0
        return 0.82
    return 0.0


# 10) 偿付能力 / 储备 / 流动性风险
KW_SOLV = [
    "偿付能力", "兑付", "挤兑", "储备不足", "储备透明度不足", "流动性危机",
    "流动性不足", "资不抵债", "现金流压力", "负债", "财务困境", "无法兑付",
    "利用率达到 100%", "坏账", "无法提取", "无法访问", "削减", "流动性压力", "杠杆率"
]
def score_solvency(text):
    if has_any(text, KW_SOLV):
        if has_any(text, STABLES) or has_any(text, ["发行方", "储备资产"]):
            return 0.88
        return 0.78
    return 0.0


# 11) 基础设施 / 协议层异常风险
KW_INFRA = [
    "跨链桥异常", "跨链桥故障", "预言机异常", "预言机失灵", "停止出块",
    "分叉异常", "共识失败", "RPC故障", "节点故障", "主网故障", "网络停止",
    "跨链桥", "域名劫持", "DNS", "域名注册商", "网关", "中继器", "侧链",
    "桥接", "OFT跨链桥", "区块重组", "协议暂停", "网络分叉", "共识问题",
    "量子计算", "密钥恢复", "基础设施攻击"
]
def score_infra(text):
    NEG_INFRA_NORMAL = ["推出", "上线", "服务", "部署", "竞赛", "净流入", "净流出", "资金净流入", "资金净流出",
                        "测试", "研究", "提议", "升级", "交易量", "供应量", "讨论",
                        "量子计算", "合作", "硬件钱包", "支付安全",
                        "讨论", "可能", "提议", "研究", "测试", "预警", "防御",
                        "安全运营", "无资金损失"]
    HACK_ATTACK_KW = ["攻击", "漏洞", "钓鱼", "被盗", "黑客"]
    if has_any(text, KW_INFRA):
        if has_any(text, HACK_ATTACK_KW):
            return 0.75
        if has_any(text, NEG_INFRA_NORMAL) and not has_any(text, ["攻击", "漏洞", "异常", "关闭"]):
            return 0.0
        if has_any(text, NEG_NO_RISK):
            return 0.50
        return 0.80
    return 0.0


# 12) 宏观 / 政策冲击风险
KW_MACRO = [
    "美元走强", "美元指数走强", "油价飙升", "美债收益率走高", "加息预期",
    "风险偏好下降", "避险情绪升温", "宏观利空", "政策收紧", "禁令",
    "战争", "地缘政治", "油价", "国债", "美联储", "央行", "CPI", "GDP",
    "利率", "贸易战", "制裁", "经济衰退", "流动性危机", "信贷危机",
    "货币政策", "美股大跌", "全球市场"
]
def score_macro(text):
    NEG_MACRO_ANALYSIS = [
        "分析", "探究", "预测市场", "概率", "讨论", "回顾", "研报", "评论",
        "上涨", "下跌", "涨幅", "价格", "走势",
        "概率最高", "评级上调", "策略师", "国债", "合作", "倡议",
        "预测", "回顾", "监控", "报告", "概率", "调查", "基金经理调查", "融资", "预期",
        "Polymarket", "预测市场", "合约", "概率市场"
    ]
    MACRO_ACTION_WORDS = ["实施", "宣布", "加息", "制裁", "禁令", "限制", "暂停", "征收", "加征", "禁止"]
    MACRO_SHOCK_WORDS = ["谈判失败", "战争影响", "威胁解雇", "冲击", "需求崩溃", "危机"]
    if has_any(text, KW_MACRO):
        if has_any(text, NEG_MACRO_ANALYSIS) and not has_any(text, MACRO_ACTION_WORDS):
            return 0.0
        if not has_any(text, MACRO_ACTION_WORDS) and not has_any(text, MACRO_SHOCK_WORDS):
            return 0.0
        pct = extract_max_pct(text)
        if has_any(text, ["部长", "总统", "美联储主席"]) or has_any(text, MACRO_SHOCK_WORDS):
            base = 0.50
        else:
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


def score_to_label(score_01: float) -> str:
    """
    把 0~1 分数映射到 low / medium / high
    这里先尽量对齐你那份银标的大致分桶：
    low:   < 0.40
    medium:[0.40, 0.70)
    high:  >= 0.70
    """
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

    score_hack_val = raw_scores.get("score_hack", 0.0)
    score_infra_val = raw_scores.get("score_infra", 0.0)
    if score_hack_val > 0.3 and score_infra_val > 0.5:
        INFRA_ATTACK_TIE = ["跨链桥", "域名劫持", "DNS", "网关", "桥接", "OFT跨链桥", "域名注册商", "中继器"]
        if has_any(text, INFRA_ATTACK_TIE):
            max_score_name = "score_infra"
        else:
            max_score_name = "score_hack"
    else:
        max_score_name = max(raw_scores, key=raw_scores.get)

    max_score_01 = raw_scores[max_score_name]

    hit_types = []
    for score_name, score_val in raw_scores.items():
        if score_val >= TYPE_THRESHOLD:
            hit_types.append(RISK_NAME_MAP[score_name])

    result = {name: round(val, 4) for name, val in raw_scores.items()}

    risk_100 = int(round(max_score_01 * 100))

    result["risk"] = risk_100
    result["rule_label"] = score_to_label(max_score_01)
    result["rule_types"] = "|".join(hit_types)
    result["rule_primary_type"] = RISK_NAME_MAP[max_score_name] if max_score_01 >= TYPE_THRESHOLD else "无明显风险"

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