from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


PROCESS_DIR = Path(__file__).resolve().parent
INPUT_PATH = PROCESS_DIR / "output" / "human_review_priority_119.csv"
OUTPUT_PATH = PROCESS_DIR / "output" / "codex_review_priority_119.csv"

ATTACK = "链上漏洞 / 攻击风险"
SCAM = "诈骗 / 跑路 / Rug Pull 风险"
REGULATORY = "监管与法律风险"
EXCHANGE_OPS = "交易所与系统运维风险"
STABLECOIN = "稳定币异常风险"
LIQUIDATION = "爆仓 / 清算风险"
WHALE = "大额转账 / 巨鲸行为风险"
VOLATILITY = "异常行情波动风险"
GOVERNANCE = "项目治理 / 团队异常风险"
LIQUIDITY = "偿付能力 / 储备 / 流动性风险"
INFRA = "基础设施 / 协议层异常风险"
MACRO = "宏观 / 政策冲击风险"
NO_RISK = "无明显风险"


# 每条记录均由 Codex 根据新闻正文独立复核。这里保留逐条决定，便于审计和回放。
# tuple: (0-100 score, risk_types, primary_risk_type, review_reason)
DECISIONS: dict[int, tuple[int, list[str], str, str]] = {
    373: (40, [ATTACK], ATTACK, "Rhea Finance 黑客事件已经发生，冻结资金降低了后续损失，但不能消除已发生的链上攻击风险。"),
    156: (90, [ATTACK], ATTACK, "新闻明确包含 Kelp DAO 遭攻击、约 2.92 亿美元损失及多链合约暂停，属于已发生的重大链上安全事件。"),
    158: (85, [ATTACK, LIQUIDITY], ATTACK, "Kelp DAO 攻击已造成约 2.92 亿美元损失并令 Aave 形成坏账，兼具攻击和偿付流动性风险。"),
    189: (30, [LIQUIDATION], LIQUIDATION, "一小时内实际清算 2930 万美元且单一代币清算额突出，构成轻度但已发生的清算风险。"),
    355: (60, [GOVERNANCE, INFRA], GOVERNANCE, "网络已经停止运营，用户若未按期迁移资产将无法找回，属于明确的项目停运和基础设施风险。"),
    510: (40, [ATTACK, SCAM], ATTACK, "域名曾被劫持并部署钓鱼站窃取签名和凭证；虽已恢复，安全事件本身已经发生。"),
    800: (45, [ATTACK, SCAM], ATTACK, "季度内 43 起攻击和欺诈造成 4.645 亿美元实际损失，反映已经发生的行业安全与诈骗风险。"),
    80: (30, [ATTACK], ATTACK, "AI 工具漏洞可能泄露 Web3 钱包前端凭证，尚无实际损失，因此定为轻度潜在攻击风险。"),
    82: (45, [LIQUIDITY], LIQUIDITY, "多个借贷市场利用率升至约 96% 至 99%，可用流动性明显收紧，已形成可观察的流动性压力。"),
    89: (0, [], NO_RISK, "按计划披露的代币解锁尚未伴随异常抛售或价格冲击，正文没有足够事实支持风险类别。"),
    154: (45, [ATTACK], ATTACK, "rsETH 漏洞事件仍在修复和根因调查中，风险正在受控但尚不能视为从未发生。"),
    260: (35, [LIQUIDATION, WHALE], LIQUIDATION, "单一大额地址已因高杠杆空头亏损 539 万美元并卖出 1184 枚 BTC 规避清算，属于轻度清算与巨鲸风险。"),
    482: (30, [WHALE], WHALE, "大型持有者在关键区域准备出售是明确的巨鲸供给风险信号，但尚未发生实际集中抛售。"),
    874: (20, [VOLATILITY], VOLATILITY, "价格下跌约 4% 并伴随认沽溢价和恐慌情绪，属于轻度行情风险而非严重异常。"),
    900: (60, [ATTACK], ATTACK, "新闻仍明确披露 Drift 遭攻击损失约 2.8 亿美元及冻结争议，重大攻击事实不能因回应声明而归零。"),
    159: (50, [LIQUIDITY, GOVERNANCE], LIQUIDITY, "ACI 因 wETH 短缺和坏账风险立即终止服务并全额退出，表明真实的流动性压力触发了治理措施。"),
    400: (45, [INFRA], INFRA, "BIP-361 可能永久冻结约 170 万枚 BTC，虽处提案阶段但影响规模巨大，构成协议层风险。"),
    674: (50, [ATTACK], ATTACK, "攻击资金已全部转入 Tornado Cash，说明攻击确实发生且追回难度上升，金额较小使风险维持中等。"),
    882: (35, [VOLATILITY], VOLATILITY, "两家大型持仓主体出现数十亿美元未实现亏损，尚未违约但已形成显著的市场敞口风险。"),
    966: (50, [LIQUIDATION, WHALE], LIQUIDATION, "6700 枚 ETH 的 25 倍空单距离清算仅 9 美元，构成明确且规模较大的清算与巨鲸风险。"),
    998: (35, [LIQUIDATION], LIQUIDATION, "关键价位对应 5 亿至 12 亿美元潜在清算，尚未触发但风险规模明确。"),
    381: (65, [ATTACK], ATTACK, "约 100 名朝鲜 IT 工人渗透 53 个项目并发现数百漏洞，属于范围较广的现实安全威胁。"),
    229: (65, [VOLATILITY], VOLATILITY, "RAVE 一周暴涨 1415% 且市值排名骤升，幅度远超常规市场波动，属于明显异常行情。"),
    14: (30, [REGULATORY], REGULATORY, "BIS 高层公开警告大型稳定币可能威胁金融稳定，虽未出台措施但构成明确的监管风险信号。"),
    54: (50, [ATTACK], ATTACK, "合约疑因访问控制缺陷遭攻击并损失约 25.7 万美元，事件尚称疑似且金额有限，定为中等风险。"),
    57: (35, [LIQUIDITY], LIQUIDITY, "多家借贷和收益协议 TVL 出现两位数下降，已反映资金流出和轻度流动性压力。"),
    74: (0, [], NO_RISK, "正文描述的是针对个人的实体犯罪，现有加密风险分类中没有合适类别，且尚无行业监管措施落地。"),
    88: (80, [ATTACK, LIQUIDITY], ATTACK, "rsETH 事件已经产生潜在 18.5% 减值和损失分配难题，兼具重大攻击与偿付风险。"),
    225: (55, [LIQUIDATION], LIQUIDATION, "BTC 与 ETH 在 24 小时内实际清算约 5.45 亿美元，属于已经发生的中等规模清算风险。"),
    293: (55, [SCAM], SCAM, "攻击者正以伪造协作软件实施社交工程并窃取用户凭证，属于正在发生的明确诈骗攻击活动。"),
    349: (35, [LIQUIDATION], LIQUIDATION, "ETH 关键价位两侧均对应接近 9 亿美元潜在清算，尚未触发但规模值得标注。"),
    358: (35, [MACRO, STABLECOIN], MACRO, "美国国债需求崩溃虽属尾部情景，但可能冲击全球资产定价并传导至 Tether 储备和脱锚风险。"),
    441: (30, [LIQUIDITY], LIQUIDITY, "1.275 亿美元外部资金被用于受影响用户恢复和赔付，说明此前存在资金缺口，但支持降低了风险程度。"),
    687: (45, [ATTACK], ATTACK, "Zerion 内部热钱包已被盗 10 万美元且 Web 应用临时下线，用户资金未受影响使风险保持中等。"),
    724: (35, [WHALE], WHALE, "350 亿 USDT 从未知钱包流入 Aave 的规模极大，虽无负面后果证据，仍构成明显的大额转账信号。"),
    953: (55, [LIQUIDITY, GOVERNANCE], LIQUIDITY, "WLFI 占平台绝大多数 TVL 和借贷并采用循环抵押，集中度和接近上限的杠杆形成中等流动性风险。"),
    166: (50, [REGULATORY], REGULATORY, "OFAC 已涉及 518 个比特币地址且法案拟进一步扩大冻结和监控权力，属于现实监管风险。"),
    1: (35, [LIQUIDATION, VOLATILITY], LIQUIDATION, "79 亿美元期权集中到期、负资金费率和潜在空头挤压共同构成轻度清算与波动风险。"),
    24: (25, [WHALE], WHALE, "早期贡献者开立约 119 万美元的 5 倍空单属于可观察的大户行为，但没有证据支持项目治理异常。"),
    45: (0, [], NO_RISK, "未来代币解锁属于预定供应事件，正文没有实际抛售、价格异常或其他负面后果。"),
    70: (40, [LIQUIDITY], LIQUIDITY, "Relay 金库当前无法处理提款，虽有手动方案且核心产品正常，仍已构成实际提款流动性风险。"),
    114: (35, [LIQUIDATION], LIQUIDATION, "BTC 关键价位对应约 9.5 亿美元潜在清算，尚未发生但风险规模明确。"),
    176: (45, [REGULATORY], REGULATORY, "伊朗大规模使用加密资产且美国财政部实施冻结，新闻包含已经发生的制裁和执法风险。"),
    180: (25, [MACRO], MACRO, "潜在美联储主席的紧缩立场可能影响比特币和加密银行准入，尚未上任使风险维持轻度。"),
    194: (40, [LIQUIDATION], LIQUIDATION, "BTC 两侧关键价位对应 15 亿至 20 亿美元潜在清算，规模较大，定为中等条件性风险。"),
    203: (60, [VOLATILITY], VOLATILITY, "HIGH 在 24 小时上涨 315%，幅度显著偏离常规交易区间，属于明确异常行情。"),
    385: (50, [GOVERNANCE, INFRA], GOVERNANCE, "Foundation 因出售失败永久关闭平台并要求用户撤回 NFT，属于项目停运和基础设施中断风险。"),
    423: (20, [REGULATORY], REGULATORY, "案件涉及加密货币洗钱、资产扣押和多项逮捕令，对行业形成轻度法律与合规风险。"),
    477: (30, [EXCHANGE_OPS], EXCHANGE_OPS, "币安将停止特定网络充值提现且错误充值可能造成资产损失，属于提前公告的轻度运营风险。"),
    568: (25, [VOLATILITY], VOLATILITY, "SHIB 一年下跌 52% 并存在进一步下跌预测，属于持续市场弱势但不是短时极端波动。"),
    642: (40, [ATTACK], ATTACK, "Aethir 已完成漏洞评估并启动补偿，说明安全事件和用户影响确实发生，恢复运营降低了当前风险。"),
    710: (20, [INFRA], INFRA, "CoW Swap 前端此前发生错误并需限定合约地址，虽已恢复，仍属于轻度基础设施安全事件。"),
    718: (40, [LIQUIDATION], LIQUIDATION, "BTC 关键价位对应 16 亿至 27 亿美元潜在清算，规模巨大，构成中等条件性风险。"),
    719: (40, [LIQUIDATION], LIQUIDATION, "ETH 关键价位对应 10 亿至 14 亿美元潜在清算，虽未触发但风险规模较大。"),
    760: (35, [REGULATORY], REGULATORY, "逾 50 起暴力犯罪均使用加密支付且已有大规模调查逮捕，构成轻度监管与法律风险。"),
    770: (30, [MACRO], MACRO, "战争后全球基金经理对经济前景显著转悲观，虽未到恐慌程度，仍是明确的宏观风险信号。"),
    805: (50, [REGULATORY], REGULATORY, "已出现要求 FCA 调查市场滥用的正式函件，政府亦推进暂停加密捐赠，监管风险并非单纯观点。"),
    951: (20, [WHALE], WHALE, "单一地址持有约 6700 万美元多空仓位，属于轻度大户敞口，但暂无异常交易后果。"),
    967: (65, [MACRO, LIQUIDATION], MACRO, "美伊谈判破裂和海峡封锁威胁已导致 BTC 下跌及约 3.5 亿美元清算，宏观冲击已传导至市场。"),
    979: (25, [VOLATILITY], VOLATILITY, "ETH 24 小时下跌 5.06%，属于轻度市场波动风险，尚未达到严重异常程度。"),
    997: (25, [REGULATORY], REGULATORY, "制裁和交易对手风险正推动银行退出部分贸易融资，稳定币承接受限资金流带来轻度合规风险。"),
    143: (30, [INFRA], INFRA, "Morpho 已实际暂停 Arbitrum 跨链功能且恢复时间取决于外部事件根因，属于轻度协议可用性风险。"),
    222: (35, [INFRA, ATTACK], INFRA, "研究显示量子攻击可能威胁约 690 万枚公钥已暴露的 BTC，能力尚未落地但协议层潜在影响巨大。"),
    120: (40, [REGULATORY], REGULATORY, "金融法草案拟扩大查记录、冻结扣押资金和限制出境的权力，构成明确的监管政策风险。"),
    18: (70, [VOLATILITY], VOLATILITY, "SKYAI 在短时间内暴跌超过 50%，属于已经发生的极端异常行情并直接影响持有者。"),
    21: (30, [LIQUIDATION], LIQUIDATION, "BTC 关键价位对应约 1.9 亿至 2.9 亿美元潜在清算，属于轻度条件性清算风险。"),
    79: (50, [INFRA], INFRA, "Vercel 的 Workspace 连接已被攻破且攻击者提升内部访问权限，虽无敏感变量访问证据，仍是中等基础设施风险。"),
    83: (25, [REGULATORY], REGULATORY, "韩国数字资产立法再次延迟且交易所股权条款存在违宪争议，形成轻度监管不确定性。"),
    97: (35, [ATTACK], ATTACK, "行业报告结合 Kelp 漏洞指出 DeFi 单点故障和高频攻击趋势，属于轻度但有事实背景的安全风险。"),
    116: (0, [], NO_RISK, "新闻明确说明所有贷款抵押安全且没有即时清算风险，只有利率骤升这一条件性例外。"),
    135: (60, [LIQUIDITY], LIQUIDITY, "rsETH 贷款可能削减 10% 至 15% 且覆盖约 16.5% ETH 市场，流动性压力规模显著。"),
    139: (25, [INFRA], INFRA, "Morpho 自身合约安全，但两个市场存在约 100 万美元敞口且跨链桥已暂停，属于轻度可用性风险。"),
    151: (20, [INFRA], INFRA, "Sky 预防性暂停 USDS 跨链功能，资产保持足额抵押，因此仅标注轻度协议可用性风险。"),
    235: (0, [], NO_RISK, "法院裁定 JENNER 不属于证券，当前判决降低而非增加监管风险，剩余州法诉求尚无明确负面结果。"),
    261: (20, [WHALE], WHALE, "巨鲸存在 1550 万美元未实现亏损但整体仍盈利且未被清算，仅构成轻度大户仓位风险。"),
    272: (25, [MACRO], MACRO, "海峡当前开放且市场反应积极，但停火即将到期，仍保留轻度地缘宏观不确定性。"),
    348: (40, [LIQUIDATION], LIQUIDATION, "BTC 关键价位对应约 12 亿至 13 亿美元潜在清算，属于中等条件性风险。"),
    375: (55, [VOLATILITY], VOLATILITY, "SIREN 24 小时上涨 154.1% 且盘中快速回落，属于明确的异常行情波动。"),
    448: (55, [ATTACK, LIQUIDITY], ATTACK, "DeFi 收益下降、活动放缓并伴随近期 2.85 亿美元黑客事件，安全和流动性压力均有事实支撑。"),
    483: (40, [SCAM], SCAM, "全球网络犯罪损失创 208 亿美元新高且投资诈骗和加密犯罪突出，属于已发生的行业诈骗风险。"),
    535: (35, [LIQUIDATION], LIQUIDATION, "ETH 关键价位对应约 6 亿至 11 亿美元潜在清算，属于轻度条件性清算风险。"),
    641: (25, [WHALE], WHALE, "不丹政府出售价值约 1846 万美元 BTC，属于可观察的大额持有者卖出，但规模不足以构成严重冲击。"),
    660: (35, [WHALE, GOVERNANCE], WHALE, "最大多头持有 6160 万美元杠杆仓位并反复大额提现，内幕指控未证实，故定为轻度巨鲸与治理风险。"),
    744: (30, [WHALE], WHALE, "单一大户平仓价值 2.27 亿美元多单并仍持巨额 ETH 多仓，属于明确但可控的巨鲸市场行为。"),
    752: (25, [REGULATORY], REGULATORY, "JPMorgan 高管警告稳定币可能规避银行标准和客户保护，构成轻度监管风险信号。"),
    827: (40, [REGULATORY], REGULATORY, "官方媒体重申虚拟货币活动属于非法金融活动并批评推广交易，反映现实且持续的监管风险。"),
    905: (45, [LIQUIDITY], LIQUIDITY, "Strategy 杠杆率约 33%、资本结构压力增加且股价显著弱于 BTC，构成中等偿付与流动性风险。"),
    914: (50, [MACRO], MACRO, "美伊谈判失败已推高油价并压低风险偏好和 ETH 价格，宏观冲击已实际传导至加密市场。"),
    972: (35, [MACRO], MACRO, "战争和高利率可能持续压制加密市场，虽为专家预测但有明确宏观事件作为基础。"),
    152: (85, [LIQUIDITY, INFRA], LIQUIDITY, "Aave 与 Plasma 的 USDT 利用率接近或达到 100%，可用流动性几乎归零，属于严重提款和协议风险。"),
    27: (20, [MACRO], MACRO, "美股期货全面下跌并引发对 BTC 的传导担忧，属于轻度宏观市场风险而非已发生的严重冲击。"),
    32: (85, [LIQUIDITY, LIQUIDATION], LIQUIDITY, "多个 Aave 池利用率达到 100%、存款人无法提款且清算可能失效，属于严重流动性和坏账风险。"),
    62: (35, [SCAM], SCAM, "社交工程占 Web3 攻击 74.7% 且追回率低于 10%，反映已发生的行业诈骗攻击风险。"),
    72: (40, [INFRA], INFRA, "eth.limo 域名注册商账户被社会工程劫持并导致服务异常，虽快速恢复且无损失，事件本身已经发生。"),
    136: (20, [INFRA], INFRA, "Hyperwave 预防性暂停全部 LayerZero 桥接，资产安全但功能中断，属于轻度基础设施可用性风险。"),
    157: (90, [ATTACK, LIQUIDITY], ATTACK, "约 2 亿美元 rsETH 被盗并导致 Aave 流动性跌破阈值和潜在坏账，属于重大攻击与流动性风险。"),
    205: (50, [VOLATILITY], VOLATILITY, "ASTEROID 六小时下跌 25.56%，对小市值代币而言仍是明显的短时异常波动。"),
    405: (50, [LIQUIDATION], LIQUIDATION, "ORDI 24 小时实际爆仓约 3029 万美元并位列全网第三，属于中等清算风险。"),
    515: (50, [VOLATILITY], VOLATILITY, "BIO 代币短时间上涨超过 100%，幅度显著偏离常规市场变化，属于异常行情。"),
    620: (20, [VOLATILITY], VOLATILITY, "专家预测可能出现重大抛售但尚未发生，仅构成轻度市场波动风险信号。"),
    631: (20, [REGULATORY], REGULATORY, "银行高管明确指出稳定币可能规避监管与消费者保护规则，属于轻度监管风险信号。"),
    735: (30, [ATTACK], ATTACK, "CowSwap 前端攻击已经发生，但 Aave 本身未受影响且已切换路由，故标注轻度外部攻击风险。"),
    818: (30, [REGULATORY], REGULATORY, "Clarity Act 草案涉及稳定币收益并已遭银行业反对，虽未成法但监管不确定性明确。"),
    850: (40, [LIQUIDATION], LIQUIDATION, "BTC 关键价位对应约 9.5 亿至 18.7 亿美元潜在清算，规模较大，构成中等条件性风险。"),
    867: (35, [LIQUIDATION], LIQUIDATION, "ETH 关键价位对应约 6.4 亿至 8.9 亿美元潜在清算，属于轻度条件性风险。"),
    877: (30, [LIQUIDATION, WHALE], LIQUIDATION, "单一大户仍持有 3160 万美元的 25 倍 ETH 多仓，形成明确但尚未触发的清算与巨鲸风险。"),
    898: (85, [SCAM, GOVERNANCE, VOLATILITY], SCAM, "团队疑似控制超过 98% 供应且七日暴涨 3747%，同时具备高度集中的 Rug Pull、治理和异常行情风险。"),
    978: (35, [ATTACK, REGULATORY], ATTACK, "合约后门和资产冻结指控尚未证实，但双方已进入法律争议，构成轻度技术与法律风险。"),
    110: (60, [MACRO], MACRO, "伊朗关闭霍尔木兹海峡已推动油价和合成期货交易、未平仓量大幅上升，宏观冲击事实明确。"),
    934: (70, [ATTACK], ATTACK, "攻击者利用网关漏洞铸造 10 亿枚 DOT 并出售获利，供应完整性受损，属于高风险链上攻击。"),
    174: (40, [REGULATORY], REGULATORY, "FCA 已正式发布覆盖稳定币、质押和交易平台的监管提案，可能要求大量企业获授权，构成中等监管风险。"),
    276: (35, [LIQUIDATION], LIQUIDATION, "原油映射合约一小时实际爆仓 2368 万美元并位列全网第三，属于轻度清算风险。"),
    311: (20, [WHALE], WHALE, "单一地址通过场外交易及做市商接收大量 LDO 和 AAVE，属于轻度巨鲸积累行为。"),
    395: (20, [REGULATORY], REGULATORY, "政客投资和宣传视频已引发要求 FCA 调查的正式呼吁，但尚未启动执法，属于轻度监管风险。"),
    556: (20, [REGULATORY], REGULATORY, "国际稳定币标准进展放缓而英美推进国内规则，形成轻度跨境监管不确定性。"),
    394: (25, [LIQUIDITY], LIQUIDITY, "STRC 以 11.5% 股息大规模融资且并非受保存款，尚无违约但资本结构具有轻度偿付风险。"),
    241: (45, [INFRA], INFRA, "BIP-361 拟冻结超过 650 万枚量子脆弱 BTC 并引发财产权冲突，虽处提案阶段但协议影响巨大。"),
    8: (20, [VOLATILITY], VOLATILITY, "BTC 高于最大痛苦点且持仓集中在 7.5 万美元，存在轻度挤压或回调风险但尚未发生异常。"),
    643: (25, [MACRO], MACRO, "大型 IPO 可能吸收逾千亿美元资本并短期压制 Bitcoin 流动性，属于轻度宏观资金面风险。"),
}


