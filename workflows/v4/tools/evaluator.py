"""Separately evaluate risk categories and risk levels."""

from pathlib import Path

from .common import binary_metrics, parse_categories, read_csv, write_json


LEVELS = ["low", "medium", "high"]


def evaluate_categories(data, risk_types: list[str]) -> dict:
    gold_sets = data["gold_风险类别"].apply(parse_categories).tolist()
    pred_sets = data["pred_风险类别"].apply(parse_categories).tolist()

    per_category = {}
    all_gold = []
    all_pred = []
    for risk_type in risk_types:
        gold = [risk_type in values for values in gold_sets]
        pred = [risk_type in values for values in pred_sets]
        per_category[risk_type] = binary_metrics(gold, pred)
        all_gold.extend(gold)
        all_pred.extend(pred)

    return {
        "rows": len(data),
        "micro_f1": binary_metrics(all_gold, all_pred)["f1"],
        "macro_f1": round(
            sum(item["f1"] for item in per_category.values()) / len(risk_types), 6
        ),
        "per_category": per_category,
    }


def evaluate_levels(data) -> dict:
    gold_levels = data["gold_风险等级"].astype(str).tolist()
    pred_levels = data["pred_风险等级"].astype(str).tolist()

    per_level = {}
    for level in LEVELS:
        gold = [value == level for value in gold_levels]
        pred = [value == level for value in pred_levels]
        per_level[level] = binary_metrics(gold, pred)

    accuracy = sum(gold == pred for gold, pred in zip(gold_levels, pred_levels)) / len(data)
    return {
        "rows": len(data),
        "accuracy": round(accuracy, 6),
        "macro_f1": round(sum(item["f1"] for item in per_level.values()) / len(LEVELS), 6),
        "per_level": per_level,
        "high_risk": {
            "recall": per_level["high"]["recall"],
            "f1": per_level["high"]["f1"],
        },
    }


def evaluate(
    merged_csv: Path,
    category_metrics_json: Path,
    level_metrics_json: Path,
    risk_types: list[str],
) -> tuple[dict, dict]:
    data = read_csv(merged_csv)
    category_metrics = evaluate_categories(data, risk_types)
    level_metrics = evaluate_levels(data)
    write_json(category_metrics, category_metrics_json)
    write_json(level_metrics, level_metrics_json)
    return category_metrics, level_metrics
