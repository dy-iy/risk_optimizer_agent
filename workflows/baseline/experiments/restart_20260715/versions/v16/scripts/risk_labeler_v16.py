import re
import math
import pandas as pd
import os

CSV_PATH = os.environ.get("CSV_PATH", r"./data/input/raw_1000_news.csv")
OUT_PATH = os.environ.get("OUT_PATH", r"./reports/predictions/risk_labeler_v2_output.csv")

df = pd.read_csv(CSV_PATH)

# 优先拼接标题+内容
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
# 扩展 NEG_REG_ONLY_TALK 加入 '表示' '称' '批评' '认为'，具体用在 scorer 里
NEG_REG_ONLY_TALK = ["呼吁", "建议", "敦促", "讨论", "区分", "澄清", "明确性", "利好", "合规", "推进", "规范", "监管明确", "积极政策",
                     "判例", "胜诉", "有利", "正面", "问卷调查", "开发者调查", "散户调查",
                     "解除禁令", "撤销禁令", "不涉及监管", "驳回", "和解",
                     "表示", "称", "批评", "认为",
                     "指出", "声称", "预期", "强调"]
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

HEALED_REPORT = ["已修复", "完成修复", "追回", "追讨", "已经恢复",
                 "已解决", "已完成补丁", "修复程序", "补丁发布"]

NEG_HACK_DISCUSSION = ["安全研究", "漏洞赏金", "白帽", "道德黑客", "修复测试", "安全演练", "防护",
                       "没有发现漏洞", "并非漏洞",
                       "推迟上线", "加强安全", "推出解决方案", "安全增强", "安全措施",
                       "争议", "对比", "分析报告",
                       "预测", "可能将", "观点", "讨论"]

PHYSICAL_ATTACK = ["扳手攻击", "物理攻击", "暴力", "抢劫", "武装"]

VOL_PREDICTION_WORDS = ["预警", "或将", "可能暴跌", "可能触发", "预计下跌", "若下跌"]
VOL_REALTIME_WORDS = ["突发", "刚刚", "正在", "瞬间", "盘中"]

NEG_MACRO_DIRECTION = ["解除", "取消", "终止", "放宽", "放宽限制"]

PASSIVE_LEGAL = ["裁定", "判决", "不被视为", "驳回", "和解", "澄清", "拟"]

LEGAL_POSITIVE = ["不属于证券", "不构成利益冲突", "不予追究", "驳回起诉", "有利裁决", "不构成欺诈", "无罪", "被判无责任", "不构成"]

DRAFT_PROPOSAL_WORDS = ["草案", "征求意见", "提议", "提案", "拟"]

# 巨鲸正常运营
NEG_OPERATIONAL_TRANSFER = ["代币化份额", "基金代币化", "迁移", "分销网络", "申购", "赎回", "机构配置", "代币化"]

USD_UNIT_MULT = {
    "美元": 1.0,
    "USDT": 1.0,
    "USDC": 1.0,
    "万美元": 1e4,
    "百万美元": 1e6,
    "千万美元": 1e7,
    "亿美元": 1e8,
}


# ---------- 补丁新增列表 ----------
NEG_HEALED_DEFENSE = ["追回", "赏金", "计划完成", "提案", "漏洞报告", "补偿计划", "计划迁移", "修复测试", "已完成补丁",
                      "未受影响", "不受影响", "已防护", "已关闭", "已监控", "未利用", "并未被利用", "无资金损失"]
HISTORICAL_FRAUD = ["判刑", "有期徒刑", "赔偿", "追回诈骗", "赔偿申请", "受害者"]
NEG_WHALE_MARKET_RECAP = ["异动盘点", "市场盘点", "行情综述", "大额异动盘点"]   # 收缩范围，不再包含“监测”
NEG_VOL_DISCUSSION = ["周期", "观点", "看法", "OTC", "横盘", "交易阶段", "排名", "预测", "整理", "区间"]
NEW_VOL_SIGNALS = ["未实现损失", "巨额损失", "抛售", "极大波动"]
SEVERITY_HACK_BOOST = ["未授权铸造", "大规模", "非法铸造", "抛售"]
# 新增加密上下文，用于抑制非区块链攻击误报
CRYPTO_CONTEXT_WORDS = ["区块链", "链上", "DeFi", "代币", "钱包地址", "智能合约", "加密", "USDT", "USDC", "ETH", "BTC", "协议", "DEX", "跨链桥"]
# 鲸鱼高风险动作
HIGH_RISK_WHALE_ACTION = [
    "抛售", "大量转出", "大额出售", "转入未知地址", "从未知地址", "巨额转账",
    "巨鲸抛售", "大量抛售", "砸盘", "大额转移至未知", "转移至黑名单地址"
]
NEG_WHALE_PROFIT_TAKING = ["盈利", "获利", "浮盈", "已实现盈利", "获利了结", "止盈", "平仓止盈"]
SAFE_VOL_CONTEXT = ["板块", "Meme", "历史回顾", "2022年", "回顾", "报道", "数据显示"]
# 新增强攻击类型
HIGH_SEVERITY_HACK = ["未授权铸造", "重放攻击", "桥接攻击", "零日攻击", "闪电贷攻击", "预言机操纵", "51%攻击", "双花攻击",
                     "DNS劫持", "前端攻击", "前端漏洞"]
