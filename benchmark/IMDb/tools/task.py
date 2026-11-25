"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = 'IMDb'
CLASS_TITLES = ('Negative', 'Positive')
SPLIT_COUNTS = {'train': 20000, 'valid': 2500, 'test': 2500}
TASK_DESCRIPTION = '英文电影评论的整体情感二分类，0=负面，1=正面。按评论者对影片的最终评价判断；剧情中的负面事件、角色评价或孤立情感词不自动代表整体态度，注意否定、转折和褒贬混合。'
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
