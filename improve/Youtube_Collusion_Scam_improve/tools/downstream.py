"""共享的下游弱标签校验、版本选择和输入溯源。"""

from collections import Counter
import hashlib
import json
import re

import numpy as np
from sklearn.metrics import confusion_matrix

if __package__:
    from .data import CSV_FILENAMES, read_csv
    from .evaluate import classification_metrics
    from .impact import AGGREGATORS
    from .task import CARDINALITY, CLASS_NAMES, label_id
else:
    from data import CSV_FILENAMES, read_csv
    from evaluate import classification_metrics
    from impact import AGGREGATORS
    from task import CARDINALITY, CLASS_NAMES, label_id


LF_VERSION = r"v[1-9]\d*"
EXPLICIT_VERSION = LF_VERSION + "|gpt_lfs"
WEAK_LABEL_IDS = {"abstain": -1, **{name: i for i, name in enumerate(CLASS_NAMES)}}


def read_upstream(version_dir, aggregator, metadata):
    path = version_dir / aggregator / "metrics.json"
    metrics = json.loads(path.read_text(encoding="utf-8"))
    if (metrics["lf_version"] != version_dir.name
            or metrics["aggregator"] != aggregator
            or metrics["data_split"]["csv_sha256"] != metadata["csv_sha256"]):
        raise ValueError(f"上游版本、聚合器或数据划分不匹配：{path}")
    return metrics


def select_versions(folder, aggregators, metadata, version_name=None):
    """各聚合器独立按验证集全量 Macro-F1 选版，同分取最早版本。"""
    versions = folder / "versions"
    candidates = ([versions / version_name] if version_name else sorted(
        (path for path in versions.iterdir() if path.is_dir() and re.fullmatch(LF_VERSION, path.name)),
        key=lambda path: int(path.name[1:]),
    ))
    selections = []
    for aggregator in dict.fromkeys(aggregators):
        scores = []
        for version in candidates:
            output = version / aggregator
            if not all(path.is_file() for path in (
                output / "metrics.json", output / "weak_labels.csv", version / "lfs.py"
            )):
                continue
            value = read_upstream(version, aggregator, metadata)["valid"]["all_samples"]["macro_f1"]
            if not np.isfinite(value):
                raise ValueError(f"验证 Macro-F1 无效：{output}")
            scores.append({"version": version.name, "valid_macro_f1": value})
        if not scores:
            raise ValueError(f"{aggregator} 没有已评估的 LF 版本（{version_name or '自动选择'}）")
        best = max(scores, key=lambda item: item["valid_macro_f1"])
        selections.append({
            "aggregator": aggregator, **best, "candidates": scores,
            "method": "explicit_version" if version_name else "best_valid_macro_f1_earliest_tie",
        })
    return selections


def training_data(version_dir, aggregator, splits, metadata):
    upstream = read_upstream(version_dir, aggregator, metadata)
    weak_path = version_dir / aggregator / "weak_labels.csv"
    weak_train = [row for row in read_csv(weak_path) if row["split"] == "train"]
    by_id = {row["id"]: row for row in weak_train}
    if len(by_id) != len(weak_train) or set(by_id) != {row["id"] for row in splits["train"]}:
        raise ValueError(f"训练弱标签存在重复、缺失或多余 ID：{weak_path}")
    train, targets, counts = [], [], Counter()
    for row in splits["train"]:
        weak = by_id[row["id"]]
        name = weak["weak_label"]
        if weak["text"] != row["text"] or name not in WEAK_LABEL_IDS:
            raise ValueError(f"训练弱标签正文或标签无效：{row['id']}")
        counts[name] += 1
        if name != "abstain":
            train.append(row)
            targets.append(WEAK_LABEL_IDS[name])
    if set(targets) != set(range(CARDINALITY)):
        raise ValueError(f"非弃权训练弱标签必须包含任务的全部类别：{weak_path}")
    if len(train) != upstream["train"]["n_labeled"]:
        raise ValueError(f"训练弱标签计数与上游指标不匹配：{weak_path}")
    return train, np.asarray(targets), counts, upstream


def input_hashes(folder, version_dir, aggregator):
    output = version_dir / aggregator
    paths = [output / "weak_labels.csv", output / "metrics.json", version_dir / "lfs.py",
             folder / "data" / "split.json", *[folder / "data" / name for name in CSV_FILENAMES.values()]]
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def score(rows, predictions):
    gold = np.asarray([label_id(row["label"]) for row in rows])
    return {
        **classification_metrics(gold, predictions),
        "confusion_matrix": confusion_matrix(gold, predictions, labels=list(range(CARDINALITY))).tolist(),
    }
