"""调用官方 Snorkel，聚合 FinNSP 的 LF 投票，返回标签和概率。"""

import numpy as np
from snorkel.labeling.model import LabelModel


def validate_votes(L):
    """L 的行是文本与候选实体记录、列是 LF；-1 弃权，0 无负面消息，1 有。"""
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or L.shape[1] < 3:
        raise ValueError("L 必须是非空二维投票矩阵，且至少有 3 个 LF")
    if not np.isin(L, [-1, 0, 1]).all():
        raise ValueError("LF 投票只能是 -1、0、1")
    return L.astype(int)


def predict_labels(model, L):
    """使用已拟合模型预测；不重新拟合，所有 LF 弃权时输出也弃权。"""
    L = validate_votes(L)
    labels, probabilities = model.predict(L, return_probs=True, tie_break_policy="abstain")
    if not np.isfinite(probabilities).all():
        raise RuntimeError("Snorkel 返回了无效概率，请检查 LF 投票")
    labels[(L == -1).all(axis=1)] = -1
    return labels, probabilities


def estimate_class_balance(valid_labels):
    """仅用验证集标签估计先验，类别顺序固定为 0、1。"""
    labels = np.asarray(valid_labels)
    if labels.ndim != 1 or not labels.size or not np.isin(labels, [0, 1]).all():
        raise ValueError("估计类别先验需要非空的一维验证标签，取值只能为 0、1")
    counts = np.bincount(labels.astype(int), minlength=2)
    if (counts == 0).any():
        raise ValueError("验证集必须同时包含 0 类和 1 类才能估计类别先验")
    return (counts / counts.sum()).tolist()


def snorkel_aggregate(L, class_balance):
    """训练集投票用于拟合，验证集仅通过传入的类别先验参与。"""
    L = validate_votes(L)
    if (L == -1).all():
        raise ValueError("训练集上所有 LF 都弃权，没有可供 Snorkel 学习的投票")
    prior = np.asarray(class_balance, dtype=float)
    if (prior.shape != (2,) or not np.isfinite(prior).all()
            or (prior <= 0).any() or not np.isclose(prior.sum(), 1)):
        raise ValueError("类别先验必须按 0、1 顺序包含两个正概率，且总和为 1")

    model = LabelModel(cardinality=2, verbose=False, device="cpu")
    model.fit(
        L_train=L,
        class_balance=prior.tolist(),
        n_epochs=500,
        seed=42,
        progress_bar=False,
    )
    labels, probabilities = predict_labels(model, L)
    return model, labels, probabilities
