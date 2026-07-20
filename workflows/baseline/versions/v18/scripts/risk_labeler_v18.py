import re
import math
import pandas as pd
import os

CSV_PATH = os.environ.get("CSV_PATH", r".\data\input\raw_news.csv")
OUT_PATH = os.environ.get("OUT_PATH", r".\reports\predictions\risk_labeler_v3_output.csv")

df = pd.read_csv(CSV_PATH)

if "标题" in df.columns:
    df["标题"] = df["标题"].fillna("").astype(str)
else:
    df["标题"] = ""

df["内容"] = df["内容"].fillna("").astype(str)
df["text"] = (df["标题"] + " " + df["内容"]).str.strip()

PCT_RE = re.compile(r'([+-]?\d+(?:\.\d+)?)\s*%')
AMT_RE = re.compile(
    r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?|\d+)\s*'
    r'(亿美元|亿美金|千万美元|百万美元|万美元|万USDT|万USDC|万美金|美元|USDT|USDC)',
    re.IGNORECASE
)

TIME_HINTS = ["分钟", "小时", "日内", "短时间", "瞬间", "盘中", "24小时", "24h", "今晨", "今日", "当天"]

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
    "并非安全事故", "非安全事故",
    "安全运营", "平安", "安全无虞", "未受影响", "安然无恙"
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

NEG_POSITIVE_MOVE = ["小幅上涨", "微涨", "温和上涨", "企稳回升", "慢牛", "持续走高", "反弹", "回升", "上涨", "涨幅收窄", "回暖", "普涨", "板块轮动", "领涨"]
NEG_STAT_DESC = ["平均", "均值", "中位数", "标准差", "方差", "统计", "概率", "分布", "回归", "相关性"]
NEG_VOL_FORECAST = ["预测", "调查", "预期", "有望", "预计", "展望", "分析报告", "或将", "可能", "可能将", "预判"]

CRYPTO_DOMAIN = ["链上", "智能合约", "DeFi", "defi", "代币", "token", "Token", "TOKEN", "合约地址", "私钥", "钱包", "跨链", "预言机", "DApp", "dApp", "NFT", "nft", "矿工", "区块", "交易哈希", "gas", "GAS", "Gas", "比特币", "以太坊", "加密货币", "数字资产", "虚拟货币", "币"]

NEG_NON_CRYPTO_HACK = ["医疗机构", "医疗数据", "AI", "人工智能", "软件", "企业网络", "网络安全", "数据泄露", "黑客松", "hackathon", "Hackathon", "IT系统", "传统企业", "工业控制系统",
                       "AI交易", "AI agent", "AI Agent", "AI助手", "AI策略", "AI产品", "人工智能交易", "量化交易", "算法交易", "自动化交易", "AI量化", "机器学习", "深度学习", "AI模型"]

NEG_HACK_DOWNWEIGHT = ["追回", "已修复", "完成修复", "报告漏洞", "漏洞赏金", "不受影响", "未受影响", "安然无恙",
                       "无资产损失", "未造成损失", "无资金损失", "已经恢复", "误报", "用户资金未受影响",
                       "不涉及用户资产", "未影响用户", "资金安全"]

NEG_HACK_MITIGATION = ["冻结", "防范", "不受影响", "不会用于", "不涉及", "未受影响", "未涉及", "否认", "澄清",
                       "安全措施", "防范措施", "保护措施", "防御", "不会遭", "不会受到", "未发现损失",
                       "安全运营", "平安", "安全无虞", "未受", "不会影响"]

NEG_HACK_BUSINESS_EXCLUDE = ["收购", "信贷基金", "私人信贷", "贷款协议", "发放贷款", "借贷协议"]

NEG_HACK_PAST = ["之前漏洞", "此前漏洞", "过去的攻击", "曾遭攻击", "之前被黑", "历史漏洞"]

NEG_VULN_REPORT = ["漏洞报告", "漏洞披露", "漏洞挖掘", "安全研究", "渗透测试", "漏洞复现", "漏洞分析",
                   "审计报告", "审计发现", "代码审计", "安全审计", "白帽", "漏洞赏金", "提交漏洞",
                   "安全公告", "安全提升", "安全合作", "共建安全", "白帽联合", "安全响应"]

NEG_HACK_NON_ATTACK = ["增持", "增持了", "买入", "购买", "建仓", "ATM 股票计划", "ATM 发行计划", "财库", "收益生成策略",
                       "理财合作", "利息", "存款", "储蓄", "投资组合", "资产管理", "战略投资",
                       "公司债券", "股票", "回购", "DeFi 收益", "收益池", "流动性挖矿"]

NEG_HACK_STRONG_NO_RISK = ["无资金损失", "未造成损失", "安全运营", "平安无事", "安然无恙", "未受影响",
                           "安全无虞", "未遭攻击", "未发现损失", "一切正常", "正常运行"]

NEG_HACK_SAFETY = ["安全产品", "安全服务", "安全合作", "安全响应", "安全提升", "安全公告", "安全运营", "安全无虞",
                   "推出安全", "发布安全", "上线安全", "安全方案", "安全白皮书", "安全演练", "安全教育",
                   "漏洞研究", "安全研究", "渗透测试", "代码审计", "安全审核", "白帽",
                   "安全运营正常", "无资产损失", "成功抵御", "防御成功", "加固", "安全加固",
                   "安全团队", "安全测试", "安全演习", "安全态势", "安全架构",
                   "AI安全", "AI 安全", "安全AI", "AI防御"]