# 新增否定语境监管
NEG_REG_DENIAL = ["不太可能", "遭到批评", "暂未", "尚未", "不予", "并未", "未受影响", "并未冻结", "尚无计划", "没有证据", "不会制裁"]
# 中等强度监管信号
REG_MEDIUM = ["监管警告", "加强审查", "制裁风险", "调查扩大", "合规压力", "反垄断调查", "纳税申报", "起诉警告", "监管收紧"]
# 财经常用词避免误报
NEG_ACCOUNTING = ["资产负债表", "现金流量表", "利润表", "财务报告", "财报", "资产负债"]
# 新增：监管已解决/追回标记
NEG_REG_RESOLVED = ["追回", "返还", "受害者", "退赔"]  # 扩展
# 新增：宏观讨论抑制
NEG_MACRO_DISCUSSION = ["认为", "分析", "回顾", "指出", "可能", "预测", "讨论"]
# 新增：黑客报告/复盘抑制
NEG_HACK_ANALYSIS_REPORT = ["季度报告", "安全总结", "复盘", "安全评估", "年度报告"]
# 新增：波动历史语境抑制
NEG_VOL_HISTORICAL = ["当时", "曾", "2022年", "回忆", "历史", "回顾"]
# 新增：偿付能力杂项抑制
NEG_SOLV_MISC = ["ETF", "季度", "营收", "代币流动性", "财务报表"]

# ---------- 本次补丁新增 ----------
# 清算讨论/预测上下文词
NEG_LIQ_CONTEXT = ["分享", "策略", "采访", "复盘", "预测", "观点", "分析", "评论", "观点分享"]
# 基础设施扩展正向词
INFRA_EXTRA = ["DNS攻击", "DNS劫持", "注册商攻击", "域名攻击", "网络攻击", "节点离线", "网络中断", "网络瘫痪", "主网挂掉"]
# 团队风险扩展
TEAM_EXTRA = ["创始人离职", "创始人辞职", "核心成员退出", "团队内讧", "联合创始人出走", "核心开发者离职", "技术负责人辞职"]
# 偿付能力扩展
SOLV_EXTRA = ["兑付压力", "备付金不足", "储备金不足", "挤兑风险", "流动性危机", "无法兑付", "资不抵债"]
# 宏观扩展
MACRO_EXTRA = ["制裁", "贸易限制", "资本管制", "外汇管制", "金融隔离", "经济制裁", "SWIFT禁止", "出口限制", "投资禁令"]
# 运维扩展
OUTAGE_EXTRA = ["暂停交易", "系统中断", "节点异常", "服务不可用", "网站无法访问", "账户无法登录", "交易暂停"]
# 实害词（真实攻击造成的损失）
REAL_HARM_WORDS = ["损失", "受损", "盗走", "资金损失", "受影响"]
# 强基础设施异常词
INFRA_STRONG = [
    "跨链桥异常", "跨链桥故障", "预言机异常", "预言机失灵", "停止出块", "分叉异常", "共识失败", "RPC故障",
    "节点故障", "主网故障", "网络停止", "出块暂停", "节点大面积故障", "出块停滞", "主网阻塞", "协议挂起",
    "协议暂停", "网络拥塞", "共识故障", "出块延迟", "桥接中断", "网络中断", "节点离线",
    "节点异常", "系统中断", "服务不可用", "网站无法访问", "账户无法登录", "交易暂停"
]
# ---------- end ----------


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


# ---------------- 风险打分 --------------

# 1) 合约/链上漏洞攻击
KW_HACK = ["漏洞", "攻击", "被盗", "盗取", "重入", "闪电贷", "利用漏洞", "黑客", "exploit", "hacker", "入侵",
           "铸造并抛售", "无故增发", "未授权铸造", "被利用", "窃取", "盗走"]
WEAK_HACK_SIGNALS = ["安全事件", "事件"]
NEW_HACK_STRONG = ["零日", "新漏洞", "正在攻击", "未修复"]
NEW_HACK_WEAK = ["紧急", "最新攻击"]
HACK_DISCUSSION_WORDS = ["预测", "可能", "观点", "届时", "将要", "可能会", "季度报告", "安全总结", "复盘", "安全评估"]  # 扩展


