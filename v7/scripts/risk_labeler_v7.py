import re
import math
import pandas as pd
import os

CSV_PATH = os.environ.get("CSV_PATH", r".\data\input\raw_news.csv")
OUT_PATH = os.environ.get("OUT_PATH", r".\reports\predictions\risk_labeler_v2_output.csv")

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
NEG_REG_ONLY_TALK = ["呼吁", "建议", "提议", "敦促", "讨论", "区分", "澄清", "拟", "草案", "征求意见"]
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

NEG_POSITIVE_MOVE = ["小幅上涨", "微涨", "温和上涨", "企稳回升", "慢牛", "持续走高", "反弹", "回升", "上涨", "涨幅收窄", "回暖"]
NEG_STAT_DESC = ["平均", "均值", "中位数", "标准差", "方差", "统计", "概率", "分布", "回归", "相关性"]
NEG_VOL_FORECAST = ["预测", "调查", "预期", "有望", "预计", "展望", "分析报告", "或将", "可能", "可能将", "预判"]

# 链上/加密领域限定词
CRYPTO_DOMAIN = ["链上", "智能合约", "DeFi", "defi", "代币", "token", "Token", "TOKEN", "合约地址", "私钥", "钱包", "跨链", "预言机", "DApp", "dApp", "NFT", "nft", "矿工", "区块", "交易哈希", "gas", "GAS", "Gas", "比特币", "以太坊", "加密货币", "数字资产", "虚拟货币", "币"]

# score_hack 非加密领域否定词
NEG_NON_CRYPTO_HACK = ["医疗机构", "医疗数据", "AI", "人工智能", "软件", "企业网络", "网络安全", "数据泄露", "黑客松", "hackathon", "Hackathon"]

# score_hack 否定语境（漏洞报告/已修复等）
NEG_HACK_FALSE = ["追回", "已修复", "完成修复", "报告漏洞", "漏洞赏金", "不受影响", "未受影响", "安然无恙", "无资产损失", "未造成损失", "无资金损失", "已经恢复", "误报"]

# score_hack 商业/金融无关新闻排除（收购、信贷等非攻击事件）
NEG_HACK_BUSINESS_EXCLUDE = ["收购", "信贷基金", "私人信贷", "贷款协议", "发放贷款", "借贷协议"]

# score_liquidation 否定词
NEG_LIQ_FALSE = ["交易量", "涨幅", "纳入指数", "成交量", "成交额", "交易额", "市值", "总市值", "流通市值"]
NEG_LIQ_PROTECT = ["降低清算风险", "避免清算", "清算保护", "防止爆仓", "降低爆仓风险", "规避清算", "清算预防", "抗清算"]
NEG_LIQ_LIQUIDITY_NON_RISK = ["代币化流动性", "流动性基金", "流动性产品", "清算价", "被动清算", "接近清算", "抵押品使用率", "未实现损失", "接近爆仓", "清算线附近"]

# score_whale 否定词
NEG_WHALE_FALSE = ["融资", "市值", "交易量", "牌照", "总市值", "流通市值", "成交额", "交易额", "成交量",
                   "ETF", "ETF基金", "资产管理公司", "贝莱德", "富达", "灰度", "MicroStrategy", "上市公司",
                   "购入", "增持", "持仓", "净资产", "净值", "资产管理", "基金", "信托", "纳入指数",
                   "个人净资产", "亿万富翁", "富豪榜", "声称", "资产组合", "持仓披露", "持有声明", "持仓情况"]

# score_whale 产品/平台推广类上下文，非真实转移
NEG_WHALE_PRODUCT_DESC = ["推出", "上线", "平台", "协议", "DeFi", "借贷", "流动性池", "AMM", "质押", "挖矿", "产品"]

# score_volatility 否定词（扩充财务利好）
NEG_VOL_FALSE = ["活跃地址", "交易量增长", "支持率", "网络活动", "用户增长", "开发者", "TVL", "tvl", "锁仓量",
                 "总市值", "24小时交易量", "占有率达", "占比", "市值排名", "交易量排名", "成交量排名", "市值报告",
                 "收入与利润", "利润增长", "营收增长", "业绩增长", "每股收益", "息税前利润", "财务报告", "业绩", "收益增长"]

