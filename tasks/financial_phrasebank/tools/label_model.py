"""调用官方 Snorkel，聚合金融情感三分类投票，返回标签和概率。"""

import numpy as np
from snorkel.labeling.model import LabelModel

if __package__:
    from .task import CARDINALITY, VALID_VOTES
else:
    from task import CARDINALITY, VALID_VOTES


def validate_votes(L):
    """L 的行是句子、列是 LF；-1 弃权，0/1/2 分别为负面/中性/正面。"""
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or L.shape[1] < 3:
        raise ValueError("L 必须是非空二维投票矩阵，且至少有 3 个 LF")
    if L.dtype.kind not in "iu" or not np.isin(L, VALID_VOTES).all():
        raise ValueError("LF 投票只能是整数 -1、0、1、2")
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
    """只用未标注投票拟合；均匀先验是默认假设，不是估计出的类别比例。"""
    L = validate_votes(L)
    if (L == -1).all():
        raise ValueError("未标注数据上所有 LF 都弃权，没有可供 Snorkel 学习的投票")

    model = LabelModel(cardinality=CARDINALITY, verbose=False, device="cpu")
    model.fit(
        L_train=L,
        # 固定标注集按 34/33/33 配额抽取，不能用其比例估计总体先验。
        # 不从源数据或测试金标取比例；不传 class_balance 也会回退到均匀先验。
        class_balance=[1 / CARDINALITY] * CARDINALITY,
        n_epochs=500,
        seed=42,
        progress_bar=False,
    )
    labels, probabilities = predict_labels(model, L)
    return model, labels, probabilities
