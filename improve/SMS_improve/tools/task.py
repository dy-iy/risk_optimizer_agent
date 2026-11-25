"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = 'SMS'
CLASS_TITLES = ('ham', 'spam')
SPLIT_COUNTS = {'train': 4571, 'valid': 500, 'test': 500}
TASK_DESCRIPTION = '根据英文短信正文区分日常短信和垃圾短信。0 为 ham，1 为 spam。留意中奖、付费号码和诱导回复等推广行为，以及普通私人约定的语境。'
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
