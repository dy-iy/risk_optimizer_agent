"""任务标签顺序与输入契约；与冻结的数据清单一致。"""

DATASET = 'CHECKED'
CLASS_TITLES = ('real', 'fake')
SPLIT_COUNTS = {'train': 1485, 'valid': 212, 'test': 425}
TASK_DESCRIPTION = '根据中文新闻/社交媒体文本判断真实性。0=真实，1=虚假。辟谣报道的正文可能复述谣言；应区分被否定的断言与文本实际主张。情绪、夸张、转发号召、机构署名均不能单独证实真假。LF 无外部检索能力，缺乏可信文本证据时弃权，不能把一般健康建议一概判成真实或虚假。'
CARDINALITY = len(CLASS_TITLES)
CLASS_NAMES = tuple(str(i) for i in range(CARDINALITY))
LABEL_IDS = {name: i for i, name in enumerate(CLASS_TITLES)}
LABELS = {-1: "abstain", **dict(enumerate(CLASS_NAMES))}
VALID_VOTES = (-1, *range(CARDINALITY))
LABEL_DESCRIPTION = "；".join(f"{i}={name}" for i, name in enumerate(CLASS_TITLES)) + "；-1=弃权"
