"""无权重多数票：忽略弃权，平票或全弃权时输出弃权。"""

import numpy as np


def simple_vote(L):
    """返回标签和非弃权票的类别占比；全弃权的占比占位为 [0.5, 0.5]。"""
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or not L.shape[1] or not np.isin(L, [-1, 0, 1]).all():
        raise ValueError("投票必须是非空二维矩阵，取值只能为 -1、0、1")
    counts = np.column_stack([(L == label).sum(axis=1) for label in (0, 1)])
    totals = counts.sum(axis=1, keepdims=True)
    probabilities = np.full(counts.shape, 0.5, dtype=float)
    np.divide(counts, totals, out=probabilities, where=totals != 0)
    predictions = counts.argmax(axis=1)
    predictions[counts[:, 0] == counts[:, 1]] = -1
    return predictions, probabilities
