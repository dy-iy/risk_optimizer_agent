"""Shared CSV, JSON and category helpers."""

import json
from pathlib import Path

import pandas as pd


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def write_csv(data: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(path, index=False, encoding="utf-8-sig")


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_categories(value: object) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return set()
    return {str(item) for item in parsed} if isinstance(parsed, list) else set()


def binary_metrics(gold: list[bool], pred: list[bool]) -> dict:
    tp = sum(actual and guessed for actual, guessed in zip(gold, pred))
    fp = sum(not actual and guessed for actual, guessed in zip(gold, pred))
    fn = sum(actual and not guessed for actual, guessed in zip(gold, pred))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "support": int(sum(gold)),
    }
