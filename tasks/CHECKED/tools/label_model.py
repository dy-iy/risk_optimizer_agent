"""训练投票拟合一次 Snorkel，验证标签仅用于估计类别先验。"""

import numpy as np
from snorkel.labeling.model import LabelModel

if __package__:
    from .task import CARDINALITY, VALID_VOTES
else:
    from task import CARDINALITY, VALID_VOTES


def validate_votes(L):
    L = np.asarray(L)
    if L.ndim != 2 or not L.shape[0] or L.shape[1] < 3:
        raise ValueError("L 必须是非空二维投票矩阵，且至少有 3 个 LF")
    if L.dtype.kind not in "iu" or not np.isin(L, VALID_VOTES).all():
        raise ValueError(f"LF 投票只能是整数 {VALID_VOTES}")
    return L.astype(int)


def predict_labels(model, L):
    L = validate_votes(L)
    labels, probabilities = model.predict(L, return_probs=True, tie_break_policy="abstain")
    if probabilities.shape != (len(L), CARDINALITY) or not np.isfinite(probabilities).all():
        raise RuntimeError("Snorkel 返回了无效概率，请检查 LF 投票")
    labels[(L == -1).all(axis=1)] = -1
    return labels, probabilities


def estimate_class_balance(valid_labels):
    labels = np.asarray(valid_labels)
    if labels.ndim != 1 or not labels.size or labels.dtype.kind not in "iu" or not np.isin(labels, range(CARDINALITY)).all():
        raise ValueError("估计类别先验需要有效的非空一维验证标签")
    counts = np.bincount(labels, minlength=CARDINALITY)
    if (counts == 0).any():
        raise ValueError("验证集必须包含所有任务类别才能估计类别先验")
    return (counts / counts.sum()).tolist()


def snorkel_aggregate(L, class_balance):
    L = validate_votes(L)
    if (L == -1).all():
        raise ValueError("训练集上所有 LF 都弃权，没有可供 Snorkel 学习的投票")
    prior = np.asarray(class_balance, dtype=float)
    if (prior.shape != (CARDINALITY,) or not np.isfinite(prior).all()
            or (prior <= 0).any() or not np.isclose(prior.sum(), 1)):
        raise ValueError(f"类别先验必须按标签顺序包含 {CARDINALITY} 个正概率，且总和为 1")
    model = LabelModel(cardinality=CARDINALITY, verbose=False, device="cpu")
    model.fit(L_train=L, class_balance=prior.tolist(), n_epochs=500, seed=42, progress_bar=False)
    labels, probabilities = predict_labels(model, L)
    return model, labels, probabilities
