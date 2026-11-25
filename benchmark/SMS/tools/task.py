"""SMS 的任务描述与冻结数据使用的标签。"""

DATASET = "SMS"
CLASS_TITLES = CLASS_NAMES = ("ham", "spam")
CARDINALITY = len(CLASS_NAMES)
TASK_DESCRIPTION = (
    "根据英文短信正文区分正常短信与垃圾信息。结合诈骗、付费、中奖、引流等推广意图，"
    "避免将普通联系或私人安排误判为垃圾信息。"
)
LABEL_DESCRIPTION = "；".join(f"{i}={name}" for i, name in enumerate(CLASS_TITLES)) + "；-1=弃权"
LABEL_IDS = {name: i for i, name in enumerate(CLASS_NAMES)}


def label_id(value):
    """接受冻结数据中的标签名或已编码的类别编号。"""
    if type(value) is int and 0 <= value < CARDINALITY:
        return value
    if value not in LABEL_IDS:
        raise ValueError(f"{DATASET} 无效标签：{value!r}")
    return LABEL_IDS[value]