def score_hack(text):
    usd = extract_usd_equiv(text)
    has_heal = has_any(text, HEALED_REPORT)
    has_new_strong = has_any(text, NEW_HACK_STRONG)
    has_new_weak = has_any(text, NEW_HACK_WEAK)
    has_kw_hack = has_any(text, KW_HACK)
    has_weak_signal = has_any(text, WEAK_HACK_SIGNALS)
    is_discussion = has_any(text, HACK_DISCUSSION_WORDS) and not has_new_strong and not has_new_weak
    has_severity_hack = has_any(text, HIGH_SEVERITY_HACK)
    has_analysis_report = has_any(text, NEG_HACK_ANALYSIS_REPORT)
    has_harm = has_any(text, REAL_HARM_WORDS)  # 实害词

    if has_any(text, PHYSICAL_ATTACK):
        return 0.0

    # 已防御/修复的进一步检查（扩展否定）
    if has_any(text, NEG_HEALED_DEFENSE):
        if has_severity_hack:
            # 高严重性但已防御，保留中低分
            base = 0.40 if usd > 10_000_000 else 0.35
            return clip01(base)
        if has_new_strong:
            base = 0.35 if usd > 1_000_000 else 0.30
            return clip01(base)
        if has_new_weak:
            base = 0.25 if usd > 1_000_000 else 0.20
            return clip01(base)
        base = 0.25
        if usd > 10_000_000:
            base = 0.35
        return clip01(base)

    # 强愈合信号：大幅降权，但保留部分风险
    if has_heal:
        if has_severity_hack:
            base = 0.55 if usd > 10_000_000 else 0.50
            return clip01(base)
        if has_new_strong:
            base = 0.45 if usd > 1_000_000 else 0.40
            return clip01(base)
        if has_new_weak:
            base = 0.35 if usd > 1_000_000 else 0.30
            return clip01(base)
        if usd > 10_000_000:
            base = 0.45
        else:
            base = 0.35
        return clip01(base)

    # 分析报告/复盘：如果不包含强攻击则降分
    if has_analysis_report and not (has_new_strong or has_severity_hack):
        if has_kw_hack:
            return 0.25
        else:
            return 0.15

    # 讨论类抑制：只有当不是明确攻击（无金额或无高危词）才生效
    if has_any(text, NEG_HACK_DISCUSSION) and not has_new_strong and not has_severity_hack:
        if usd > 1_000_000:
            return 0.35
        else:
            return 0.20

    # 一般修复/无风险描述抑制
    if has_any(text, NEG_NO_RISK):
        if has_kw_hack:
            return 0.20
        return 0.0

    if has_kw_hack:
        base = 0.55 + 0.25 * smooth_strength(usd, 0, 2_000_000)
        if has_harm:
            base += 0.10  # 实害词提分
        if usd > 10_000_000 or has_any(text, SEVERITY_HACK_BOOST):
            base += 0.15
        if is_discussion:
            base = 0.40 + 0.10 * smooth_strength(usd, 0, 1_000_000)
            if usd < 1_000_000:
                base *= 0.8
        if has_new_strong:
            base += 0.15
        if has_new_weak:
            base += 0.08
        if has_severity_hack:
            base = max(base, 0.80)
        if usd >= 1_000_000_000:
            base += 0.15
        elif usd >= 10_000_000:
            base += 0.10
        score = clip01(base)
        if not has_any(text, CRYPTO_CONTEXT_WORDS):
            score *= 0.5
        if usd > 5_000_000 and score < 0.35:
            score = 0.35
        return score

    if has_any(text, ["被利用", "窃取", "盗走"]):
        score = 0.25 if usd > 100_000 else 0.15
        if not has_any(text, CRYPTO_CONTEXT_WORDS):
            score *= 0.5
        return score

    if has_weak_signal and not has_kw_hack:
        score = 0.25 if usd > 1_000_000 else 0.12
        if not has_any(text, CRYPTO_CONTEXT_WORDS):
            score *= 0.5
        return score

    return 0.0


# 2) 诈骗/跑路/rug
KW_FRAUD_STRONG = ["诈骗", "骗局", "庞氏", "传销", "跑路", "rug", "rugpull", "钓鱼", "假冒", "冒充", "卷款",
                   "庞氏骗局", "资金盘", "理财骗局", "虚拟币诈骗"]
KW_FRAUD_WEAK = ["集中度", "控盘", "庄家", "团队钱包持有", "老鼠仓", "高控盘", "发行方控制大量供应",
                 "持仓高度集中", "代币分配", "供应量集中", "疑似内部"]


def score_fraud(text):
    if has_any(text, NEG_FRAUD):
        return 0.0
    if has_any(text, KW_FRAUD_STRONG):
        if has_any(text, HISTORICAL_FRAUD):
            if has_any(text, ["破获", "本周", "近日"]):
                return 0.70
            return 0.45
        base = 0.92
        usd = extract_usd_equiv(text)
        if usd > 100_000:
            base += 0.05
        return clip01(base)
    if has_any(text, KW_FRAUD_WEAK):
        return 0.65
    return 0.0


