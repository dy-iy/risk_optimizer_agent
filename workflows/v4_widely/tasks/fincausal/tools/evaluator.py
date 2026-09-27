"""指标工具：计算分类指标、覆盖率、混淆矩阵和 LF 质量。"""
from collections import Counter


def binary(gold, pred):
    tp = sum(a and b for a, b in zip(gold, pred))
    fp = sum(not a and b for a, b in zip(gold, pred))
    fn = sum(a and not b for a, b in zip(gold, pred))
    p = tp / (tp + fp) if tp + fp else 0.
    r = tp / (tp + fn) if tp + fn else 0.
    return {"precision": round(p, 6), "recall": round(r, 6),
            "f1": round(2 * p * r / (p + r), 6) if p + r else 0., "support": sum(gold)}


def field_metrics(golds, preds, labels, multilabel=False):
    gold_sets = [set(g) if multilabel else {g} for g in golds]
    pred_sets = [set(p) if multilabel else {p} for p in preds]
    per_label, all_gold, all_pred = {}, [], []
    for label in labels:
        gold = [label in g for g in gold_sets]
        pred = [label in p for p in pred_sets]
        per_label[label] = binary(gold, pred)
        all_gold.extend(gold)
        all_pred.extend(pred)
    return {"micro_f1": binary(all_gold, all_pred)["f1"],
            "macro_f1": round(sum(v["f1"] for v in per_label.values()) / len(labels), 6),
            "accuracy": round(sum(a == b for a, b in zip(gold_sets, pred_sets)) / len(golds), 6),
            "per_label": per_label}


def evaluate(task, records, predictions):
    if not records or len(predictions) != len(records):
        raise ValueError("Metrics require nonempty records and exactly one prediction per record")
    if len({p["id"] for p in predictions}) != len(predictions):
        raise ValueError("Duplicate prediction IDs")
    indexed = {p["id"]: p for p in predictions}
    if set(indexed) != {r["id"] for r in records}:
        raise ValueError("Prediction ID mismatch")
    from .data import validate_gold
    golds = [r["gold"] for r in records]
    preds = [indexed[r["id"]]["prediction"] for r in records]
    for output in golds + preds:
        validate_gold(task, output)
    metrics = field_metrics([r[task.field] for r in golds], [r[task.field] for r in preds],
                            task.labels, task.kind != "single")
    if task.positive:
        metrics["positive_f1"] = metrics["per_label"][task.positive]["f1"]
    if task.kind == "joint":
        level = field_metrics([r["level"] for r in golds], [r["level"] for r in preds], ["low", "medium", "high"])
        metrics.update(level_macro_f1=level["macro_f1"], level_accuracy=level["accuracy"],
                       high_recall=level["per_label"]["high"]["recall"], high_f1=level["per_label"]["high"]["f1"],
                       per_level=level["per_label"])
    metrics.update(rows=len(records), coverage=round(sum(bool(p["votes"]) for p in predictions) / len(records), 6),
                   conflict_rate=round(sum(p["conflict"] for p in predictions) / len(records), 6))
    return metrics


def confusion(records, predictions, field):
    indexed = {p["id"]: p for p in predictions}
    counts = Counter((r["gold"][field], indexed[r["id"]]["prediction"][field]) for r in records)
    return [{"gold": gold, "predicted": pred, "count": n} for (gold, pred), n in sorted(counts.items())]


def _base_quality(task, records, predictions):
    gold = {r["id"]: r["gold"] for r in records}
    counters = {name: Counter() for name in task.lf_names}
    for prediction in predictions:
        output = gold[prediction["id"]]
        targets = output[task.field]
        targets = set(targets) if isinstance(targets, list) else {targets}
        for vote in prediction["votes"]:
            count = counters[vote["lf"]]
            count["fired"] += 1
            count["voted_labels"] += len(vote["labels"])
            count["correct_labels"] += len(targets & set(vote["labels"]))
            if task.kind == "joint":
                count["matches_document_level"] += vote["level"] == output["level"]
    details = {}
    for name, label in zip(task.lf_names, task.labels):
        count = counters[name]
        details[name] = {"assigned_label": label, "fired": count["fired"],
                         "coverage": round(count["fired"] / len(records), 6) if records else 0.,
                         "label_precision_when_fired": (round(count["correct_labels"] / count["voted_labels"], 6)
                                                         if count["voted_labels"] else None),
                         "never_fired": not count["fired"]}
        if task.kind == "joint":
            details[name]["document_level_agreement"] = (round(count["matches_document_level"] / count["fired"], 6)
                                                         if count["fired"] else None)
    return {"rows": len(records), "lf_quality": details,
            "uncovered_rows": sum(p["fallback"] for p in predictions),
            "conflicting_rows": sum(p["conflict"] for p in predictions),
            "note": "LF precision uses raw votes; task metrics use final aggregation. "
                    "Document risk level is not per-LF gold."}


def quality_report(task, records, predictions):
    return {**_base_quality(task, records, predictions), "causality_confusion": confusion(records, predictions, task.field),
            "focus": "因果存在性错误；连接词命中只是线索，不是因果金标。"}
