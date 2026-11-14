"""评价 SMS 弱标签：0=ham，1=spam，-1=弃权。"""

import numpy as np


def classification_metrics(gold, predictions):
    """全量评价时，弃权计为真实类别的一次漏判。"""
    result = {
        "n_samples": len(gold),
        "accuracy": float((gold == predictions).mean()) if len(gold) else None,
    }
    for label, name in enumerate(("ham", "spam")):
        tp = int(((gold == label) & (predictions == label)).sum())
        fp = int(((gold != label) & (predictions == label)).sum())
        fn = int(((gold == label) & (predictions != label)).sum())
        result[name] = {
            "support": int((gold == label).sum()),
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        }
    result["macro_f1"] = (result["ham"]["f1"] + result["spam"]["f1"]) / 2
    return result


def evaluate(gold, predictions):
    gold = np.asarray(gold)
    predictions = np.asarray(predictions)
    if gold.ndim != 1 or predictions.shape != gold.shape or not len(gold):
        raise ValueError("真实标签与预测须为等长、非空的一维数组")
    if not np.isin(gold, [0, 1]).all() or not np.isin(predictions, [-1, 0, 1]).all():
        raise ValueError("真实标签只能是 0/1，预测只能是 -1/0/1")
    covered = predictions != -1
    return {
        "method": "snorkel_label_model",
        "n_samples": len(gold),
        "n_labeled": int(covered.sum()),
        "n_abstained": int((~covered).sum()),
        "coverage": float(covered.mean()),
        "correct": int((gold == predictions).sum()),
        "incorrect_labeled": int(((gold != predictions) & covered).sum()),
        "all_samples": classification_metrics(gold, predictions),
        "covered_only": classification_metrics(gold[covered], predictions[covered]),
        "confusion": {
            actual_name: {
                predicted_name: int(((gold == actual) & (predictions == predicted)).sum())
                for predicted, predicted_name in [(0, "ham"), (1, "spam"), (-1, "abstain")]
            }
            for actual, actual_name in enumerate(("ham", "spam"))
        },
    }