# score_volatility 非市场内容否定词（访谈/观点/AI等）
NEG_VOL_NON_MARKET = ["访谈", "AMA", "分析师指出", "分析师表示", "分析师认为", "观点", "建议", "学习",
                      "人工智能", "AI趋势", "科普", "教育", "指南", "入门", "初学者"]

# score_regulatory 否定词
NEG_REG_FALSE = ["AI", "人工智能", "谷歌", "医疗", "身份验证", "生物识别", "自动驾驶"]
NEG_REGULATORY_POSITIVE = ["解除禁令", "解除制裁", "允许", "不视为", "不构成", "裁定", "不属于证券",
                           "许可", "合法化", "认可", "放行", "批准", "放宽", "放松",
                           "监管明确", "合规指引", "监管框架", "监管沙盒", "试点", "豁免",
                           "胜诉"]

# 负面执法动作词（用于 score_regulatory）
NEG_REG_NEGATIVE_ACTIONS = ["罚款", "指控", "起诉", "逮捕", "禁令", "禁止", "冻结", "查封", "判刑", "监禁", "处罚", "制裁"]

# score_regulatory 加密领域限定词
REG_CRYPTO_DOMAIN = ["加密", "数字货币", "虚拟货币", "代币", "token", "Token", "TOKEN", "区块链", "比特币", "以太坊", "稳定币", "交易所", "DeFi", "defi", "NFT", "nft"]

# score_fraud 额外关键词
KW_FRAUD_EXTRA = ["团队控制", "供应量集中", "内部人士", "钱包持有", "代币集中", "筹码集中", "高度控盘", "操纵市场"]

# score_volatility 漏召回关键词
KW_VOL_MISS = ["价格波动", "最大痛苦点", "抛售", "回调", "挤压", "逼空", "空头挤压", "多头挤压",
               "价格下滑", "大幅下挫", "跳水", "滑落", "急跌", "大跌", "狂跌", "熔断"]

# score_regulatory 漏召回关键词
KW_REG_MISS = ["法案", "草案", "呼吁", "禁止", "反对", "银行协会", "监管机构", "立法者", "参议员", "众议员", "国会"]

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
KW_HACK = ["漏洞", "攻击", "被盗", "盗取", "重入", "闪电贷", "利用", "利用漏洞", "黑客", "exploit", "hacker", "入侵",
           "被利用", "遭受攻击", "攻击事件", "攻破", "窃取", "损失金额", "被黑", "安全漏洞", "由于漏洞", "黑客攻击",
           "漏洞攻击", "社会工程", "钓鱼攻击", "量子攻击", "后门", "合约暂停", "安全事件", "异常活动",
           "漏洞攻击", "坏账", "安全警钟", "量子攻击", "社会工程攻击"]  # 新增真实攻击词


def score_hack(text):
    # 商业无关新闻排除，除非存在明确攻击动作
    if has_any(text, NEG_HACK_BUSINESS_EXCLUDE) and not has_any(text, ["漏洞", "攻击", "被黑", "安全事件"]):
        return 0.0

    if has_any(text, NEG_HACK_FALSE) and not has_any(text, ["仍受损", "未被追回", "资金消失"]):
        if has_any(text, KW_HACK):
            # 若有明确攻击和大幅度损失，突破缓和限制给予高分
            usd = extract_usd_equiv(text)
            if usd > 1_000_000 and has_any(text, ["损失", "被盗", "盗取", "资金消失", "资金损失", "被利用"]):
                return clip01(0.85 + 0.10 * smooth_strength(usd, 1_000_000, 20_000_000))
            return 0.20
    # 若非加密领域且无加密域词，果断排除
    if has_any(text, NEG_NON_CRYPTO_HACK) and not has_any(text, CRYPTO_DOMAIN):
        return 0.0
    if has_any(text, KW_HACK):
        usd = extract_usd_equiv(text)
        if usd > 0:
            return clip01(0.85 + 0.10 * smooth_strength(usd, 1_000_000, 20_000_000))
        # 明确攻击证据，即使无金额也给予较高分
        if has_any(text, ["攻击事件", "被黑", "安全漏洞", "利用漏洞", "黑客攻击", "漏洞攻击", "遭受攻击", "窃取"]):
            return 0.70
        # 其他攻击词，可能为讨论前科
        if has_any(text, ["损失", "被盗", "盗取", "资金消失", "资金损失", "被利用"]):
            return 0.60
        return 0.40
    return 0.0


