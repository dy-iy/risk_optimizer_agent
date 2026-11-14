"""所有模块共用的标签顺序；与清理后的源 CSV 一致。"""

CLASS_NAMES = ("negative", "neutral", "positive")
CLASS_TITLES = ("负面", "中性", "正面")
CARDINALITY = len(CLASS_NAMES)
LABEL_IDS = {name: i for i, name in enumerate(CLASS_NAMES)}
LABEL_NAMES = {-1: "abstain", **dict(enumerate(CLASS_NAMES))}
VALID_VOTES = (-1, *range(CARDINALITY))