# 3) 监管/法律风险
REG_ACTORS = ["SEC", "CFTC", "司法部", "检察", "法院", "监管", "执法", "警察", "法官", "审计", "调查机构", "税务"]
HARD_ACTIONS = ["起诉", "指控", "罚款", "制裁", "逮捕", "拘留", "认罪", "判决", "冻结", "查封", "诉讼", "传唤"]
SOFT_ACTIONS = ["警告", "法案", "立法", "监管关注", "调查通知"]
INVESTIGATION_WORDS = ["调查"]
ENFORCEMENT_ENTITIES = ["SEC", "CFTC", "司法部", "警察", "执法", "公安", "检察"]
ACTIVE_PUNISH = ["罚款", "逮捕", "起诉", "制裁", "拘留", "认罪"]

NON_RISK_REG_CTX = ["融资", "轮", "上线", "部署", "产品", "推出", "获投"]


def score_regulatory(text):
    # 已解决/追回先处理，大幅降权
    if has_any(text, NEG_REG_RESOLVED):
        if has_any(text, ACTIVE_PUNISH) and not has_any(text, ["追回", "退赔"]):
            base = 0.35
        else:
            base = 0.20
        return clip01(base)

    # 早期返回：只有讨论/声明，而无执法/硬行动/活跃惩处，则降权但不一定归零
    if has_any(text, NEG_REG_ONLY_TALK) and not (has_any(text, ENFORCEMENT_ENTITIES) or has_any(text, HARD_ACTIONS) or has_any(text, ACTIVE_PUNISH)):
        # 若仍有监管机构或软行动，保留一点分
        if has_any(text, REG_ACTORS) or has_any(text, SOFT_ACTIONS):
            return clip01(0.25)
        else:
            return 0.0

    # 否定语境先检查
    if has_any(text, NEG_REG_DENIAL):
        if has_any(text, ENFORCEMENT_ENTITIES) and has_any(text, HARD_ACTIONS):
            base = 0.30
            if has_any(text, ACTIVE_PUNISH):
                base = 0.45
            return clip01(base)
        return 0.0

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
    non_risk_ctx = has_any(text, NON_RISK_REG_CTX)
    has_medium = has_any(text, REG_MEDIUM)

    # 草案/提议
    if draft_proposal:
        if hard:
            base = 0.45
        elif soft and actor:
            base = 0.40
        elif actor:
            base = 0.30
        else:
            base = 0.15
        if passive:
            base *= 0.8
        if neg_talk_strong:
            base *= 0.6
        if non_risk_ctx:
            base *= 0.5
        return clip01(base)

    # 减轻词或非风险语境
    if neg_talk_strong or non_risk_ctx:
        if hard:
            base = 0.55
        elif soft and actor:
            base = 0.35
        elif actor:
            base = 0.25
        else:
            base = 0.0
        if passive:
            base *= 0.5
        if non_risk_ctx:
            base *= 0.5
        if legal_positive and not active_pun:
            base *= 0.5
        return clip01(base)

    # 正常路径
    if actor and hard:
        if "冻结" in text and not active_pun:
            score = 0.55
        else:
            score = 0.80
        if passive:
            score *= 0.65
        return clip01(score)

    if actor and inv:
        return 0.75 if enforce else 0.45

    if actor and soft:
        return 0.50

    if actor:
        base = 0.40
        if legal_positive and not active_pun:
            base = 0.15
        return clip01(base)

    if hard:
        base = 0.30
        if passive:
            base *= 0.6
        return clip01(base)

    if has_medium:
        return 0.45 if actor else 0.35

    return 0.0


# 4) 交易所/链/钱包运维风险
KW_OUTAGE = ["暂停提现", "暂停充提", "暂停充值", "提现暂停", "充值暂停", "维护", "钱包维护", "宕机", "系统故障", "无法交易", "停止充提", "网络拥堵",
             "暂停运营", "提款不可用", "账户交易不可用", "暂停服务"] + OUTAGE_EXTRA


def score_outage(text):
    if not has_any(text, KW_OUTAGE):
        return 0.0

    if has_any(text, NEG_PLANNED_MAINT) or has_any(text, NEG_NO_RISK):
        if "暂停提现" in text or "停止充提" in text or "暂停充提" in text:
            return 0.40
        return 0.25

    if has_any(text, ["暂停提现", "暂停充提", "停止充提", "提现暂停", "充值暂停", "暂停运营", "提款不可用", "账户交易不可用", "暂停服务"]):
        return 0.72

    if has_any(text, ["宕机", "系统故障", "无法交易"]):
        return 0.68

    if has_any(text, OUTAGE_EXTRA):
        return 0.60

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
KW_LIQ_CORE = ["清算", "liquidation", "清算强度", "浮动亏损", "空头头寸"]
KW_LIQ_STRONG = ["爆仓", "强平"]
KW_LIQ_EXTENDED = ["永续", "合约", "杠杆", "保证金", "持仓", "仓位"]
CONDITIONAL_WORDS = ["若", "如果", "可能触发", "可能清算", "可能爆仓", "假设", "可触发"]
EVENT_CONFIRM = ["实际清算", "大量爆仓", "多头爆仓", "实际爆仓", "多头清算", "空头清算", "持仓清算", "大额爆仓", "大规模清算"]
MASSIVE_WORDS = ["大规模"]
NEG_LIQ = ["合约地址", "智能合约", "智能合约内", "合约调用", "分析", "预警", "接近", "不可能"] + NEG_LIQ_CONTEXT
MARKET_NOISE = ["交易量", "清算量", "统计", "日均", "费用", "基金", "代币化"]
KW_LIQ_EARLY = ["期权到期", "资金费率", "强平价格", "爆仓额", "清算金额"]