# 2) 诈骗/跑路/rug
KW_FRAUD = ["诈骗", "骗局", "庞氏", "传销", "跑路", "rug", "rugpull", "钓鱼", "假冒", "冒充", "卷款"]

def score_fraud(text):
    if has_any(text, NEG_FRAUD):
        return 0.0
    if has_any(text, KW_FRAUD):
        return 0.88
    if has_any(text, KW_FRAUD_EXTRA):
        return 0.55
    return 0.0


# 3) 监管/法律风险
REG_ACTORS = ["SEC", "CFTC", "司法部", "检察", "法院", "监管", "执法", "警察", "法官", "审计", "调查机构", "税务"]
REG_WEAK_SIGNALS = ["提案", "草案", "咨询期", "监管阻力", "警告", "提醒", "关注", "担忧", "压力", "审查", "听证会", "立法", "法规", "政策"]

def score_regulatory(text):
    if has_any(text, NEG_REG_FALSE):
        return 0.0
    if has_any(text, ["SEC"]) and has_any(text, NEG_REG_ONLY_TALK) and not has_any(text, NEG_REG_NEGATIVE_ACTIONS):
        return 0.0

    # 正面/缓和结果优先判断
    if has_any(text, NEG_REGULATORY_POSITIVE):
        if not has_any(text, NEG_REG_NEGATIVE_ACTIONS):
            return 0.15  # 中性或利好
        # 即使有负面动作，若有正面结果，降级
        return 0.30

    # 执法/强负面动作
    if has_any(text, REG_ACTORS) and has_any(text, NEG_REG_NEGATIVE_ACTIONS):
        return 0.80

    # 仅有弱信号与监管主体
    if has_any(text, REG_WEAK_SIGNALS) and (has_any(text, REG_ACTORS) or has_any(text, ["监管", "政策", "法律"])):
        if has_any(text, REG_CRYPTO_DOMAIN):
            return 0.35
        return 0.25

    if has_any(text, KW_REG_MISS) and has_any(text, REG_CRYPTO_DOMAIN):
        return 0.30
    return 0.0


# 4) 交易所/链/钱包运维风险
KW_OUTAGE = ["暂停提现", "暂停充提", "暂停充值", "提现暂停", "充值暂停", "维护", "钱包维护", "宕机", "系统故障", "无法交易", "停止充提", "网络拥堵"]
KW_OUTAGE_EXTRA = ["冷钱包无法访问", "私钥未移交", "资产无法访问", "提款异常", "提现延迟", "充值延迟", "无法提现", "无法充值", "访问异常", "登录异常", "API异常"]

def score_outage(text):
    outage_keywords = KW_OUTAGE + KW_OUTAGE_EXTRA
    if not has_any(text, outage_keywords):
        return 0.0

    if has_any(text, NEG_PLANNED_MAINT) or has_any(text, NEG_NO_RISK):
        if "暂停提现" in text or "停止充提" in text or "暂停充提" in text:
            return 0.35
        return 0.20

    if has_any(text, ["暂停提现", "暂停充提", "停止充提", "提现暂停", "充值暂停", "无法提现", "无法充值"]):
        return 0.72

    if has_any(text, ["宕机", "系统故障", "无法交易"]):
        return 0.68

    if has_any(text, ["提现延迟", "充值延迟", "访问异常", "登录异常", "API异常"]):
        return 0.45

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
KW_LIQ = ["爆仓", "强平", "清算", "liquidation", "强制平仓"]
KW_LIQ_WEAK = ["期权集中到期", "负资金费率", "空头挤压", "逼空", "清算风险", "爆仓风险", "强平风险", "清算压力"]

