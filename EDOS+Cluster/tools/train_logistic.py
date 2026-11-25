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
    from .evaluate import classification_metrics
    from .task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, TFIDF_CONFIG, label_id
else:
    from task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, TFIDF_CONFIG, label_id
    from data import load_data, read_csv, write_csv
    from evaluate import classification_metrics


TASK_DIR = Path(__file__).resolve().parents[1]
LABELS = {"abstain": -1, **{name: i for i, name in enumerate(CLASS_NAMES)}}
SELECTION_TITLE = f"{CLASS_TITLES[1]} F1" if CARDINALITY == 2 else "Macro-F1"
SOLVER = "liblinear" if CARDINALITY == 2 else "lbfgs"
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
    if set(targets) != set(range(CARDINALITY)):
        raise ValueError("非弃权训练弱标签必须包含任务的全部类别")
    if len(train) != upstream["train"]["n_labeled"]:
        raise ValueError("训练弱标签条数与上游指标不匹配")
    return train, np.asarray(targets), counts, upstream


def score(rows, predictions):
    gold = np.asarray([label_id(row["label"]) for row in rows])
    return {
        **classification_metrics(gold, predictions),
        "confusion_matrix": confusion_matrix(gold, predictions, labels=list(range(CARDINALITY))).tolist(),
    }


