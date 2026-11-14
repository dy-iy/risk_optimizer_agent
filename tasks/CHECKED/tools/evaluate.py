"""多类别弱标签评价；全量口径将弃权计为真实类别的漏判。"""

import numpy as np

if __package__:
    from .task import CARDINALITY, CLASS_NAMES, VALID_VOTES
else:
    from task import CARDINALITY, CLASS_NAMES, VALID_VOTES


def classification_metrics(gold, predictions):
    result = {"n_samples": len(gold), "accuracy": float((gold == predictions).mean()) if len(gold) else None}
    for label, name in enumerate(CLASS_NAMES):
        tp = int(((gold == label) & (predictions == label)).sum())
        fp = int(((gold != label) & (predictions == label)).sum())
        fn = int(((gold == label) & (predictions != label)).sum())
        result[name] = {
            "support": int((gold == label).sum()),
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        }
    result["macro_f1"] = sum(result[name]["f1"] for name in CLASS_NAMES) / CARDINALITY
    return result


def evaluate(gold, predictions):
    gold, predictions = np.asarray(gold), np.asarray(predictions)
    if gold.ndim != 1 or predictions.shape != gold.shape or not len(gold):
        raise ValueError("真实标签与预测须为等长、非空的一维数组")
    if (gold.dtype.kind not in "iu" or predictions.dtype.kind not in "iu"
            or not np.isin(gold, range(CARDINALITY)).all() or not np.isin(predictions, VALID_VOTES).all()):
        raise ValueError("真实标签或预测超出任务类别范围")
    covered = predictions != -1
    return {
        "method": "snorkel_label_model", "n_samples": len(gold),
        "n_labeled": int(covered.sum()), "n_abstained": int((~covered).sum()),
        "coverage": float(covered.mean()), "correct": int((gold == predictions).sum()),
        "incorrect_labeled": int(((gold != predictions) & covered).sum()),
        "all_samples": classification_metrics(gold, predictions),
        "covered_only": classification_metrics(gold[covered], predictions[covered]),
        "confusion": {
            str(actual): {
                name: int(((gold == actual) & (predictions == predicted)).sum())
                for predicted, name in [*enumerate(CLASS_NAMES), (-1, "abstain")]
            }
            for actual in range(CARDINALITY)
        },
    }
