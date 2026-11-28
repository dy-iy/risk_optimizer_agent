"""无权重多数票：忽略弃权，平票或全弃权时输出弃权。"""

import numpy as np


if __package__:
    from .task import CARDINALITY, VALID_VOTES
else:
    from task import CARDINALITY, VALID_VOTES


def simple_vote(L):
    """返回标签和非弃权票的类别占比；全弃权的占比占位为各类别均匀分布。"""
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or not L.shape[1] or not np.isin(L, VALID_VOTES).all():
        raise ValueError("投票必须是非空二维矩阵，取值只能为弃权或任务类别编号")
    counts = np.column_stack([(L == label).sum(axis=1) for label in range(CARDINALITY)])
    totals = counts.sum(axis=1, keepdims=True)
    probabilities = np.full(counts.shape, 1.0 / CARDINALITY, dtype=float)
    np.divide(counts, totals, out=probabilities, where=totals != 0)
    predictions = counts.argmax(axis=1)
    predictions[(counts == counts.max(axis=1, keepdims=True)).sum(axis=1) != 1] = -1
    return predictions, probabilities