def score_liquidation(text):
    if has_any(text, NEG_LIQ):
        if has_any(text, EVENT_CONFIRM) or has_any(text, KW_LIQ_STRONG):
            usd = extract_usd_equiv(text)
            base = 0.35 + 0.20 * smooth_strength(usd, 500_000, 2_000_000) if usd > 0 else 0.35
            return clip01(base * 0.65)
        return 0.0

    if has_any(text, KW_LIQ_EARLY) or has_any(text, CONDITIONAL_WORDS):
        usd = extract_usd_equiv(text)
        if has_any(text, NEG_LIQ_CONTEXT) and not (has_any(text, EVENT_CONFIRM) or has_any(text, MASSIVE_WORDS)):
            base = 0.15
            if usd > 10_000_000:
                base += 0.05
            return clip01(base)
        if has_any(text, EVENT_CONFIRM) or has_any(text, KW_LIQ_STRONG):
            base = 0.45 + 0.20 * smooth_strength(usd, 500_000, 5_000_000)
        else:
            base = 0.20
            if usd > 500_000:
                base += 0.10 * smooth_strength(usd, 500_000, 5_000_000)
        return clip01(base)

    has_event_confirm = has_any(text, EVENT_CONFIRM) or (has_any(text, MASSIVE_WORDS) and (has_any(text, KW_LIQ_CORE) or has_any(text, KW_LIQ_STRONG)))
    has_strong = has_any(text, KW_LIQ_STRONG) or has_event_confirm

    if has_strong:
        usd = extract_usd_equiv(text)
        if usd <= 0:
            base = 0.40
        else:
            base = 0.50 + 0.25 * smooth_strength(usd, 500_000, 5_000_000)
        if has_any(text, CONDITIONAL_WORDS) and not has_event_confirm:
            if usd >= 100_000_000:
                penalty_factor = 0.90
            elif usd >= 50_000_000:
                penalty_factor = 0.80
            else:
                penalty_factor = 0.65
            base *= penalty_factor
            base = max(base, 0.30)
        if has_any(text, MARKET_NOISE):
            base *= 0.6
        if has_any(text, NEG_LIQ_CONTEXT) and not has_event_confirm:
            base = min(base, 0.30)
        return clip01(base)

    if has_any(text, KW_LIQ_CORE):
        usd = extract_usd_equiv(text)
        if usd <= 0 and not has_event_confirm:
            base = 0.20
        elif has_any(text, CONDITIONAL_WORDS) and not has_event_confirm:
            base = max(0.25, 0.25 if usd < 100_000_000 else 0.30)
        else:
            base = 0.35
        if has_any(text, MARKET_NOISE):
            base *= 0.6
        if has_any(text, NEG_LIQ_CONTEXT):
            base = min(base, 0.20)
        return clip01(base)

    if has_any(text, KW_LIQ_EXTENDED):
        if has_any(text, NEG_LIQ_CONTEXT):
            return 0.10
        return 0.15

    return 0.0


# 7) 大额转账/巨鲸
KW_WHALE = [
    "巨鲸", "whale", "转入", "转出", "转入交易所", "转出交易所", "从匿名地址", "从未知地址",
    "大户", "鲸鱼地址", "大额持有者", "巨量持有", "大单转账",
    "大额转移", "大量转入", "大量转出", "大额流入", "大额流出",
    "巨鲸地址", "鲸鱼钱包", "大额异动", "链上转移", "链上大额", "鲸鱼动向",
    "平仓", "出售", "转移", "撤出", "转存", "存入", "转至", "大额出售", "大量抛售", "将...存入"
]
WHALE_ACTION = [
    "转入", "转出", "转移", "从未知地址", "未知地址", "大额异动", "链上大额",
    "大额流出", "大额流入", "大额转入", "大额转出", "链上转移",
    "平仓", "出售", "撤出", "转存", "存入", "转至"
]

NEG_WHALE_DISCUSSION = ["预测", "可能", "若", "如果", "评论", "历史"]


