from __future__ import annotations

NEWS_ID_COL = "\u65b0\u95fbid"
CONTENT_COL = "\u5185\u5bb9"
TIME_COL = "\u65f6\u95f4"
LINK_COL = "\u94fe\u63a5"

SOURCE_COLUMNS = [
    CONTENT_COL,
    TIME_COL,
    LINK_COL,
]

GOLD_RENAME_MAP = {
    "risk_score": "gold_risk_score",
    "risk_label": "gold_risk_label",
    "risk_types": "gold_risk_types",
    "primary_risk_type": "gold_primary_risk_type",
    "reason": "gold_reason",
    "confidence": "gold_confidence",
    "summary": "gold_summary",
}

PREDICTION_RENAME_MAP = {
    "risk": "rule_risk_score",
    "rule_label": "rule_risk_label",
    "rule_types": "rule_risk_types",
    "rule_primary_type": "rule_primary_risk_type",
}

MERGED_FRONT_COLUMNS = [
    NEWS_ID_COL,
    CONTENT_COL,
    TIME_COL,
    LINK_COL,
    "gold_risk_score",
    "rule_risk_score",
    "score_diff",
    "gold_risk_label",
    "rule_risk_label",
    "label_match",
    "gold_risk_types",
    "rule_risk_types",
    "gold_primary_risk_type",
    "rule_primary_risk_type",
    "primary_type_match",
    "gold_reason",
    "gold_confidence",
    "gold_summary",
]

MERGED_REQUIRED_COLUMNS = [
    "gold_risk_score",
    "rule_risk_score",
    "gold_risk_label",
    "rule_risk_label",
    "gold_primary_risk_type",
    "rule_primary_risk_type",
]

LABEL_ORDER = ["low", "medium", "high"]

VERSION_SUBDIRS = [
    "scripts",
    "reports/predictions",
    "reports/merged",
    "reports/evals",
    "reports/errors",
    "reports/analysis",
    "reports/patched",
    "reports/comparisons",
    "reports/orchestrations",
]
