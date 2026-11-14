"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = 'Yelp'
CLASS_TITLES = ('Negative', 'Positive')
SPLIT_COUNTS = {'train': 30400, 'valid': 3800, 'test': 3800}
TASK_DESCRIPTION = '英文商户/服务评论的整体情感二分类，0=负面，1=正面。按评论者的实际消费体验和最终态度判断；注意否定、转折、比较与褒贬混合，不将每条评论默认判为某类。'
CARDINALITY = len(CLASS_TITLES)
CLASS_NAMES = tuple(str(i) for i in range(CARDINALITY))
LABEL_IDS = {name: i for i, name in enumerate(CLASS_TITLES)}
LABELS = {-1: "abstain", **dict(enumerate(CLASS_NAMES))}
VALID_VOTES = (-1, *range(CARDINALITY))
LABEL_DESCRIPTION = "；".join(f"{i}={name}" for i, name in enumerate(CLASS_TITLES)) + "；-1=弃权"