def score_whale(text):
    usd = extract_usd_equiv(text)
    has_kw_whale = has_any(text, KW_WHALE)
    has_action = has_any(text, WHALE_ACTION)
    is_high_risk_action = has_any(text, HIGH_RISK_WHALE_ACTION)
    is_profit_taking = has_any(text, NEG_WHALE_PROFIT_TAKING)

    if usd < 2_000_000:
        if is_high_risk_action:
            base = 0.25
        elif has_action and has_kw_whale:
            base = 0.20
        else:
            return 0.0
        if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_OPERATIONAL_TRANSFER):
            base *= 0.5
        if has_any(text, NEG_WHALE_MARKET_RECAP):
            base = 0.0
        return clip01(base)

    # usd >= 2M
    if not is_high_risk_action:
        if usd > 10_000_000 and has_action:
            base = 0.40
        else:
            return 0.0
        if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_OPERATIONAL_TRANSFER):
            base = 0.25
        if has_any(text, NEG_WHALE_PROFIT_TAKING):
            base *= 0.95
        if has_any(text, NEG_WHALE_MARKET_RECAP):
            base = 0.0
        return clip01(base)

    # 高风险动作 + 金额达标
    base = 0.35 + 0.40 * smooth_strength(usd, 200_000, 1_500_000)

    if is_profit_taking:
        if usd > 10_000_000:
            base *= 0.95
        else:
            base *= 0.90

    if has_any(text, NEG_OPERATIONAL_TRANSFER):
        base *= 0.6

    if has_any(text, NEG_WHALE_DISCUSSION):
        if usd < 10_000_000:
            base *= 0.5
        else:
            base *= 0.75

    if has_any(text, NEG_INTERNAL_TRANSFER):
        base = 0.2

    if has_any(text, NEG_NO_RISK):
        base = min(base, 0.1)

    score = clip01(base)
    if has_any(text, NEG_WHALE_MARKET_RECAP):
        score = max(min(score * 0.6, 0.3), 0.2)
    return score


# 8) 行情异常波动
KW_SHOCK = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "暴涨", "剧烈波动", "瞬间暴跌", "瞬间拉升",
            "大涨", "飙升", "猛涨", "涨超", "跌超"]
NEGATIVE_PRICE_MOVE = ["下跌", "跌幅", "下挫", "大幅下跌"]
STRONG_PRICE_DROP = ["暴跌", "暴泻", "崩盘", "闪崩", "插针", "急跌", "重挫", "跳水"]


def score_volatility(text):
    pct = extract_max_pct(text)
    has_time = has_any(text, TIME_HINTS)
    is_analysis = has_any(text, VOL_PREDICTION_WORDS)
    is_realtime = has_any(text, VOL_REALTIME_WORDS)
    has_discussion = has_any(text, NEG_VOL_DISCUSSION)
    has_surge = "涨" in text or "飙升" in text or "大涨" in text or "涨幅" in text
    has_safe_ctx = has_any(text, SAFE_VOL_CONTEXT)
    is_historical = has_any(text, NEG_VOL_HISTORICAL) and not is_realtime

    if is_historical:
        if is_realtime:
            pass
        if has_any(text, NEW_VOL_SIGNALS) or has_any(text, STRONG_PRICE_DROP):
            base = 0.10
        else:
            return 0.0
        if is_analysis:
            base *= 0.5
        return clip01(base)

    if has_any(text, NEW_VOL_SIGNALS):
        base = 0.50
        if has_time:
            base += 0.05
        score = clip01(base + 0.30 * smooth_strength(max(pct, 1), 5, 20))
        if is_analysis and not is_realtime:
            score *= 0.6
        if has_discussion:
            score *= 0.35
        if has_safe_ctx and not (has_any(text, STRONG_PRICE_DROP) or has_any(text, NEW_VOL_SIGNALS)):
            score *= 0.5
        if is_historical:
            score *= 0.5
        return clip01(score)

    if has_any(text, STRONG_PRICE_DROP):
        base = 0.30 if pct == 0 else 0.55
        if has_time:
            base += 0.05
        score = clip01(base + 0.25 * smooth_strength(pct if pct > 0 else 5, 5, 20))
        if is_analysis and not is_realtime:
            score *= 0.6
        if has_discussion:
            score *= 0.35
        if has_safe_ctx and not (has_any(text, STRONG_PRICE_DROP) or has_any(text, NEW_VOL_SIGNALS)):
            score *= 0.5
        if is_historical:
            score *= 0.4
        return clip01(score)

    if pct >= 30 and has_surge:
        base = 0.35
        score = clip01(base + 0.20 * smooth_strength(pct, 30, 100))
        if is_analysis and not is_realtime:
            score *= 0.6
        if has_discussion:
            score *= 0.35
        if has_safe_ctx:
            score *= 0.5
        if is_historical:
            score *= 0.5
        return clip01(score)

    if has_any(text, KW_SHOCK):
        surge_only = has_any(text, ['大涨', '飙升', '猛涨', '涨超']) and not has_any(text, STRONG_PRICE_DROP)
        if pct < 3:
            base = 0.25 if surge_only else 0.35
            smooth_scale = 0.05 if surge_only else 0.10
            score = clip01(base + smooth_scale * smooth_strength(max(pct, 1), 1, 5))
        else:
            base = 0.35 if surge_only else 0.45
            smooth_scale = 0.15 if surge_only else 0.30
            score = clip01(base + smooth_scale * smooth_strength(pct, 5, 20))
        if is_analysis and not is_realtime:
            score *= 0.6
        if has_discussion:
            score *= 0.35
        if has_safe_ctx:
            score *= 0.5
        if is_historical:
            score *= 0.5
        return clip01(score)

    if pct >= 5 and has_any(text, NEGATIVE_PRICE_MOVE):
        base = 0.25 + (0.05 if has_time else 0.0)
        score = clip01(base + 0.30 * smooth_strength(pct, 5, 20))
        if is_analysis and not is_realtime:
            score *= 0.6
        if has_discussion:
            score *= 0.35
        if has_safe_ctx:
            score *= 0.5
        if is_historical:
            score *= 0.5
        return clip01(score)

    return 0.0