NEG_HACK_PRODUCT_LAUNCH = ["推出", "上线", "发布", "启动", "新产品", "新功能", "平台升级", "版本发布"]

KW_HACK_BASE = ["攻击", "被盗", "盗取", "重入", "闪电贷", "利用漏洞", "黑客", "exploit", "hacker", "入侵",
                "遭受攻击", "攻击事件", "攻破", "窃取", "损失金额", "被黑", "安全漏洞", "由于漏洞", "黑客攻击",
                "漏洞攻击", "社会工程", "钓鱼攻击", "量子攻击", "后门", "合约暂停", "安全事件", "异常活动",
                "坏账", "安全警钟", "社会工程攻击", "AI攻击", "热钱包被盗", "内部攻击", "凭证泄露",
                "内部热钱包被盗", "量子计算", "密钥恢复", "冻结地址", "截留资金", "漏洞", "资金被盗",
                "桥接被攻击", "冻结黑客地址", "窃取", "特权地址结留"]
KW_HACK_EXTRA = ["利用", "漏洞", "未授权铸造", "资金被盗", "桥接被攻击", "协议被利用", "合约缺陷", "重入漏洞",
                 "预言机攻击", "治理攻击", "私钥泄漏", "价格操纵", "女巫攻击", "51%攻击", "粉尘攻击",
                 "拒绝服务", "DoS", "DDoS", "交易回滚", "双花", "资金丢失", "冻结资产",
                 "非法铸造", "异常提款", "攻击合约", "智能合约漏洞", "闪电贷攻击", "社会工程攻击",
                 "量子攻击", "私钥恢复", "漏洞利用", "损失", "黑客", "入侵",
                 "严重漏洞", "冻结黑客地址", "攻击者地址", "无限期冻结", "用户资金冻结", "提款冻结漏洞",
                 "截留资金", "黑客地址"]
KW_HACK = list(set(KW_HACK_BASE + KW_HACK_EXTRA))

KW_VULN_HIGH = ["严重漏洞", "高危漏洞", "重大安全漏洞", "未授权铸造", "权限提升漏洞", "致命漏洞", "关键漏洞"]

KW_HACK_POST_EVENT = ["冻结黑客地址", "已冻结", "拦截资金", "阻止交易", "成功冻结", "冻结资产", "拦截黑客", "阻断攻击"]

NEG_LIQ_FALSE = ["交易量", "涨幅", "纳入指数", "成交量", "成交额", "交易额", "市值", "总市值", "流通市值"]
NEG_LIQ_PROTECT = ["降低清算风险", "避免清算", "清算保护", "防止爆仓", "降低爆仓风险", "规避清算", "清算预防", "抗清算"]
NEG_LIQ_LIQUIDITY_NON_RISK = ["代币化流动性", "流动性基金", "流动性产品", "清算价", "被动清算", "接近清算", "抵押品使用率", "未实现损失", "接近爆仓", "清算线附近"]

NEG_LIQ_DISCUSSION = ["仓位策略", "仓位管理", "分享", "分析", "观点", "看法", "教程", "如果", "假设", "假如"]
NEG_LIQ_PRODUCT_LAUNCH = ["合约上市", "合约上线", "推出合约", "上线合约"]
NEG_LIQ_MACRO = ["石油", "能源", "原油", "霍尔木兹", "封锁", "地缘", "油运", "能源市场"]

NEG_WHALE_FALSE = ["融资", "市值", "交易量", "牌照", "总市值", "流通市值", "成交额", "交易额", "成交量",
                   "ETF", "ETF基金", "资产管理公司", "贝莱德", "富达", "灰度", "MicroStrategy", "上市公司",
                   "购入", "增持", "持仓", "净资产", "净值", "资产管理", "基金", "信托", "纳入指数",
                   "个人净资产", "亿万富翁", "富豪榜", "声称", "资产组合", "持仓披露", "持有声明", "持仓情况",
                   "赔偿", "退款", "受害者", "受害方"]

NEG_WHALE_PRODUCT_DESC = ["推出", "上线", "平台", "协议", "DeFi", "借贷", "流动性池", "AMM", "质押", "挖矿", "产品"]

NEG_VOL_FALSE = ["活跃地址", "交易量增长", "支持率", "网络活动", "用户增长", "开发者", "TVL", "tvl", "锁仓量",
                 "总市值", "24小时交易量", "占有率达", "占比", "市值排名", "交易量排名", "成交量排名", "市值报告",
                 "收入与利润", "利润增长", "营收增长", "业绩增长", "每股收益", "息税前利润", "财务报告", "业绩", "收益增长",
                 "创新高", "交易量创新高", "新高"]

NEG_VOL_NON_MARKET = ["访谈", "AMA", "分析师指出", "分析师表示", "分析师认为", "观点", "建议", "学习",
                      "人工智能", "AI趋势", "科普", "教育", "指南", "入门", "初学者"]

NEG_VOL_NEUTRAL = ["持仓变动", "解锁代币", "解锁", "代币解锁", "投资于", "买入", "卖出", "增持", "减持", "定投",
                   "长期持有", "持有", "战略投资", "配置于", "纳入投资组合", "资产配置"]

NEG_VOL_TECH = ["金叉", "死叉", "MACD", "RSI", "布林带", "均线", "支撑位", "阻力位", "技术指标", "斐波那契"]

