"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = 'YouTube'
CLASS_TITLES = ('ham', 'spam')
SPLIT_COUNTS = {'train': 1686, 'valid': 120, 'test': 250}
TASK_DESCRIPTION = '根据 YouTube 评论正文判断垃圾信息：0=正常评论，1=垃圾推广。区分对当前视频的真实讨论与引流、订阅交换、商业推广或诈骗；链接、粗口、短评和提到订阅不单独决定类别。'
CARDINALITY = len(CLASS_TITLES)
CLASS_NAMES = CLASS_TITLES
LABEL_IDS = {name: i for i, name in enumerate(CLASS_TITLES)}
LABELS = {-1: "abstain", **dict(enumerate(CLASS_NAMES))}
VALID_VOTES = (-1, *range(CARDINALITY))
LABEL_DESCRIPTION = "；".join(f"{i}={name}" for i, name in enumerate(CLASS_TITLES)) + "；-1=弃权"

# LF 输入统一为正文字符串；CSV 标签名保留历史约定。
TFIDF_CONFIG = {'analyzer': 'word', 'ngram_range': (1, 2)}
BERT_MODEL_ID = "bert-base-cased"
BERT_VOCAB_SIZE = 28996
BERT_MAX_LENGTH = 128
BERT_REVISION = "cd5ef92a9fb2f889e972770a36d4ed042daf221e"


def label_id(value):
    """接受数据读取后的整数、来源标签名或弱标签名，拒绝未知类别。"""
    if type(value) is int and 0 <= value < CARDINALITY:
        return value
    lookup = {**LABEL_IDS, **{name: i for i, name in enumerate(CLASS_NAMES)}}
    if value not in lookup:
        raise ValueError(f"{DATASET} 无效标签：{value!r}")
    return lookup[value]