def score_liquidation(text):
    # 保护性语境直接排除
    if has_any(text, NEG_LIQ_PROTECT):
        return 0.0
    if has_any(text, NEG_LIQ_FALSE):
        return 0.0

    # 非风险清算语境：接近清算、抵押品使用率等交由偿付能力，给极低分
    if has_any(text, NEG_LIQ_LIQUIDITY_NON_RISK):
        if has_any(text, KW_LIQ):
            return 0.15  # 部分重叠时才给微量分
        return 0.0

    liq_keywords = KW_LIQ + KW_LIQ_WEAK
    if not has_any(text, liq_keywords):
        return 0.0

    # 仅弱信号，无强清算词
    if has_any(text, KW_LIQ_WEAK) and not has_any(text, KW_LIQ):
        return 0.20

    usd = extract_usd_equiv(text)
    if usd <= 0:
        return 0.45
    # 下调上限，避免轻易到80
    return clip01(0.50 + 0.25 * smooth_strength(usd, 500_000, 2_000_000))


# 7) 大额转账/巨鲸
KW_WHALE = ["巨鲸", "whale", "转入", "转出", "转入交易所", "转出交易所", "从匿名地址", "从未知地址", "链上", "地址", "钱包转移", "大额转账",
            "准备出售", "抛售", "转移至交易所准备卖出", "大户准备卖出", "鲸鱼卖出"]
KW_WHALE_WEAK = ["大额转移", "大额交易", "大额资金", "大额资产", "巨额转账", "巨额转移"]

def score_whale(text):
    if has_any(text, NEG_WHALE_FALSE):
        return 0.0
    whale_keywords = KW_WHALE + KW_WHALE_WEAK
    if not has_any(text, whale_keywords):
        return 0.0

    # 必须包含链上转移动作或地址特征，否则视为非行为性描述
    if not has_any(text, ["转入", "转出", "转移", "发送", "从地址", "链上", "地址"]):
        return 0.0

    # 产品/平台推广描述排除（除非有具体地址或哈希）
    if has_any(text, NEG_WHALE_PRODUCT_DESC) and not has_any(text, ["地址", "交易哈希", "txid", "TXID"]):
        return 0.0

    # 计划/未来性内容降权
    future_discount = 1.0
    if has_any(text, ["计划", "即将", "预期", "可能", "或将", "潜在", "准备"]) and not has_any(text, ["已经", "已", "确认", "完成"]):
        future_discount = 0.4

    if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_NO_RISK):
        usd = extract_usd_equiv(text)
        if usd > 10_000_000:
            return 0.10
        return 0.02

    usd = extract_usd_equiv(text)

    if has_any(text, KW_WHALE_WEAK) and not has_any(text, KW_WHALE):
        if usd <= 1_000_000:
            return 0.05 * future_discount
        return clip01(0.25 + 0.20 * smooth_strength(usd, 50_000_000, 200_000_000)) * future_discount

    # strong signals
    if usd <= 500_000:
        return 0.25 * future_discount
    return clip01(0.35 + 0.40 * smooth_strength(usd, 10_000_000, 100_000_000)) * future_discount


# 8) 行情异常波动
KW_SHOCK = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "剧烈波动", "瞬间暴跌", "瞬间拉升"]

