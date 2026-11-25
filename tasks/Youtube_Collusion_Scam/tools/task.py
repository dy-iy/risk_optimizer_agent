"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = "Youtube_Collusion_Scam"
CLASS_TITLES = ("no collusion scam", "collusion scam")
SPLIT_COUNTS = {"train": 1256, "valid": 308, "test": 639}
TASK_DESCRIPTION = (
    "英文 YouTube 评论串协同投资诈骗二分类。1=评论串包含多个表面独立的账号"
    "相互配合，伪装成求助、推荐、获利见证或私下联络等互动以推广投资诈骗；"
    "0=评论串未体现这类协同诈骗。判断单位是主评论及其回复组成的整串文本。"
    "按实际换行和“作者: 正文”识别发言，<br> 可作为同一发言内的换行。"
    "关注不同发言者是否围绕同一导师、服务或引流对象呼应，区分推荐和引用、"
    "质疑、劝阻等立场。普通行情讨论、单独的推广、单个联系方式或出现 crypto、"
    "profit、scam 等词都不足以判为 1。个别用户的警告也不能抹消其他发言间的协同证据。"
)
CARDINALITY = len(CLASS_TITLES)
CLASS_NAMES = tuple(str(i) for i in range(CARDINALITY))
LABEL_IDS = {name: i for i, name in enumerate(CLASS_TITLES)}
LABELS = {-1: "abstain", **dict(enumerate(CLASS_NAMES))}
VALID_VOTES = (-1, *range(CARDINALITY))
LABEL_DESCRIPTION = "；".join(f"{i}={name}" for i, name in enumerate(CLASS_TITLES)) + "；-1=弃权"

# LF 输入统一为正文字符串；CSV 标签名保留历史约定。
TFIDF_CONFIG = {"analyzer": "word", "ngram_range": (1, 2)}


def label_id(value):
    """接受数据读取后的整数、来源标签名或弱标签名，拒绝未知类别。"""
    if type(value) is int and 0 <= value < CARDINALITY:
        return value
    lookup = {**LABEL_IDS, **{name: i for i, name in enumerate(CLASS_NAMES)}}
    if value not in lookup:
        raise ValueError(f"{DATASET} 无效标签：{value!r}")
    return lookup[value]
