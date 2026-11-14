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
