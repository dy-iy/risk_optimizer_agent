"""调用官方 Snorkel，聚合当前任务的 LF 投票，返回标签和概率。"""

import numpy as np
from snorkel.labeling.model import LabelModel


if __package__:
    from .task import CARDINALITY, VALID_VOTES
else:
    from task import CARDINALITY, VALID_VOTES


def validate_votes(L):
    """L 的行是文本、列是 LF；-1 弃权，其余为任务类别编号。"""
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or L.shape[1] < 3:
        raise ValueError("L 必须是非空二维投票矩阵，且至少有 3 个 LF")
    if not np.isin(L, VALID_VOTES).all():
        raise ValueError("LF 投票超出任务类别范围")
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
    """仅用验证集标签估计先验，类别顺序与 task.py 一致。"""
    labels = np.asarray(valid_labels)
    if labels.ndim != 1 or not labels.size or not np.isin(labels, range(CARDINALITY)).all():
        raise ValueError("估计类别先验需要非空的一维任务类别标签")
    counts = np.bincount(labels.astype(int), minlength=CARDINALITY)
    if (counts == 0).any():
        raise ValueError("验证集必须包含任务的全部类别才能估计类别先验")
    return (counts / counts.sum()).tolist()


def snorkel_aggregate(L, class_balance):
    """训练集投票用于拟合，验证集仅通过传入的类别先验参与。"""
    L = validate_votes(L)
    if (L == -1).all():
        raise ValueError("训练集上所有 LF 都弃权，没有可供 Snorkel 学习的投票")
    prior = np.asarray(class_balance, dtype=float)
    if (prior.shape != (CARDINALITY,) or not np.isfinite(prior).all()
            or (prior <= 0).any() or not np.isclose(prior.sum(), 1)):
        raise ValueError("类别先验必须按任务类别顺序包含正概率，且总和为 1")

    model = LabelModel(cardinality=CARDINALITY, verbose=False, device="cpu")
    model.fit(
        L_train=L,
        class_balance=prior.tolist(),
        n_epochs=500,
        seed=42,
        progress_bar=False,
    )
    labels, probabilities = predict_labels(model, L)
    return model, labels, probabilities