# 9) 项目治理 / 团队异常风险
KW_TEAM = [
    "创始人失联", "团队失联", "删除社交媒体账号", "删除账号", "官网无法访问",
    "官网无法打开", "停止运营", "停更", "项目方失联", "团队突然解散",
    "官方失联", "核心成员离职", "多签异常", "团队辞职", "核心开发者离开",
    "项目方出货", "团队砸盘", "创始人抛售", "核心成员套现",
    "项目方清仓", "团队减持", "团队抛售", "项目方出售", "创始人抛售",
    "核心成员抛售", "项目方抛售",
    "团队解锁", "释放代币给团队", "锁定机制变更",
    "项目方减持", "股东减持", "解锁代币",
    "代币释放", "团队大规模套现", "项目方套现",
    "团队解散", "创始人离职", "VG暂停", "核心成员退出"
] + TEAM_EXTRA


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
    "流动性压力", "削减",
    "抵押品使用率过高", "清算阈值", "借贷上限",
    "抵押品不足", "杠杆过高",
    "TVL下降", "资金外流", "大规模出逃", "流动性下降", "资金流出", "大规模赎回", "赎回潮",
    "储备金不足", "冻结提现", "无法提现"
] + SOLV_EXTRA
STRONG_SOLV = ["挤兑", "资不抵债", "储备金亏空", "崩盘", "无法兑付", "偿付缺口", "流动性枯竭", "坏账风险", "坏账率攀升", "抵押品不足"]


def score_solvency(text):
    if not has_any(text, KW_SOLV):
        return 0.0

    has_strong = has_any(text, STRONG_SOLV)
    is_discussion = has_any(text, ["央行", "研究人员", "报告", "评估", "讨论"])
    has_misc = has_any(text, NEG_SOLV_MISC) and not has_strong

    if has_misc:
        return clip01(0.20)

    if has_any(text, NEG_ACCOUNTING) and not has_strong:
        return clip01(0.20)

    if has_any(text, STABLES) or has_any(text, ["发行方", "储备资产"]):
        if has_any(text, ["脱锚", "挤兑", "储备不足", "储备金亏空", "跌破1美元", "偿付能力"]):
            base = 0.88 if has_strong else 0.60
        else:
            base = 0.25  # 仅有稳定币提及，降分
    else:
        base = 0.78 if has_strong else 0.50

    if is_discussion and not has_strong:
        base *= 0.5

    return clip01(base)


# 11) 基础设施 / 协议层异常风险
KW_INFRA = [
    "跨链桥异常", "跨链桥故障", "预言机异常", "预言机失灵", "停止出块",
    "分叉异常", "共识失败", "RPC故障", "节点故障", "主网故障", "网络停止",
    "出块暂停", "节点大面积故障", "出块停滞", "主网阻塞", "协议挂起", "RPC大面积超时", "链上暂停",
    "协议暂停", "网络拥塞", "共识故障", "出块延迟",
    "桥接中断", "分叉", "网络中断", "节点离线"
] + INFRA_EXTRA


def score_infra(text):
    if not has_any(text, KW_INFRA):
        return 0.0
    is_strong = has_any(text, INFRA_STRONG)
    has_neg_no_risk = has_any(text, NEG_NO_RISK)
    if is_strong:
        if has_neg_no_risk:
            return 0.30
        return 0.75
    else:
        # weak infra signal (e.g., "网络攻击")
        if has_neg_no_risk:
            return 0.15
        return 0.30


# 12) 宏观 / 政策冲击风险
KW_MACRO = [
    "美元走强", "美元指数走强", "油价飙升", "美债收益率走高", "加息预期",
    "风险偏好下降", "避险情绪升温", "宏观利空", "政策收紧", "禁令",
    "战争", "军事冲突", "地缘政治", "利率决议", "央行加息", "央行降息",
    "国债危机", "金融市场恐慌"
] + MACRO_EXTRA