def train_model(version_dir, aggregator, splits, metadata, *, tuning_split="valid", development_manifest=None):
    train, targets, counts, upstream = training_data(version_dir, aggregator, splits, metadata)
    if development_manifest and upstream.get("development_roles") != development_manifest:
        raise ValueError("训练上游指标与指定的开发角色不一致")
    vectorizer = TfidfVectorizer(**TFIDF_CONFIG, max_features=100000, sublinear_tf=True)
    x_train = vectorizer.fit_transform([row["text"] for row in train])
    x_valid = vectorizer.transform([row["text"] for row in splits[tuning_split]])
    tuning, best_model, best_score = [], None, -1.0
    for c_value in C_VALUES:
        model = LogisticRegression(C=c_value, solver=SOLVER, max_iter=2000, random_state=SEED)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, targets)
        valid_score = score(splits[tuning_split], model.predict(x_valid))
        selection_score = valid_score[CLASS_NAMES[1]]["f1"] if CARDINALITY == 2 else valid_score["macro_f1"]
        tuning.append({"C": c_value, "selection_score": selection_score,
                       "macro_f1": valid_score["macro_f1"], "accuracy": valid_score["accuracy"]})
        print(f"{version_dir.name} {aggregator} C={c_value:g}: "
              f"{tuning_split} {SELECTION_TITLE}={selection_score:.4f}", flush=True)
        # 严格提升才更新；同分保留 C 网格中的第一个。
        if selection_score > best_score:
            best_model, best_score = model, selection_score

    pipeline = Pipeline([("tfidf", vectorizer), ("logreg", best_model)])
    probabilities = {
        split: pipeline.predict_proba([row["text"] for row in splits[split]])
        for split in ("valid", "selection", "test") if split in splits
    }
    inputs = [version_dir / aggregator / "weak_labels.csv",
              version_dir / aggregator / "metrics.json", version_dir / "lfs.py",
              TASK_DIR / "data" / "split.json",
              *[TASK_DIR / "data" / f"{split}.csv" for split in ("train", "valid", "test") if split in splits]]
    result = {
        "lf_version": version_dir.name,
        "aggregator": aggregator,
        "model": "tfidf_logistic_regression",
        "train": {"total": len(splits["train"]), "used": len(train),
                  "abstained_removed": counts["abstain"],
                  **{name: counts[name] for name in CLASS_NAMES}},
        "config": {
            "sklearn_version": sklearn.__version__,
            "vectorizer": {**TFIDF_CONFIG, "max_features": 100000,
                           "sublinear_tf": True, "vocabulary_size": len(vectorizer.vocabulary_)},
            "logistic_regression": {"C": best_model.C, "solver": SOLVER, "max_iter": 2000,
                                    "random_state": SEED, "class_weight": None},
            "training_target": "non-abstained train hard weak_label",
            "selection": f"{tuning_split} {SELECTION_TITLE}; ties use first C",
            "development_roles": development_manifest,
            "prediction": "argmax; no abstention",
            "class_order": list(CLASS_NAMES),
            "upstream_label_model": upstream["label_model"],
        },
        "tuning": tuning,
        **{split: score(splits[split], probs.argmax(axis=1))
           for split, probs in probabilities.items()},
        "input_sha256": {(path.relative_to(TASK_DIR).as_posix() if path.is_relative_to(TASK_DIR) else path.as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in inputs},
    }
    output = version_dir / aggregator / "tfidf_lr_results"
    output.mkdir(exist_ok=True)
    joblib.dump(pipeline, output / "model.joblib", compress=3)
    for split, probs in probabilities.items():
        predicted = probs.argmax(axis=1)
        rows = [{"id": row["id"], "gold": row["label"], "prediction": CLASS_NAMES[predicted[i]],
                 **{f"p_{name}": float(probs[i, j]) for j, name in enumerate(CLASS_NAMES)}}
                for i, row in enumerate(splits[split])]
        write_csv(output / f"{split}_predictions.csv", rows,
                  ["id", "gold", "prediction", *[f"p_{name}" for name in CLASS_NAMES]])
    (output / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [f"# {version_dir.name} / {aggregator} / TF-IDF + LR", "",
             f"训练 {len(train)}/{len(splits['train'])} 条；移除弃权 {counts['abstain']} 条。", "",
             f"{TFIDF_CONFIG['analyzer']} {TFIDF_CONFIG['ngram_range']} gram；{tuning_split} {SELECTION_TITLE} 选 C={best_model.C:g}；seed={SEED}；完整预测、不弃权。", "",
             "| 集合 | 条数 | Accuracy | Macro-F1 | " + " | ".join(f"{title} F1" for title in CLASS_TITLES) + " |",
             "| --- | ---: | ---: | ---: | " + " | ".join("---:" for _ in CLASS_NAMES) + " |"]
    for split in ("valid", "selection", "test"):
        if split not in result:
            continue
        metrics = result[split]
        cells = [split, str(metrics['n_samples']), f"{metrics['accuracy']:.2%}", f"{metrics['macro_f1']:.2%}",
                 *[f"{metrics[name]['f1']:.2%}" for name in CLASS_NAMES]]
        lines.append("| " + " | ".join(cells) + " |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{aggregator}: test accuracy={result['test']['accuracy']:.4f}, "
          f"macro F1={result['test']['macro_f1']:.4f}; {output}", flush=True)
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--run-dir", type=Path, help="从新运行的已接受版本训练，使用选择集选 C")
    parser.add_argument("--aggregator", nargs="+", choices=("Snorkel", "simple_vote"),
                        default=["Snorkel", "simple_vote"])
    args = parser.parse_args()
    if not re.fullmatch(r"v[1-9]\d*|gpt_lfs", args.version):
        parser.error("版本格式应为 v5 或 gpt_lfs")
    splits = load_data(TASK_DIR)
    metadata = json.loads((TASK_DIR / "data" / "split.json").read_text(encoding="utf-8"))
    root, roles, tuning_split = TASK_DIR, None, "valid"
    if args.run_dir:
        from importlib import import_module
        data_module = import_module(f"{__package__}.data") if __package__ else import_module("data")
        root = args.run_dir.resolve()
        roles = json.loads((root / "development_roles.json").read_text(encoding="utf-8"))
        expected = data_module.development_roles(splits["valid"], metadata["csv_sha256"]["valid"],
                                                 selection_fraction=roles["selection_fraction"], seed=roles["seed"])
        if roles != expected:
            raise ValueError("开发角色清单与冻结数据不一致")
        original_valid = splits["valid"]
        splits["valid"] = [r for r in original_valid if roles["assignments"][r["id"]] == "discovery"]
        splits["selection"] = [r for r in original_valid if roles["assignments"][r["id"]] == "selection"]
        tuning_split = "selection"
        if not (root / "versions" / args.version / "lfs.py").is_file():
            raise ValueError("该版本不在运行目录的已接受版本中")
    for aggregator in args.aggregator:
        train_model(root / "versions" / args.version, aggregator, splits, metadata,
                    tuning_split=tuning_split, development_manifest=data_module.role_identity(roles) if roles else None)


if __name__ == "__main__":
    main()