NEG_VOL_DAILY = ["板块上涨", "板块下跌", "涨跌互现", "小幅上涨", "小幅下跌", "微涨", "微跌",
                 "震荡行情", "横盘", "窄幅波动", "技术性反弹", "回调", "修正", "盘整",
                 "行情回顾", "市场总结", "日报", "周报", "快报"]

MAJOR_MARKET_TOKENS = ["BTC", "比特币", "ETH", "以太坊", "主流币", "大盘", "整体市场", "市场全线", "币市", "加密市场", "总市值", "系统风险"]

NEG_REG_FALSE = ["AI", "人工智能", "谷歌", "医疗", "身份验证", "生物识别", "自动驾驶"]
NEG_REGULATORY_POSITIVE = ["解除禁令", "解除制裁", "允许", "不视为", "不构成", "裁定", "不属于证券",
                           "许可", "合法化", "认可", "放行", "批准", "放宽", "放松",
                           "监管明确", "合规指引", "监管框架", "监管沙盒", "试点", "豁免",
                           "胜诉", "驳回", "否决", "撤销指控", "不受影响", "法院驳回"]

NEG_REGULATORY_NEUTRAL = ["推动监管", "寻求监管", "监管进展", "公众咨询", "框架", "指引", "试点", "建议监管", "监管沙盒", "合规指引"]

NEG_REGULATORY_DISCUSS = ["质疑", "反对", "不会影响", "不采取行动", "申请不采取行动", "不影响", "无影响", "未受", "不会",
                          "不予", "否认", "辩驳", "驳斥", "不太可能", "没有计划", "并不意味", "无需", "讨论", "评论", "看法",
                          "表示", "声称", "认为", "分析", "推测"]

REG_STRONG_NEGATIVE = ["罚款", "指控", "起诉", "逮捕", "禁令", "禁止", "冻结", "查封", "判刑", "监禁", "处罚", "制裁", "宣判", "裁决",
                       "诉讼", "集体诉讼", "执法行动", "和解", "被指", "被控", "被起诉"]
REG_MODERATE_SIGNALS = ["调查", "审查", "警告", "批评", "听证会", "关注", "担忧", "压力", "监管阻力", "国会批评", "监管调查",
                        "被调查", "接受调查", "被询问", "调查中"]
REG_WEAK_SIGNALS = ["提案", "草案", "咨询期", "立法", "法规", "政策", "法案", "呼吁", "反对", "合规", "KYC", "反洗钱", "监管框架", "监管进展", "公众咨询"]

REG_CRYPTO_DOMAIN = ["加密", "数字货币", "虚拟货币", "代币", "token", "Token", "TOKEN", "区块链", "比特币", "以太坊", "稳定币", "交易所", "DeFi", "defi", "NFT", "nft"]

KW_FRAUD_EXTRA = ["团队控制", "供应量集中", "内部人士", "钱包持有", "代币集中", "筹码集中", "高度控盘", "操纵市场"]

FRAUD_POST_EVENT = ["赔偿申请", "追回资金", "警方", "逮捕", "判决", "法庭", "量刑", "侦破", "破获"]

KW_VOL_MISS = ["价格波动", "最大痛苦点", "抛售", "回调", "挤压", "逼空", "空头挤压", "多头挤压",
               "价格下调", "大幅下挫", "跳水", "滑落", "急跌", "大跌", "狂跌", "熔断",
               "期权到期", "挤仓", "轧空", "信任危机", "阻力崩溃", "暴力拉升", "暴力下跌",
               "回落至", "恐慌性", "支撑缺失", "升级态势", "情绪波动", "市场动荡", "巨幅震荡",
               "价格回调", "急剧下跌", "急剧上涨", "一夜暴跌", "日内重挫",
               "暴涨", "狂涨", "大跌", "大涨", "飙升", "骤降", "急拉", "巨震",
               "大幅下挫", "大幅上行", "单日跌幅", "单日涨幅", "价格剧烈波动",
               "恐慌性下跌", "恐慌性上涨", "盘中急跌", "盘中急涨"]

KW_VOL_STRONG_SIGNAL = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "恐慌抛售", "恐慌性抛售", "极度恐慌", "流动性枯竭", "瞬间暴跌", "瞬间拉升", "崩盘"]

KW_MACRO_STRONG = ["危机", "冲击", "战争", "军事行动", "紧张局势", "冲突", "制裁", "暴雷", "金融风暴", "崩溃",
                   "地缘冲突", "封锁", "停火", "地缘政治", "霍尔木兹", "海峡", "原油危机", "石油危机"]
KW_MACRO_WEAK = ["美元走强", "美元指数走强", "油价飙升", "美债收益率走高", "加息预期", "风险偏好下降", "避险情绪升温",
                 "通胀", "贬值", "资本管制", "贸易摩擦", "利率决议", "衰退", "经济衰退", "货币政策",
                 "原油", "石油", "能源危机"]

NEG_MACRO_FORECAST = ["预期", "概率", "预计", "不太可能", "可能将", "预测"]

NEG_MACRO_COMMENT = ["分析", "评论", "AMA", "讲话", "采访", "访谈", "观点", "报告", "展望", "讨论", "知乎", "交流",
                     "认为", "声称", "分析称", "研究称", "推测", "预计", "书评", "发言", "对话", "嘉年华",
                     "播客", "在播客中", "专栏", "署名文章"]

NEG_MACRO_POSITIVE = ["缓解", "停火", "开放", "支撑反弹", "上涨推动", "推动反弹", "支持市场", "协议达成",
                      "关系缓和", "解禁", "解除封锁", "恢复出口"]

