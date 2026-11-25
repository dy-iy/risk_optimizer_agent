"""用指定版本的非弃权训练硬弱标签，分别训练 TF-IDF + 逻辑回归。"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import warnings

sys.dont_write_bytecode = True

import joblib
import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from sklearn.pipeline import Pipeline

if __package__:
    from .data import load_data, read_csv, write_csv
    from .decision import was_rejected
    from .evaluate import classification_metrics
else:
    from data import load_data, read_csv, write_csv
    from decision import was_rejected
    from evaluate import classification_metrics


BENCHMARK_DIR = Path(__file__).resolve().parents[1]
LABELS = {"ham": 0, "spam": 1, "abstain": -1}
CLASS_NAMES = ("ham", "spam")
C_VALUES = (0.1, 1.0, 10.0)
SEED = 42


def training_data(version_dir, aggregator, splits, metadata):
    weak_path = version_dir / aggregator / "weak_labels.csv"
    upstream = json.loads((weak_path.parent / "metrics.json").read_text(encoding="utf-8"))
    if (upstream["lf_version"] != version_dir.name
            or upstream["aggregator"] != aggregator
            or upstream["data_split"]["csv_sha256"] != metadata["csv_sha256"]):
        raise ValueError("上游版本、聚合器或数据划分不匹配")
    weak_train = [row for row in read_csv(weak_path) if row["split"] == "train"]
    by_id = {row["id"]: row for row in weak_train}
    if (len(by_id) != len(weak_train)
            or set(by_id) != {row["id"] for row in splits["train"]}):
        raise ValueError("训练弱标签存在重复、缺失或多余 ID")
    train, targets = [], []
    counts = Counter()
    for row in splits["train"]:
        weak = by_id[row["id"]]
        if weak["text"] != row["text"]:
            raise ValueError(f"训练弱标签正文不匹配：{row['id']}")
        label = LABELS[weak["weak_label"]]
        counts[weak["weak_label"]] += 1
        if label != -1:
            train.append(row)
            targets.append(label)
    if set(targets) != {0, 1}:
        raise ValueError("非弃权训练弱标签必须同时包含 ham 和 spam")
    if len(train) != upstream["train"]["n_labeled"]:
        raise ValueError("训练弱标签条数与上游指标不匹配")
    return train, np.asarray(targets), counts, upstream


def score(rows, predictions):
    gold = np.asarray([LABELS[row["label"]] for row in rows])
    return {
        **classification_metrics(gold, predictions),
        "confusion_matrix": confusion_matrix(gold, predictions, labels=[0, 1]).tolist(),
    }


def train_model(version_dir, aggregator, splits, metadata):
    train, targets, counts, upstream = training_data(version_dir, aggregator, splits, metadata)
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=100000, sublinear_tf=True)
    x_train = vectorizer.fit_transform([row["text"] for row in train])
    x_valid = vectorizer.transform([row["text"] for row in splits["valid"]])
    tuning, best_model, best_score = [], None, -1.0
    for c_value in C_VALUES:
        model = LogisticRegression(C=c_value, solver="liblinear", max_iter=2000, random_state=SEED)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, targets)
        valid_score = score(splits["valid"], model.predict(x_valid))
        tuning.append({"C": c_value, "spam_f1": valid_score["spam"]["f1"],
                       "macro_f1": valid_score["macro_f1"], "accuracy": valid_score["accuracy"]})
        print(f"{version_dir.name} {aggregator} C={c_value:g}: "
              f"valid spam F1={valid_score['spam']['f1']:.4f}", flush=True)
        # 严格提升才更新；同分保留 C 网格中的第一个。
        if valid_score["spam"]["f1"] > best_score:
            best_model, best_score = model, valid_score["spam"]["f1"]

    pipeline = Pipeline([("tfidf", vectorizer), ("logreg", best_model)])
    probabilities = {
        split: pipeline.predict_proba([row["text"] for row in splits[split]])
        for split in ("valid", "test")
    }
    inputs = [version_dir / aggregator / "weak_labels.csv",
              version_dir / aggregator / "metrics.json", version_dir / "lfs.py",
              BENCHMARK_DIR / "data" / "split.json",
              *[BENCHMARK_DIR / "data" / f"{split}.csv" for split in splits]]
    result = {
        "lf_version": version_dir.name,
        "aggregator": aggregator,
        "model": "tfidf_logistic_regression",
        "train": {"total": len(splits["train"]), "used": len(train),
                  "abstained_removed": counts["abstain"],
                  **{name: counts[name] for name in CLASS_NAMES}},
        "config": {
            "sklearn_version": sklearn.__version__,
            "vectorizer": {"analyzer": "word", "ngram_range": [1, 2], "max_features": 100000,
                           "sublinear_tf": True, "vocabulary_size": len(vectorizer.vocabulary_)},
            "logistic_regression": {"C": best_model.C, "solver": "liblinear", "max_iter": 2000,
                                    "random_state": SEED, "class_weight": None},
            "training_target": "non-abstained train hard weak_label",
            "selection": "valid spam F1; ties use first C",
            "prediction": "argmax; no abstention",
            "class_order": list(CLASS_NAMES),
            "upstream_label_model": upstream["label_model"],
        },
        "tuning": tuning,
        **{split: score(splits[split], probs.argmax(axis=1))
           for split, probs in probabilities.items()},
        "input_sha256": {path.relative_to(BENCHMARK_DIR).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in inputs},
    }
    output = version_dir / aggregator / "tfidf_lr_results"
    output.mkdir(exist_ok=True)
    joblib.dump(pipeline, output / "model.joblib", compress=3)
    for split, probs in probabilities.items():
        predicted = probs.argmax(axis=1)
        rows = [{"id": row["id"], "gold": row["label"], "prediction": CLASS_NAMES[predicted[i]],
                 "p_ham": float(probs[i, 0]), "p_spam": float(probs[i, 1])}
                for i, row in enumerate(splits[split])]
        write_csv(output / f"{split}_predictions.csv", rows,
                  ["id", "gold", "prediction", "p_ham", "p_spam"])
    (output / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [f"# {version_dir.name} / {aggregator} / TF-IDF + LR", "",
             f"训练 {len(train)}/{len(splits['train'])} 条；ham {counts['ham']}，spam {counts['spam']}；移除弃权 {counts['abstain']} 条。", "",
             f"词 1–2 gram；验证集 spam F1 选 C={best_model.C:g}；seed={SEED}；完整预测、不弃权。", "",
             "| 集合 | 条数 | Accuracy | Macro-F1 | Spam Precision | Spam Recall | Spam F1 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for split in ("valid", "test"):
        metrics = result[split]
        spam = metrics["spam"]
        lines.append(f"| {split} | {metrics['n_samples']} | {metrics['accuracy']:.2%} | "
                     f"{metrics['macro_f1']:.2%} | {spam['precision']:.2%} | {spam['recall']:.2%} | {spam['f1']:.2%} |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{aggregator}: test accuracy={result['test']['accuracy']:.4f}, "
          f"macro F1={result['test']['macro_f1']:.4f}, spam F1={result['test']['spam']['f1']:.4f}; {output}", flush=True)
    return result


def best_lf_version(folder, aggregator, metadata):
    """Select by full validation Macro-F1; ties keep the earliest LF version."""
    versions = sorted((p for p in (folder / "versions").iterdir()
                       if p.is_dir() and re.fullmatch(r"v[1-9]\d*", p.name)),
                      key=lambda p: int(p.name[1:]))
    best, best_score = None, -1.0
    for version in versions:
        if was_rejected(version):
            continue
        source = version / aggregator / "metrics.json"
        if not all(p.is_file() for p in (source, source.parent / "weak_labels.csv", version / "lfs.py")):
            continue
        metrics = json.loads(source.read_text(encoding="utf-8"))
        if (metrics["lf_version"] != version.name or metrics["aggregator"] != aggregator
                or metrics["data_split"]["csv_sha256"] != metadata["csv_sha256"]):
            raise ValueError(f"Upstream version, aggregator or data split mismatch: {source}")
        value = metrics["valid"]["all_samples"]["macro_f1"]
        if not np.isfinite(value):
            raise ValueError(f"Invalid validation score: {source}")
        if value > best_score:
            best, best_score = version.name, value
    if best is None:
        raise ValueError(f"No completed LF versions for {aggregator}")
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="LF version; default: best non-rejected version by validation Macro-F1 per aggregator")
    parser.add_argument("--aggregator", nargs="+", choices=("Snorkel", "simple_vote"),
                        default=["Snorkel", "simple_vote"])
    parser.add_argument("--plan", action="store_true", help="Show selected versions without training")
    parser.add_argument("--check-data", action="store_true", help="Validate inputs without training")
    args = parser.parse_args()
    if args.version is not None and not re.fullmatch(r"v[1-9]\d*", args.version):
        parser.error("版本格式应为 v5 等")
    splits = load_data(BENCHMARK_DIR)
    metadata = json.loads((BENCHMARK_DIR / "data" / "split.json").read_text(encoding="utf-8"))
    for aggregator in args.aggregator:
        version = args.version or best_lf_version(BENCHMARK_DIR, aggregator, metadata)
        version_dir = BENCHMARK_DIR / "versions" / version
        print(f"{version} / {aggregator}", flush=True)
        if args.plan:
            continue
        if args.check_data:
            train, targets, counts, upstream = training_data(version_dir, aggregator, splits, metadata)
            print(f"Checked {len(train)} non-abstained training rows; no training or writes.", flush=True)
            continue
        train_model(version_dir, aggregator, splits, metadata)


if __name__ == "__main__":
    main()