def score_volatility(text):
    # 排除访谈/观点/非市场内容，除非出现强波动信号
    if has_any(text, NEG_VOL_NON_MARKET) and not has_any(text, KW_SHOCK):
        return 0.0

    # 财务利好完全排除（若无负面波动）
    if has_any(text, ["收入与利润", "利润增长", "营收增长", "业绩增长", "每股收益", "息税前利润"]):
        if not has_any(text, ["暴跌", "闪崩", "瀑布", "腰斩", "插针", "危机", "暴雷"]):
            return 0.0

    # 前瞻/预测类文本弱化
    if has_any(text, NEG_VOL_FORECAST) and not has_any(text, KW_SHOCK):
        return 0.0

    pct = extract_max_pct(text)
    has_time = has_any(text, TIME_HINTS)
    has_softener = has_any(text, NEG_NO_RISK)

    if has_any(text, NEG_VOL_FALSE):
        return 0.0

    # 温和上涨等正面描述，且未出现极端下跌词
    if has_any(text, NEG_POSITIVE_MOVE) and not has_any(text, ["暴跌", "闪崩", "瀑布", "腰斩", "插针"] + KW_VOL_MISS + KW_SHOCK):
        return 0.0
    if has_any(text, NEG_STAT_DESC):
        return 0.0

    # 强波动关键词触发
    if has_any(text, KW_SHOCK):
        base = 0.50 if has_softener else 0.65
        if has_time:
            base += 0.05
        return clip01(base + 0.20 * smooth_strength(pct, 10, 20))

    if has_any(text, KW_VOL_MISS):
        base = 0.40 if has_softener else 0.50
        if has_time:
            base += 0.05
        return clip01(base + 0.20 * smooth_strength(pct, 5, 15))

    # 无强关键词但百分比显著，此时要求更严格的阈值（>=15%）
    if pct >= 50:
        base = 0.55 if has_softener else 0.65
        if has_time:
            base += 0.05
        return clip01(base)
    if pct >= 30:
        base = 0.40 if has_softener else 0.50
        if has_time:
            base += 0.05
        return clip01(base + 0.60 * smooth_strength(pct, 30, 50))
    if pct >= 15:
        base = 0.25 if has_softener else 0.35
        if has_time:
            base += 0.05
        return clip01(base + 0.30 * smooth_strength(pct, 15, 30))

    return 0.0


# 9) 项目治理 / 团队异常风险
KW_TEAM = [
    "创始人失联", "团队失联", "删除社交媒体账号", "删除账号", "官网无法访问",
    "官网无法打开", "停止运营", "停更", "项目方失联", "团队突然解散",
    "官方失联", "核心成员离职", "多签异常", "治理攻击", "内部操纵",
    "创始人套现", "团队内讧", "核心成员被捕"
]
KW_TEAM_WEAK = ["高管离职", "项目停滞", "转型受阻", "团队变动", "人事调整", "治理争议", "投票率低", "提案失败"]

def score_team(text):
    if has_any(text, KW_TEAM):
        return 0.82
    if has_any(text, KW_TEAM_WEAK):
        return 0.35
    return 0.0


# 10) 偿付能力 / 储备 / 流动性风险
KW_SOLV = [
    "偿付能力", "兑付", "挤兑", "储备不足", "储备透明度不足", "流动性危机",
    "流动性不足", "资不抵债", "现金流压力", "负债", "财务困境", "无法兑付",
    "抵押品使用率过高", "接近清算线", "未实现损失", "抵押品接近上限"
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
    "网络中断", "共识问题", "分叉风险", "节点宕机", "出块停止", "链停止"
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
    "战争", "地缘冲突", "制裁", "货币政策", "央行", "利率决议", "衰退",
    "贸易摩擦", "军事行动", "冲突升级", "局势紧张", "经济衰退", "通胀",
    "贬值", "资本管制", "金融风暴", "全球经济", "美伊", "海湾", "霍尔木兹"
]

def score_macro(text):
    if not has_any(text, KW_MACRO):
        return 0.0
    # 必须与加密市场关联，否则不予考虑
    if not has_any(text, CRYPTO_DOMAIN + REG_CRYPTO_DOMAIN):
        return 0.0
    pct = extract_max_pct(text)
    base = 0.45
    return clip01(base + 0.10 * smooth_strength(pct, 3, 10))


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
    max_score_name = max(raw_scores, key=raw_scores.get)
    max_score_01 = raw_scores[max_score_name]

    # 主类别：只要最高分>0，就输出对应风险类型，取消硬阈值裁决
    primary_type = RISK_NAME_MAP[max_score_name] if max_score_01 > 0 else "无明显风险"

    effective_risk = int(round(max_score_01 * 100))
    effective_label = score_to_label(max_score_01) if max_score_01 > 0 else "low"

    hit_types = []
    for score_name, score_val in raw_scores.items():
        if score_val >= TYPE_THRESHOLD:
            hit_types.append(RISK_NAME_MAP[score_name])

    result = {name: round(val, 4) for name, val in raw_scores.items()}
    result["risk"] = effective_risk
    result["rule_label"] = effective_label
    result["rule_types"] = "|".join(hit_types)
    result["rule_primary_type"] = primary_type

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