MACRO_CRYPTO_LINK = ["加密市场", "比特币价格", "数字货币市场", "加密货币", "币价", "比特币", "以太坊", "主流币", "山寨币", "交易所",
                     "币市", "数字资产市场", "加密领域"]

NEG_SOLVENCY_NON_CRISIS = ["代币化", "资产支持代币", "增持", "持仓", "买入", "资产管理", "收益策略", "代币化流动性", "资产代币化"]

USD_UNIT_MULT = {
    "美元": 1.0,
    "美金": 1.0,
    "USDT": 1.0,
    "USDC": 1.0,
    "万美元": 1e4,
    "万USDT": 1e4,
    "万USDC": 1e4,
    "万美金": 1e4,
    "百万美元": 1e6,
    "千万美元": 1e7,
    "亿美元": 1e8,
    "亿美金": 1e8,
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


# ================ 风险评分 ================

def score_hack(text):
    # 强烈否定且无损失直接归零
    if has_any(text, NEG_HACK_STRONG_NO_RISK) and not has_any(text, ["损失", "被盗", "盗取", "资金丢失", "资金消失", "仍受损"]):
        return 0.0

    in_safety_context = has_any(text, NEG_HACK_SAFETY) or has_any(text, NEG_VULN_REPORT)
    has_real_loss = has_any(text, ["资金损失", "实际损失", "被盗", "盗取", "损失金额", "资金丢失", "用户资产受损"])

    if in_safety_context:
        if not has_real_loss:
            upper_bound = 0.2
        else:
            upper_bound = 0.35
        if not has_any(text, ["攻击事件", "被黑", "利用漏洞", "黑客攻击", "漏洞攻击", "遭受攻击", "窃取", "被盗"]):
            return 0.0
    else:
        upper_bound = 1.0

    # 非攻击商业/金融行为直接排除
    if has_any(text, NEG_HACK_NON_ATTACK) and not has_any(text, ["漏洞", "攻击", "被黑", "安全事件", "黑客"]):
        return 0.0

    if has_any(text, NEG_HACK_BUSINESS_EXCLUDE) and not has_any(text, ["漏洞", "攻击", "被黑", "安全事件"]):
        return 0.0

    # 历史语境无损失直接归零
    if has_any(text, NEG_HACK_PAST) and not has_any(text, ["损失", "被盗", "盗取", "资金消失", "资金损失", "攻击事件"]):
        return 0.0

    # 非加密域直接排除
    if has_any(text, NEG_NON_CRYPTO_HACK) and not has_any(text, CRYPTO_DOMAIN):
        return 0.0

    mitigated = has_any(text, NEG_HACK_MITIGATION)

    if has_any(text, KW_HACK):
        usd = extract_usd_equiv(text)
        downweight = has_any(text, NEG_HACK_DOWNWEIGHT)
        has_loss_evid = has_any(text, ["损失", "被盗", "盗取", "资金消失", "资金损失", "被利用", "资金丢失", "仍受损", "未被追回"])
        has_attack_evid = has_any(text, ["攻击事件", "被黑", "安全漏洞", "利用漏洞", "黑客攻击", "漏洞攻击", "遭受攻击", "窃取",
                                         "利用", "漏洞", "未授权铸造", "资金被盗", "桥接被攻击", "攻击合约", "社会工程", "量子攻击",
                                         "量子计算", "冻结黑客地址", "截留资金"])

        # 高位漏洞关键词降权
        if has_any(text, KW_VULN_HIGH):
            score_val = 0.4
            if has_real_loss:
                score_val += 0.10
            if mitigated:
                score_val = min(score_val, 0.35)
            return min(score_val, upper_bound)

        # 事后处理关键词
        if has_any(text, KW_HACK_POST_EVENT):
            score_val = 0.35
            if has_real_loss or usd > 0:
                score_val = 0.45
            if mitigated:
                score_val = min(score_val, 0.30)
            return min(score_val, upper_bound)

        # 防御语境
        if mitigated and not has_loss_evid:
            return min(0.25, 0.12 if downweight else 0.20)

        # 金额损失：降低基础分
        if usd > 0:
            base = 0.75
            score_val = clip01(base + 0.08 * smooth_strength(usd, 1_000_000, 50_000_000))
            if downweight:
                score_val = max(0.30, score_val * 0.55)
            if mitigated:
                score_val = min(score_val, 0.35)
            score_val = max(score_val, 0.50)
            return min(score_val, upper_bound)

        # 明确攻击证据
        if has_attack_evid:
            if downweight and not has_loss_evid:
                base = 0.25
            elif has_loss_evid:
                base = 0.55
            else:
                base = 0.35
            if mitigated:
                base = min(base, 0.30)
            return min(base, upper_bound)

        # 有损失描述但无攻击证据
        if has_loss_evid:
            base = 0.50
            if downweight:
                base = 0.35
            if mitigated:
                base = min(base, 0.35)
            return min(max(base, 0.30), upper_bound)

        # 仅有关键词无证据
        base = 0.20
        if downweight:
            base = 0.12
        if mitigated:
            base = min(base, 0.20)
        return min(base, upper_bound)
    return 0.0


KW_FRAUD = ["诈骗", "骗局", "庞氏", "传销", "跑路", "rug", "rugpull", "钓鱼", "假冒", "冒充", "卷款"]


def score_fraud(text):
    if has_any(text, NEG_FRAUD):
        return 0.0
    if has_any(text, KW_FRAUD):
        if has_any(text, FRAUD_POST_EVENT):
            return 0.35
        return 0.88
    if has_any(text, KW_FRAUD_EXTRA):
        return 0.55
    return 0.0


REG_ACTORS = ["SEC", "CFTC", "司法部", "检察", "法院", "监管", "执法", "警察", "法官", "审计", "调查机构", "税务",
              "央行", "中央银行", "联邦储备", "财政部长", "参议员", "众议员", "国会", "金融监管局", "FSB", "FCA", "DOJ", "FBI"]

REG_WEAK_ACTIONS = ["申请不采取行动", "不采取行动函", "银行家协会质疑稳定币", "监管机构批准", "监管放行",
                    "合规许可", "牌照发放"]


def score_regulatory(text):
    if has_any(text, NEG_REG_FALSE):
        return 0.0

    # 纯讨论/提议且无强负面行动返回0
    if has_any(text, NEG_REG_ONLY_TALK) and not has_any(text, REG_STRONG_NEGATIVE):
        return 0.0

    # 强烈正面/中性结果：无强负面时极低分
    if has_any(text, NEG_REGULATORY_POSITIVE) or has_any(text, ["获得批准", "已获批", "监管部门批准", "监管放行"]):
        if not has_any(text, REG_STRONG_NEGATIVE):
            return 0.05
        return 0.20

    has_strong = has_any(text, REG_STRONG_NEGATIVE)
    discuss_discount = has_any(text, NEG_REGULATORY_DISCUSS)

    if has_any(text, NEG_REGULATORY_NEUTRAL) and not has_strong:
        score_val = 0.02 if discuss_discount else 0.05
        return clip01(score_val)

    # 强负面执法动作
    if has_any(text, REG_ACTORS) and has_strong:
        base = 0.75
        if discuss_discount:
            base = min(base, 0.22)
        return base

    has_crypto = has_any(text, REG_CRYPTO_DOMAIN)
    if has_any(text, REG_MODERATE_SIGNALS):
        if has_crypto:
            base = 0.22 if not discuss_discount else 0.10
        else:
            base = 0.12 if not discuss_discount else 0.06
        return clip01(base)

    has_reg_signal = has_any(text, REG_WEAK_SIGNALS) or has_any(text, ["监管", "政策", "法律"])
    if has_crypto and has_reg_signal:
        base = 0.10
        if discuss_discount:
            base = 0.04
        return clip01(base)
    if has_reg_signal:
        base = 0.03 if discuss_discount else 0.06
        return clip01(base)

    if has_crypto and has_any(text, REG_WEAK_ACTIONS):
        base = 0.12 if not discuss_discount else 0.05
        return clip01(base)

    if has_any(text, ["法案", "草案", "国会批评", "监管阻力"]) and has_crypto:
        base = 0.08
        if discuss_discount:
            base = 0.03
        return clip01(base)

    return 0.0


KW_OUTAGE = ["暂停提现", "暂停充提", "暂停充值", "提现暂停", "充值暂停", "维护", "钱包维护", "宕机", "系统故障", "无法交易", "停止充提", "网络拥堵"]
KW_OUTAGE_EXTRA = ["冷钱包无法访问", "私钥未移交", "资产无法访问", "提款异常", "提现延迟", "充值延迟", "无法提现", "无法充值", "访问异常", "登录异常", "API异常"]


def score_outage(text):
    outage_keywords = KW_OUTAGE + KW_OUTAGE_EXTRA
    if not has_any(text, outage_keywords):
        return 0.0

    # 明显的计划维护、升级公告大幅压低
    if has_any(text, NEG_PLANNED_MAINT) or has_any(text, ["按计划", "升级公告", "通知"]):
        if "暂停提现" in text or "停止充提" in text or "暂停充提" in text:
            return 0.25
        return 0.10

    if has_any(text, NEG_NO_RISK):
        if "暂停提现" in text or "停止充提" in text or "暂停充提" in text:
            return 0.30
        return 0.15

    if has_any(text, ["暂停提现", "暂停充提", "停止充提", "提现暂停", "充值暂停", "无法提现", "无法充值"]):
        return 0.65

    if has_any(text, ["宕机", "系统故障", "无法交易"]):
        return 0.62

    if has_any(text, ["提现延迟", "充值延迟", "访问异常", "登录异常", "API异常"]):
        return 0.40

    return 0.50


STABLES = ["USDT", "USDC", "DAI", "FDUSD", "TUSD", "UST", "USDD", "FRAX", "PYUSD", "稳定币"]
STABLE_HINTS = ["脱锚", "锚定", "锚离", "peg", "depeg", "跌破1美元", "跌破 1 美元", "回到1美元"]
STABLE_NUM = re.compile(r'\b0\.9\d\b|\b1\.0\d\b')


def score_stablecoin(text):
    if not has_any(text, STABLES):
        return 0.0
    if has_any(text, STABLE_HINTS) or STABLE_NUM.search(text or ""):
        return 0.85
    return 0.0


KW_LIQ = ["爆仓", "强平", "清算", "liquidation", "强制平仓"]
KW_LIQ_WEAK = ["期权集中到期", "负资金费率", "空头挤压", "逼空", "清算风险", "爆仓风险", "强平风险", "清算压力"]
KW_LIQ_STRONG = ["清算强度", "清算金额", "将清算", "发生清算", "已清算", "实际清算"]


def score_liquidation(text):
    if has_any(text, NEG_LIQ_MACRO) and not has_any(text, ["爆仓", "强平", "已清算", "实际清算", "发生清算"]):
        return 0.0

    if has_any(text, NEG_LIQ_PROTECT):
        return 0.0
    if has_any(text, NEG_LIQ_FALSE):
        return 0.0

    if has_any(text, NEG_LIQ_DISCUSSION) and not has_any(text, ["爆仓", "强平", "已清算", "实际清算", "发生清算"]):
        return 0.0

    if has_any(text, NEG_LIQ_PRODUCT_LAUNCH) and not has_any(text, ["爆仓", "强平"]):
        return 0.0

    if has_any(text, NEG_LIQ_LIQUIDITY_NON_RISK):
        if has_any(text, KW_LIQ):
            return 0.15
        return 0.0

    has_strong_liq = has_any(text, KW_LIQ_STRONG)
    if has_any(text, KW_LIQ):
        usd = extract_usd_equiv(text)
        if usd > 0:
            if usd < 500_000:
                score_val = clip01(0.30 + 0.12 * smooth_strength(usd, 100_000, 500_000))
            elif usd < 5_000_000:
                score_val = clip01(0.40 + 0.18 * smooth_strength(usd, 500_000, 5_000_000))
            else:
                score_val = clip01(0.45 + 0.25 * smooth_strength(usd, 5_000_000, 20_000_000))
            return min(score_val, 0.65)
        if has_strong_liq:
            return 0.50
        return 0.38
    if has_any(text, KW_LIQ_WEAK):
        return 0.12
    return 0.0


KW_WHALE = ["巨鲸", "whale", "转入", "转出", "转入交易所", "转出交易所", "从匿名地址", "从未知地址", "链上", "地址", "钱包转移", "大额转账",
            "准备出售", "抛售", "转移至交易所准备卖出", "大户准备卖出", "鲸鱼卖出", "大户", "巨鲸地址", "大额转移", "巨额转账",
            "巨额转移", "大额交易", "大额资金", "大额资产", "持仓变动", "大户头寸",
            "大额转入", "大额转出", "未知钱包", "unknown wallet", "大量抛售", "巨量抛售",
            "链上异动", "异常转账"]
KW_WHALE_BEHAVIOUR = ["准备出售", "抛售", "清仓", "接近清仓", "大量解锁", "巨量的卖单", "巨量的买单", "大户出售", "大额卖单", "代币解锁", "巨额解锁",
                       "巨鲸开仓", "大户开仓", "大户头寸", "准备减持", "出售", "减持", "鲸鱼减持", "仓位转移", "持仓转移",
                       "大型持有者准备出售", "鲸鱼持有", "持有者准备出售", "大额砸盘",
                       "解锁代币", "大额解锁", "大量代币解锁", "代币解锁"]


def score_whale(text):
    if has_any(text, NEG_WHALE_FALSE):
        return 0.0

    position_discount = 1.0
    if has_any(text, ["开仓", "平仓", "多头", "空头", "仓位调整", "头寸", "杠杆", "止盈", "止损"]) and not has_any(text, KW_WHALE_BEHAVIOUR):
        position_discount = 0.4

    whale_keywords = KW_WHALE + KW_WHALE_BEHAVIOUR
    if not has_any(text, whale_keywords):
        return 0.0

    if not has_any(text, ["转入", "转出", "转移", "发送", "从地址", "链上", "地址", "巨鲸", "whale", "大户", "鲸鱼", "持仓变动"] + KW_WHALE_BEHAVIOUR):
        return 0.0

    if has_any(text, NEG_WHALE_PRODUCT_DESC) and not has_any(text, ["地址", "交易哈希", "txid", "TXID"]):
        return 0.0

    future_discount = 1.0
    if has_any(text, ["计划", "即将", "预期", "可能", "或将", "潜在", "准备"]) and not has_any(text, ["已经", "已", "确认", "完成"]):
        future_discount = 0.3

    if has_any(text, NEG_INTERNAL_TRANSFER) or has_any(text, NEG_NO_RISK):
        usd = extract_usd_equiv(text)
        if usd > 50_000_000:
            return 0.10
        return 0.02

    usd = extract_usd_equiv(text)
    # 行为触发降权
    if has_any(text, KW_WHALE_BEHAVIOUR):
        base = 0.35
        if usd > 1_000_000:
            base = clip01(0.35 + 0.12 * smooth_strength(usd, 1_000_000, 20_000_000))
        elif usd > 0:
            base = 0.28
        else:
            base = 0.22
        return base * future_discount * position_discount

    # 非行为触发
    if usd < 1_000_000:
        return 0.05 * future_discount * position_discount
    return clip01(0.15 + 0.20 * smooth_strength(usd, 1_000_000, 100_000_000)) * future_discount * position_discount


KW_SHOCK = ["闪崩", "插针", "瀑布", "腰斩", "暴跌", "剧烈波动", "瞬间暴跌", "瞬间拉升"]

def score_volatility(text):
    # 必须与加密领域相关
    if not has_any(text, CRYPTO_DOMAIN):
        return 0.0

    # 如果文本含有强信号词，忽略部分抑制
    has_strong_signal = has_any(text, KW_VOL_STRONG_SIGNAL + ["暴跌", "闪崩", "瀑布", "腰斩", "插针"])

    # 日常短语抑制：无强信号时扣分，但不禁用
    if has_any(text, NEG_VOL_DAILY) and not has_strong_signal:
        daily_discount = 0.5
    else:
        daily_discount = 1.0

    # 市场回顾/周报等，无极端词大幅压制
    if has_any(text, ["市场回顾", "行情综述", "每日行情", "周报", "周评", "市场总结", "日报", "快报"]) and not has_strong_signal:
        return 0.0

    if has_any(text, NEG_VOL_NON_MARKET) and not has_any(text, KW_SHOCK + KW_VOL_MISS):
        return 0.0

    if has_any(text, NEG_VOL_NEUTRAL) and not has_any(text, KW_SHOCK + ["暴跌", "闪崩", "瀑布", "腰斩", "插针"]):
        return 0.0

    if has_any(text, ["收入与利润", "利润增长", "营收增长", "业绩增长", "每股收益", "息税前利润"]):
        if not has_strong_signal:
            return 0.0

    if has_any(text, NEG_VOL_TECH) and not has_any(text, KW_SHOCK + ["暴跌", "闪崩"]):
        return 0.0

    # 预测类抑制，但强信号仍然放行
    if has_any(text, NEG_VOL_FORECAST) and not has_strong_signal:
        return 0.0

    if has_any(text, NEG_VOL_FALSE):
        return 0.0

    pct = extract_max_pct(text)
    has_time = has_any(text, TIME_HINTS)
    has_softener = has_any(text, NEG_NO_RISK)

    if has_any(text, NEG_POSITIVE_MOVE) and not has_strong_signal and not has_any(text, KW_VOL_MISS + KW_SHOCK):
        return 0.0
    if has_any(text, NEG_STAT_DESC):
        return 0.0

    is_major = has_any(text, MAJOR_MARKET_TOKENS)
    major_factor = 1.0 if is_major else 0.3

    # 强波动信号独立高分
    if has_any(text, KW_VOL_STRONG_SIGNAL):
        base = 0.55 if has_softener else 0.70
        if has_time:
            base += 0.05
        score_val = clip01(base + 0.15 * smooth_strength(pct, 10, 20))
        return score_val * major_factor * daily_discount

    # 普通强波动关键词
    if has_any(text, KW_SHOCK):
        base = 0.48 if has_softener else 0.62
        if has_time:
            base += 0.05
        score_val = clip01(base + 0.20 * smooth_strength(pct, 15, 25))
        return score_val * major_factor * daily_discount

    # 漏召回关键词
    if has_any(text, KW_VOL_MISS):
        base = 0.36 if has_softener else 0.48
        if has_time:
            base += 0.05
        if pct > 0 or has_time:
            score_val = clip01(base + 0.12 * smooth_strength(pct, 10, 20))
        else:
            base = 0.22 if not has_softener else 0.16
            score_val = base
        return max(score_val * major_factor * daily_discount, 0.08)

    # 仅百分比触发
    if pct >= 50:
        base = 0.30 if has_softener else 0.38
        if has_time:
            base += 0.05
        score_val = clip01(base)
        return max(score_val * major_factor * daily_discount, 0.08)
    if pct >= 30:
        base = 0.18 if has_softener else 0.25
        if has_time:
            base += 0.05
        score_val = clip01(base + 0.30 * smooth_strength(pct, 30, 50))
        return max(score_val * major_factor * daily_discount, 0.08)
    if pct >= 25:
        base = 0.10 if has_softener else 0.15
        if has_time:
            base += 0.05
        score_val = clip01(base + 0.25 * smooth_strength(pct, 25, 45))
        return max(score_val * major_factor * daily_discount, 0.08)
    if pct >= 20:
        base = 0.06 if has_softener else 0.10
        if has_time:
            base += 0.05
        score_val = clip01(base + 0.20 * smooth_strength(pct, 20, 35))
        return max(score_val * major_factor * daily_discount, 0.08)
    if pct >= 15:
        base = 0.04 if has_softener else 0.08
        if has_time:
            base += 0.05
        score_val = clip01(base + 0.15 * smooth_strength(pct, 15, 25))
        return max(score_val * major_factor * daily_discount, 0.08)

    return 0.0


KW_TEAM = [
    "创始人失联", "团队失联", "删除社交媒体账号", "删除账号", "官网无法访问",
    "官网无法打开", "停止运营", "停更", "项目方失联", "团队突然解散",
    "官方失联", "核心成员离职", "多签异常", "治理攻击", "内部操纵",
    "创始人套现", "团队内讧", "核心成员被捕", "CEO离职", "CEO辞职",
    "联合创始人离开", "开发团队退出", "团队资金耗尽", "内部腐败", "项目方出货",
    "团队抛售", "项目方砸盘", "团队大量减持", "代币倾销", "退出骗局",
    "代币增发", "项目方减持", "多签异常", "合约销毁异常", "内部抛售",
    "团队解散", "控制权变更", "创始人被调查", "项目方跑路", "团队解体",
    "CEO被捕", "创始人被捕", "管理层被捕", "高层动荡", "内部斗争", "项目方争议",
    "创始人辞职"
]
KW_TEAM_WEAK = ["高管离职", "项目停滞", "转型受阻", "团队变动", "人事调整", "治理争议", "投票率低", "提案失败", "人事震荡",
                "项目代币集中", "团队争议", "管理层震荡", "项目负责人离职"]


def score_team(text):
    if has_any(text, KW_TEAM):
        return 0.80
    if has_any(text, KW_TEAM_WEAK):
        return 0.45
    return 0.0


KW_SOLV = [
    "偿付能力", "兑付", "挤兑", "储备不足", "储备透明度不足", "流动性危机",
    "流动性不足", "资不抵债", "现金流压力", "负债", "财务困境", "无法兑付",
    "抵押品使用率过高", "接近清算线", "未实现损失", "抵押品接近上限",
    "坏账", "准备金率", "无法提取", "暂停赎回", "赎回暂停", "资不抵债",
    "偿付危机", "储备金不足", "流动性枯竭", "资金缺口", "挤兑风险", "抵押品不足",
    "抵押池不足", "暂停提款", "无法偿还", "违约"
]


def score_solvency(text):
    if has_any(text, NEG_SOLVENCY_NON_CRISIS):
        if not has_any(text, ["挤兑", "资不抵债", "储备不足", "无法提取", "违约", "偿付危机"]):
            return 0.0

    if has_any(text, KW_SOLV):
        if has_any(text, STABLES) or has_any(text, ["发行方", "储备资产"]):
            return 0.88
        return 0.78
    if has_any(text, ["流动性"]) and has_any(text, ["风险", "紧张", "不足", "危机", "压力"]):
        return 0.12
    if has_any(text, ["偿付", "偿债", "储备"]) and has_any(text, ["不足", "危机", "风险", "缺口"]):
        return 0.12
    return 0.0


KW_INFRA = [
    "跨链桥异常", "跨链桥故障", "预言机异常", "预言机失灵", "停止出块",
    "分叉异常", "共识失败", "RPC故障", "节点故障", "主网故障", "网络停止",
    "网络中断", "共识问题", "分叉风险", "节点宕机", "出块停止", "链停止",
    "手续费异常", "手续费调至", "Gas费飙升", "预言机故障", "预言机价格异动",
    "区块重组", "分叉攻击", "共识攻击", "跨链桥暂停", "节点掉线", "主网暂停",
    "协议暂停", "智能合约暂停", "L2故障", "侧链故障", "预言机操纵", "Gas异常",
    "网络中断", "通信故障", "升级事故", "分叉争议",
    "网络分裂", "链上拥堵", "TPS暴跌", "验证者离线", "网络分区",
    "网络分区", "验证者退出", "节点退出"
]

KW_INFRA_WEAK = ["网络延迟", "Gas飙升", "节点响应慢", "状态不同步", "出块间隔延长", "交易未确认"]


def score_infra(text):
    if has_any(text, KW_INFRA):
        if has_any(text, NEG_NO_RISK):
            return 0.20
        return 0.72
    if has_any(text, KW_INFRA_WEAK):
        return 0.28
    return 0.0


MACRO_ALL = KW_MACRO_STRONG + KW_MACRO_WEAK


def score_macro(text):
    if not has_any(text, MACRO_ALL):
        return 0.0
    # 纯评论/观点/预测，非强冲击事件直接排除
    if has_any(text, NEG_MACRO_COMMENT) and not has_any(text, KW_MACRO_STRONG):
        return 0.0

    linked = has_any(text, MACRO_CRYPTO_LINK)
    strong = has_any(text, KW_MACRO_STRONG)
    has_positive = has_any(text, NEG_MACRO_POSITIVE)

    if not linked:
        if strong:
            if has_any(text, NEG_MACRO_COMMENT):
                return 0.0
            base = 0.18
            if has_any(text, NEG_MACRO_FORECAST):
                base *= 0.4
            if has_positive:
                base *= 0.2
            return clip01(base + 0.03 * smooth_strength(extract_max_pct(text), 5, 15))
        else:
            return 0.0

    # 有加密关联
    if has_any(text, NEG_MACRO_COMMENT) and not strong:
        return 0.0

    pct = extract_max_pct(text)
    has_forecast = has_any(text, NEG_MACRO_FORECAST)

    if strong:
        base = 0.45
        if has_forecast:
            base *= 0.4
        if has_positive:
            base *= 0.25
        return clip01(base + 0.10 * smooth_strength(pct, 3, 10))

    base = 0.12
    if has_forecast:
        base *= 0.30
    if has_positive:
        base *= 0.15
    score_val = clip01(base + 0.04 * smooth_strength(pct, 5, 15))
    return min(score_val, 0.15)


# ================ 风险配置 ================
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

TYPE_THRESHOLD = 0.30
PRIMARY_TYPE_MIN = 0.08


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
    sorted_scores = sorted(raw_scores.items(), key=lambda x: x[1], reverse=True)
    max_name, max_val = sorted_scores[0]

    primary_type = "无明显风险"
    if max_val >= PRIMARY_TYPE_MIN:
        # 竞争规则：如果最高分是hack且不高，且regulatory有一定分数且有强执法词，优先选regulatory
        if max_name == "score_hack" and max_val < 0.6:
            reg_val = raw_scores["score_regulatory"]
            if reg_val >= 0.18 and (max_val - reg_val) < 0.3:
                primary_type = RISK_NAME_MAP["score_regulatory"]
            else:
                # 若无 regulatory 抢占，但 volatility 也接近，考虑提升 volatility
                vol_val = raw_scores["score_volatility"]
                if vol_val >= 0.12 and (max_val - vol_val) < 0.25:
                    primary_type = RISK_NAME_MAP["score_volatility"]
                else:
                    primary_type = RISK_NAME_MAP[max_name]
        else:
            primary_type = RISK_NAME_MAP[max_name]
    else:
        primary_type = "无明显风险"

    effective_risk = int(round(max_val * 100))
    effective_label = score_to_label(max_val) if max_val > 0 else "low"

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
