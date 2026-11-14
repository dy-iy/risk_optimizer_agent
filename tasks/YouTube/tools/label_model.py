"""调用官方 Snorkel，聚合 YouTube 的 LF 投票，返回标签和概率。"""

import numpy as np
from snorkel.labeling.model import LabelModel


def validate_votes(L):
    """L 的行是评论、列是 LF；-1 弃权，0 为 ham，1 为 spam。"""
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or L.shape[1] < 3:
        raise ValueError("L 必须是非空二维投票矩阵，且至少有 3 个 LF")
    if L.dtype.kind not in "iu" or not np.isin(L, [-1, 0, 1]).all():
        raise ValueError("LF 投票只能是整数 -1、0、1")
    return L.astype(int)


def predict_labels(model, L):
    """使用已拟合模型预测；不重新拟合，所有 LF 弃权时输出也弃权。"""
    L = validate_votes(L)
    labels, probabilities = model.predict(L, return_probs=True, tie_break_policy="abstain")
    if not np.isfinite(probabilities).all():
        raise RuntimeError("Snorkel 返回了无效概率，请检查 LF 投票")
    labels[(L == -1).all(axis=1)] = -1
    return labels, probabilities


def snorkel_aggregate(L):
    """只把训练集的投票矩阵传入 fit，不读取任何金标。"""
    L = validate_votes(L)
    if (L == -1).all():
        raise ValueError("训练集上所有 LF 都弃权，没有可供 Snorkel 学习的投票")

    model = LabelModel(cardinality=2, verbose=False, device="cpu")
    model.fit(
        L_train=L,
        class_balance=[0.5, 0.5],
        n_epochs=500,
        seed=42,
        progress_bar=False,
    )
    labels, probabilities = predict_labels(model, L)
    return model, labels, probabilities