def score_to_label(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def main() -> None:
    review = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)
    actual_ids = set(review["新闻id"].astype(int))
    decision_ids = set(DECISIONS)
    if actual_ids != decision_ids:
        raise ValueError(
            f"复核决定与输入ID不一致，missing={sorted(actual_ids - decision_ids)}, "
            f"extra={sorted(decision_ids - actual_ids)}"
        )

    editable_columns = [
        "human_review_status",
        "human_risk_score",
        "human_risk_label",
        "human_risk_types",
        "human_primary_risk_type",
        "human_reason",
        "human_reviewer",
        "human_reviewed_at",
    ]
    review[editable_columns] = review[editable_columns].astype(object)
    review["reviewer_kind"] = "ai"
    for index, row in review.iterrows():
        record_id = int(row["新闻id"])
        score, risk_types, primary, reason = DECISIONS[record_id]
        if not 0 <= score <= 100:
            raise ValueError(f"新闻id={record_id} 分数超出0-100")
        if not risk_types and primary != NO_RISK:
            raise ValueError(f"新闻id={record_id} 无风险类型但主类型不是无明显风险")
        if risk_types and primary not in risk_types:
            raise ValueError(f"新闻id={record_id} 主类型不在风险类型中")

        review.at[index, "human_review_status"] = "approved"
        review.at[index, "human_risk_score"] = score
        review.at[index, "human_risk_label"] = score_to_label(score)
        review.at[index, "human_risk_types"] = json.dumps(risk_types, ensure_ascii=False)
        review.at[index, "human_primary_risk_type"] = primary
        review.at[index, "human_reason"] = reason
        review.at[index, "human_reviewer"] = "Codex_AI_review"
        review.at[index, "human_reviewed_at"] = "2026-07-15T00:00:00+08:00"

    review.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"Codex 复核完成：{OUTPUT_PATH}，共 {len(review)} 条")
    print("标签分布：")
    print(review["human_risk_label"].value_counts().to_string())


if __name__ == "__main__":
    main()
