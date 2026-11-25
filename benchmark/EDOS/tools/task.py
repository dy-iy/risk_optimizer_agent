"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = 'EDOS'
CLASS_TITLES = ('not sexist', 'sexist')
SPLIT_COUNTS = {'train': 14000, 'valid': 2000, 'test': 4000}
TASK_DESCRIPTION = '英文性别歧视二分类（EDOS Task A）。1=针对女性的性别歧视、贬损、刻板印象、排斥或暴力威胁，0=非性别歧视。提到女性、一般粗口、讨论或反驳性别歧视不自动构成正类；区分引用、否定、反讽和说话者立场。仅做二分类，不输出 category/vector 子类别。'
CARDINALITY = len(CLASS_TITLES)
CLASS_NAMES = tuple(str(i) for i in range(CARDINALITY))
LABEL_IDS = {name: i for i, name in enumerate(CLASS_TITLES)}
LABELS = {-1: "abstain", **dict(enumerate(CLASS_NAMES))}
VALID_VOTES = (-1, *range(CARDINALITY))
LABEL_DESCRIPTION = "；".join(f"{i}={name}" for i, name in enumerate(CLASS_TITLES)) + "；-1=弃权"

# LF 输入统一为正文字符串；CSV 标签名保留历史约定。
TFIDF_CONFIG = (
    {"analyzer": "char", "ngram_range": (2, 5)} if DATASET == "CHECKED"
    else {"analyzer": "word", "ngram_range": (1, 2)}
)


def label_id(value):
    """接受数据读取后的整数、来源标签名或弱标签名，拒绝未知类别。"""
    if type(value) is int and 0 <= value < CARDINALITY:
        return value
    lookup = {**LABEL_IDS, **{name: i for i, name in enumerate(CLASS_NAMES)}}
    if value not in lookup:
        raise ValueError(f"{DATASET} 无效标签：{value!r}")
    return lookup[value]
