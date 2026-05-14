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
# 领域限定：要求上下文包含加密相关词汇
CRYPTO_CONTEXT = ["链上", "代币", "智能合约", "DeFi", "NFT", "区块链", "加密货币", "数字资产", "钱包", "交易所"]
# 正面/技术性词汇排除
POSITIVE_HACK = ["黑客松", "漏洞检测", "漏洞修复", "安全审计", "渗透测试"]
def score_hack(text):
    if has_any(text, POSITIVE_HACK):
        return 0.0
    if has_any(text, NEG_NO_RISK):
        if has_any(text, KW_HACK):
            return 0.20
    if has_any(text, KW_HACK):
        # 领域限定：必须包含加密相关词汇
        if not has_any(text, CRYPTO_CONTEXT):
            return 0.0
        usd = extract_usd_equiv(text)
        return clip01(0.90 + 0.08 * smooth_strength(usd, 50_000, 200_000))
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
REG_ACTORS = ["SEC", "CFTC", "司法部", "检察", "法院", "监管", "执法", "警察", "法官", "审计", "调查机构", "税务"]
REG_ACTIONS = ["起诉", "指控", "调查", "罚款", "制裁", "传唤", "逮捕", "拘留", "认罪", "和解", "判决", "冻结", "查封", "禁令", "诉讼"]
# 扩充：覆盖监管警告、提案等风险信号
REG_WARNINGS = ["警告可能威胁", "发布监管提案", "呼吁禁止", "限制交易", "加强监管", "监管压力", "监管风险"]
def score_regulatory(text):
    if has_any(text, ["SEC"]) and has_any(text, NEG_REG_ONLY_TALK) and not has_any(text, REG_ACTIONS):
        return 0.0
    if has_any(text, REG_ACTORS) and has_any(text, REG_ACTIONS):
        return 0.80
    if has_any(text, REG_WARNINGS):
        return 0.65
    return 0.0


# 4) 交易所/链/钱包运维风险
KW_OUTAGE = ["暂停提现", "暂停充提", "暂停充值", "提现暂停", "充值暂停", "维护", "钱包维护", "宕机", "系统故障", "无法交易", "停止充提", "网络拥堵", "钱包无法访问", "私钥丢失", "私钥未移交", "提款异常", "提款暂停", "资金无法动用"]
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

    if has_any(text, ["钱包无法访问", "私钥丢失", "私钥未移交", "提款异常", "提款暂停", "资金无法动用"]):
        return 0.78

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
KW_LIQ = ["爆仓", "强平", "清算", "liquidation", "永续", "合约", "杠杆", "保证金"]
# 核心负面词：必须与清算/强平/爆仓强关联
CORE_LIQ = ["爆仓", "强平", "清算", "liquidation"]
def score_liquidation(text):
    if not has_any(text, KW_LIQ):
        return 0.0
    # 必须包含核心负面词
    if not has_any(text, CORE_LIQ):
        return 0.0
    usd = extract_usd_equiv(text)
    if usd <= 0:
        return 0.55
    return clip01(0.55 + 0.30 * smooth_strength(usd, 500_000, 2_000_000))


# 7) 大额转账/巨鲸
KW_WHALE = ["巨鲸", "whale", "转入", "转出", "转入交易所", "转出交易所", "从匿名地址", "从未知地址", "链上", "地址"]
# 行为暗示：要求上下文包含具体行为
WHALE_ACTION = ["转移至", "准备出售", "抛售压力", "大量抛售", "集中转账", "大额转账", "巨额转账"]
# 过滤词：总市值、融资、交易量等非转账数据
FILTER_WHALE = ["总市值", "融资", "交易量", "成交量", "市场份额", "市值", "流通量"]
def score_whale(text):
    if not has_any(text, KW_WHALE):
        return 0.0

    # 过滤非转账数据
    if has_any(text, FILTER_WHALE):
        return 0.0

    if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_NO_RISK):
        usd = extract_usd_equiv(text)
        if usd > 0:
            return 0.15
        return 0.05

    usd = extract_usd_equiv(text)
    # 金额门槛：明确提及超过1000万美元
    if usd <= 0:
        return 0.25
    if usd < 10_000_000:
        return 0.25
    # 行为暗示：要求上下文包含具体行为
    if has_any(text, WHALE_ACTION):
        return clip01(0.35 + 0.40 * smooth_strength(usd, 200_000, 1_500_000))
    return 0.25