def score_macro(text):
    if not has_any(text, KW_MACRO):
        return 0.0
    if "禁令" in text and has_any(text, NEG_MACRO_DIRECTION):
        return 0.0

    if has_any(text, ["调查", "要求调查", "司法调查"]) and not has_any(text, ["军事冲突", "战争"]):
        pct = extract_max_pct(text)
        base = 0.10
        score = clip01(base + 0.15 * smooth_strength(pct, 3, 10))
    else:
        pct = extract_max_pct(text)
        base = 0.35
        score = clip01(base + 0.20 * smooth_strength(pct, 3, 10))

    if has_any(text, NEG_MACRO_DISCUSSION):
        if not has_any(text, VOL_REALTIME_WORDS + TIME_HINTS):
            score *= 0.6
            score = min(score, 0.4)

    if not has_any(text, ['加密货币', '比特币', '币价', '市场', '行情', '加密', 'ETH', 'BTC']):
        score *= 0.5

    return score


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

TYPE_THRESHOLD = 0.35

PRIMARY_PRIORITY = {
    "监管与法律风险": 10,
    "偿付能力 / 储备 / 流动性风险": 9,
    "链上漏洞 / 攻击风险": 8,
    "项目治理 / 团队异常风险": 7,
    "诈骗 / 跑路 / Rug Pull 风险": 6,
    "交易所与系统运维风险": 5,
    "基础设施 / 协议层异常风险": 4,
    "稳定币异常风险": 3,
    "异常行情波动风险": 3,
    "爆仓 / 清算风险": 2,
    "大额转账 / 巨鲸行为风险": 2,
    "宏观 / 政策冲击风险": 1,
}

STRONG_REAL_EVENT_SIGNALS = [
    "实际清算", "大量爆仓", "已发生", "确认", "关闭", "暂停提现", "无法访问",
    "冻结", "制裁", "逮捕", "起诉", "正在进行攻击", "未修复", "零日",
    "已修复", "追回", "暴跌", "崩盘", "插针", "闪崩", "暂停充提", "停止充提"
]


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

    hit_types = []
    for score_name, score_val in raw_scores.items():
        if score_val >= TYPE_THRESHOLD:
            hit_types.append(RISK_NAME_MAP[score_name])

    result = {name: round(val, 4) for name, val in raw_scores.items()}

    active_scorer_count = sum(1 for v in raw_scores.values() if v > 0.0)
    if active_scorer_count >= 3 or max_score_01 >= 0.80:
        factor = 1.0
    elif active_scorer_count == 2:
        factor = 0.85
    else:
        factor = 0.70

    risk_100 = int(round(max_score_01 * factor * 100))

    if max_score_01 < TYPE_THRESHOLD:
        primary_type_name = "无明显风险"
    else:
        second_above_threshold = second_score_01 >= TYPE_THRESHOLD
        gap = max_score_01 - second_score_01
        if second_above_threshold and gap < 0.1 and max_score_01 < 0.7:
            second_name = sorted_scores[1][0]
            primary_name_max = RISK_NAME_MAP[max_score_name]
            primary_name_second = RISK_NAME_MAP[second_name]
            if PRIMARY_PRIORITY.get(primary_name_second, 0) > PRIMARY_PRIORITY.get(primary_name_max, 0):
                primary_type_name = primary_name_second
            else:
                primary_type_name = primary_name_max
        else:
            primary_type_name = RISK_NAME_MAP[max_score_name]
            if second_above_threshold and gap < 0.05:
                second_name = sorted_scores[1][0]
                second_type = RISK_NAME_MAP[second_name]
                if PRIMARY_PRIORITY.get(second_type, 0) > PRIMARY_PRIORITY.get(primary_type_name, 0):
                    primary_type_name = second_type

    fraud_score = raw_scores.get("score_fraud", 0.0)
    hack_score = raw_scores.get("score_hack", 0.0)
    if has_any(text, ["诈骗", "骗局"]) and fraud_score > 0.5:
        if fraud_score >= hack_score - 0.15:
            primary_type_name = "诈骗 / 跑路 / Rug Pull 风险"
    elif primary_type_name == "链上漏洞 / 攻击风险":
        if fraud_score >= TYPE_THRESHOLD and (hack_score - fraud_score) < 0.15 and has_any(text, KW_FRAUD_STRONG):
            primary_type_name = "诈骗 / 跑路 / Rug Pull 风险"
    if primary_type_name == "链上漏洞 / 攻击风险":
        if fraud_score >= TYPE_THRESHOLD and (hack_score - fraud_score) < 0.1 and has_any(text, KW_FRAUD_STRONG):
            primary_type_name = "诈骗 / 跑路 / Rug Pull 风险"

    result["risk"] = risk_100
    result["rule_label"] = score_to_label(max_score_01)
    result["rule_types"] = "|".join(hit_types)
    result["rule_primary_type"] = primary_type_name

    return result


# 计算全部输出
score_df = df["text"].apply(lambda x: pd.Series(score_all_risks(x)))
df_out = pd.concat([df, score_df], axis=1)

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
