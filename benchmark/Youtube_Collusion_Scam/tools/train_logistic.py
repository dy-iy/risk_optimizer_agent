"""用指定版本的非弃权训练硬弱标签，分别训练 TF-IDF + 逻辑回归。"""

import argparse
import json
from pathlib import Path
import re
import sys
import warnings

sys.dont_write_bytecode = True

import joblib
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

if __package__:
    from .data import load_data, write_csv
    from .downstream import AGGREGATORS, EXPLICIT_VERSION, input_hashes, score, select_versions, training_data
    from .task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, TFIDF_CONFIG
else:
    from task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, TFIDF_CONFIG
    from data import load_data, write_csv
    from downstream import AGGREGATORS, EXPLICIT_VERSION, input_hashes, score, select_versions, training_data


TASK_DIR = Path(__file__).resolve().parents[1]
SELECTION_TITLE = f"{CLASS_TITLES[1]} F1" if CARDINALITY == 2 else "Macro-F1"
SOLVER = "liblinear" if CARDINALITY == 2 else "lbfgs"
C_VALUES = (0.1, 1.0, 10.0)
SEED = 42


def train_model(version_dir, aggregator, splits, metadata, *, selection=None):
    train, targets, counts, upstream = training_data(version_dir, aggregator, splits, metadata)
    vectorizer = TfidfVectorizer(**TFIDF_CONFIG, max_features=100000, sublinear_tf=True)
    x_train = vectorizer.fit_transform([row["text"] for row in train])
    x_valid = vectorizer.transform([row["text"] for row in splits["valid"]])
    tuning, best_model, best_score = [], None, -1.0
    for c_value in C_VALUES:
        model = LogisticRegression(C=c_value, solver=SOLVER, max_iter=2000, random_state=SEED)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, targets)
        valid_score = score(splits["valid"], model.predict(x_valid))
        selection_score = valid_score[CLASS_NAMES[1]]["f1"] if CARDINALITY == 2 else valid_score["macro_f1"]
        tuning.append({"C": c_value, "selection_score": selection_score,
                       "macro_f1": valid_score["macro_f1"], "accuracy": valid_score["accuracy"]})
        print(f"{version_dir.name} {aggregator} C={c_value:g}: "
              f"valid {SELECTION_TITLE}={selection_score:.4f}", flush=True)
        # 严格提升才更新；同分保留 C 网格中的第一个。
        if selection_score > best_score:
            best_model, best_score = model, selection_score

    pipeline = Pipeline([("tfidf", vectorizer), ("logreg", best_model)])
    probabilities = {
        split: pipeline.predict_proba([row["text"] for row in splits[split]])
        for split in ("valid", "test")
    }
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
            "selection": f"valid {SELECTION_TITLE}; ties use first C",
            "prediction": "argmax; no abstention",
            "class_order": list(CLASS_NAMES),
            "upstream_label_model": upstream["label_model"],
        },
        "tuning": tuning,
        **{split: score(splits[split], probs.argmax(axis=1))
           for split, probs in probabilities.items()},
        "input_sha256": input_hashes(TASK_DIR, version_dir, aggregator),
    }
    if selection is not None:
        result["lf_selection"] = selection
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
             f"{TFIDF_CONFIG['analyzer']} {TFIDF_CONFIG['ngram_range']} gram；验证集 {SELECTION_TITLE} 选 C={best_model.C:g}；seed={SEED}；完整预测、不弃权。", "",
             "| 集合 | 条数 | Accuracy | Macro-F1 | " + " | ".join(f"{title} F1" for title in CLASS_TITLES) + " |",
             "| --- | ---: | ---: | ---: | " + " | ".join("---:" for _ in CLASS_NAMES) + " |"]
    for split in ("valid", "test"):
        metrics = result[split]
        cells = [split, str(metrics['n_samples']), f"{metrics['accuracy']:.2%}", f"{metrics['macro_f1']:.2%}",
                 *[f"{metrics[name]['f1']:.2%}" for name in CLASS_NAMES]]
        lines.append("| " + " | ".join(cells) + " |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{aggregator}: test accuracy={result['test']['accuracy']:.4f}, "
          f"macro F1={result['test']['macro_f1']:.4f}; {output}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="LF 版本，默认按各聚合器的验证集全量 Macro-F1 在未被拒绝的版本中选版")
    parser.add_argument("--aggregator", "--aggregators", nargs="+", choices=AGGREGATORS, default=list(AGGREGATORS))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="仅显示所选版本")
    mode.add_argument("--check-data", action="store_true", help="校验训练弱标签")
    args = parser.parse_args()
    if args.version is not None and not re.fullmatch(EXPLICIT_VERSION, args.version):
        parser.error("版本格式应为 v5 或 gpt_lfs")
    splits = load_data(TASK_DIR)
    metadata = json.loads((TASK_DIR / "data" / "split.json").read_text(encoding="utf-8"))
    for selection in select_versions(TASK_DIR, args.aggregator, metadata, args.version):
        aggregator, version = selection["aggregator"], selection["version"]
        version_dir = TASK_DIR / "versions" / version
        print(f"{version} / {aggregator}: LF valid Macro-F1={selection['valid_macro_f1']:.6f}", flush=True)
        if args.plan:
            continue
        if args.check_data:
            train, _, counts, _ = training_data(version_dir, aggregator, splits, metadata)
            print(f"Checked {len(train)} non-abstained training rows: {dict(counts)}", flush=True)
            continue
        train_model(version_dir, aggregator, splits, metadata, selection=selection)


if __name__ == "__main__":
    main()
