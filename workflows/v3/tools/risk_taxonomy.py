from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RiskTypeSpec:
    score_col: str
    risk_type_name: str
    patch_scope: str


RISK_TYPES = [
    RiskTypeSpec("score_hack", "链上漏洞 / 攻击风险", "score_hack and related hack keyword/guard lists"),
    RiskTypeSpec("score_fraud", "诈骗 / 跑路 / Rug Pull 风险", "score_fraud and fraud keyword/guard lists"),
    RiskTypeSpec("score_regulatory", "监管与法律风险", "score_regulatory and regulatory keyword/guard lists"),
    RiskTypeSpec("score_outage", "交易所与系统运维风险", "score_outage and outage keyword/guard lists"),
    RiskTypeSpec("score_stablecoin", "稳定币异常风险", "score_stablecoin and stablecoin keyword/guard lists"),
    RiskTypeSpec("score_liquidation", "爆仓 / 清算风险", "score_liquidation and liquidation keyword/guard lists"),
    RiskTypeSpec("score_whale", "大额转账 / 巨鲸行为风险", "score_whale and whale keyword/guard lists"),
    RiskTypeSpec("score_volatility", "异常行情波动风险", "score_volatility and volatility keyword/guard lists"),
    RiskTypeSpec("score_team", "项目治理 / 团队异常风险", "score_team and team keyword/guard lists"),
    RiskTypeSpec("score_solvency", "偿付能力 / 储备 / 流动性风险", "score_solvency and solvency keyword/guard lists"),
    RiskTypeSpec("score_infra", "基础设施 / 协议层异常风险", "score_infra and infra keyword/guard lists"),
    RiskTypeSpec("score_macro", "宏观 / 政策冲击风险", "score_macro and macro keyword/guard lists"),
]

SCORE_COLS = [item.score_col for item in RISK_TYPES]
RISK_NAME_MAP = {item.score_col: item.risk_type_name for item in RISK_TYPES}
RISK_TYPE_TO_SCORE_COL = {item.risk_type_name: item.score_col for item in RISK_TYPES}
RISK_TAXONOMY = [asdict(item) for item in RISK_TYPES]


def risk_type_name(score_col: str) -> str:
    return RISK_NAME_MAP.get(score_col, score_col)