# 8) 行情异常波动
KW_SHOCK = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "暴涨", "剧烈波动", "瞬间暴跌", "瞬间拉升"]
# 中性词排除：避免将上涨/下跌等中性词误判
NEUTRAL_VOL = ["上涨", "下跌", "涨幅", "跌幅", "走高", "走低", "反弹", "回落"]
def score_volatility(text):
    pct = extract_max_pct(text)
    has_time = has_any(text, TIME_HINTS)
    has_softener = has_any(text, NEG_NO_RISK)

    if has_any(text, KW_SHOCK):
        base = 0.50 if has_softener else 0.60
        if has_time:
            base += 0.05
        return clip01(base + 0.25 * smooth_strength(pct, 10, 20))

    # 仅当百分比>=15%时才考虑中性词触发
    if pct < 15:
        return 0.0

    # 如果只包含中性词，降低基础分
    if has_any(text, NEUTRAL_VOL):
        base = 0.20 + (0.05 if has_time else 0.0)
        return clip01(base + 0.30 * smooth_strength(pct, 15, 30))

    base = 0.30 + (0.05 if has_time else 0.0)
    return clip01(base + 0.40 * smooth_strength(pct, 8, 20))


# 9) 项目治理 / 团队异常风险
KW_TEAM = [
    "创始人失联", "团队失联", "删除社交媒体账号", "删除账号", "官网无法访问",
    "官网无法打开", "停止运营", "停更", "项目方失联", "团队突然解散",
    "官方失联", "核心成员离职", "多签异常"
]
def score_team(text):
    if has_any(text, KW_TEAM):
        return 0.82
    return 0.0


# 10) 偿付能力 / 储备 / 流动性风险
KW_SOLV = [
    "偿付能力", "兑付", "挤兑", "储备不足", "储备透明度不足", "流动性危机",
    "流动性不足", "资不抵债", "现金流压力", "负债", "财务困境", "无法兑付"
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
    "分叉异常", "共识失败", "RPC故障", "节点故障", "主网故障", "网络停止"
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
    "风险偏好下降", "避险情绪升温", "宏观利空", "政策收紧", "禁令"
]
def score_macro(text):
    if has_any(text, KW_MACRO):
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


def score_to_label(score_01: float) -> str:
    """
    把 0~1 分数映射到 low / medium / high
    调整阈值以降低高分scorer的贡献权重
    low:   < 0.45
    medium:[0.45, 0.75)
    high:  >= 0.75
    """
    if score_01 >= 0.75:
        return "high"
    if score_01 >= 0.45:
        return "medium"
    return "low"


def score_all_risks(text: str) -> dict:
    """
    输出：
    - 每类分数
    - risk（总分，0~100）
    - rule_label
    - rule_types
    - rule_primary_type
    """
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

    # 命中的所有类型
    hit_types = []
    for score_name, score_val in raw_scores.items():
        if score_val >= TYPE_THRESHOLD:
            hit_types.append(RISK_NAME_MAP[score_name])

    # 输出分数字段（保留 0~1）
    result = {name: round(val, 4) for name, val in raw_scores.items()}

    # 总分转成 0~100，方便和银标对齐
    risk_100 = int(round(max_score_01 * 100))

    result["risk"] = risk_100
    result["rule_label"] = score_to_label(max_score_01)
    result["rule_types"] = "|".join(hit_types)
    # 优化主类别竞争逻辑：当有具体风险事件时，其优先级高于宽泛的行情波动类别
    # 具体风险事件优先级：hack, fraud, regulatory, outage, stablecoin, liquidation, whale, team, solvency, infra, macro, volatility
    priority_order = ["score_hack", "score_fraud", "score_regulatory", "score_outage", "score_stablecoin", "score_liquidation", "score_whale", "score_team", "score_solvency", "score_infra", "score_macro", "score_volatility"]
    # 找到最高优先级且分数>0的类别
    primary_type = "无明显风险"
    for pname in priority_order:
        if raw_scores.get(pname, 0) > 0:
            primary_type = RISK_NAME_MAP[pname]
            break
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
