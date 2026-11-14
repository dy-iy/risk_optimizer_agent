"""参考 SMS：非弃权训练弱标签 -> 字符 TF-IDF + LR -> 验证正类 F1 选 C -> 完整测试评价。"""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import warnings

sys.dont_write_bytecode = True

if __package__:
    from .data import load_data, parse_inputs
else:
    from data import load_data, parse_inputs


FINNSP_DIR = Path(__file__).resolve().parents[1]
LABEL2ID = {"0": 0, "1": 1}
ID2LABEL = {value: key for key, value in LABEL2ID.items()}
INPUT_FORMAT = "正文：{text}\n候选实体：{entities joined by ；}"


def parse_args(version_dir=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=version_dir.name if version_dir else None)
    parser.add_argument("--weak-labels", type=Path)
    parser.add_argument("--data-dir", type=Path, default=FINNSP_DIR / "data")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--c-values", type=float, nargs="+", default=[0.1, 1.0, 10.0],
                        help="按完整 valid 的正类 1 F1 选择 C，与 SMS 的正类 F1 口径一致")
    parser.add_argument("--ngram-max", type=int, default=2,
                        help="中文字符 1-gram 到 n-gram，默认 2")
    parser.add_argument("--max-features", type=int, default=100000)
    parser.add_argument("--max-iter", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--check-data", action="store_true", help="只校验输入，不训练、不写文件")
    args = parser.parse_args()
    if not args.version or not re.fullmatch(r"v[1-9]\d*", args.version):
        parser.error("请指定 --version v4 等有效版本")
    if (any(not (0 < c < float("inf")) for c in args.c_values)
            or min(args.ngram_max, args.max_features, args.max_iter) < 1):
        parser.error("C 必须有限且大于 0，ngram-max/max-features/max-iter 必须为正整数")
    args.version_dir = FINNSP_DIR / "versions" / args.version
    args.weak_labels = args.weak_labels or args.version_dir / "results/weak_labels.csv"
    args.output_dir = args.output_dir or args.version_dir / "tfidf_lr_results"
    return args


def read_weak_labels(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"split", "id", "text", "inputs", "weak_label"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f"{path} 缺少弱标签字段 {sorted(required)}")
        rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("弱标签文件为空或列数不一致")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or any(not value.strip() for value in ids):
        raise ValueError("弱标签文件存在重复或空 ID")
    if any(row["split"] not in {"train", "valid", "test"} for row in rows):
        raise ValueError("弱标签文件存在未知 split")
    return rows


def model_input(row):
    """只序列化正文与候选实体，不包含标签、概率、编号或来源。"""
    entities = parse_inputs(row["inputs"])["entities"]
    return "正文：" + row["text"] + "\n候选实体：" + "；".join(entities)


def overlap_count(left, right, key):
    seen = {key(row) for row in left}
    return sum(key(row) in seen for row in right)


def prepare_data(args):
    # 复用清单校验，防止版本弱标签与已冻结划分混用。
    if args.data_dir.resolve().name != "data":
        raise ValueError("--data-dir 必须指向包含固定清单的 data 目录")
    splits = load_data(args.data_dir.resolve().parent)
    metadata_path = args.data_dir / "split.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metrics_path = args.version_dir / "results/metrics.json"
    source_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    expected = {key: metadata[key] for key in ("dataset", "revision", "counts", "csv_sha256")}
    if source_metrics.get("lf_version") != args.version or source_metrics.get("data_split") != expected:
        raise ValueError("版本指标与当前数据划分不一致，请先评价对应 LF 版本")
    weak = read_weak_labels(args.weak_labels)
    by_split = {name: {row["id"]: row for row in weak if row["split"] == name} for name in splits}
    for name, rows in splits.items():
        by_id = by_split[name]
        if set(by_id) != {row["id"] for row in rows}:
            raise ValueError(f"弱标签 {name} ID 与固定数据不一致")
        for row in rows:
            weak_row = by_id[row["id"]]
            if (weak_row["text"] != row["text"]
                    or parse_inputs(weak_row["inputs"]) != row["inputs"]):
                raise ValueError(f"弱标签 {name} 正文或候选实体与固定数据不一致")
    counts = Counter(row["weak_label"] for row in by_split["train"].values())
    if set(counts) - {"0", "1", "abstain"}:
        raise ValueError("训练弱标签只能是 0、1、abstain")
    cleaned = []
    for source in splits["train"]:
        row = by_split["train"][source["id"]]
        if row["weak_label"] == "abstain":
            continue
        cleaned.append({"split": "train", "id": source["id"], "text": source["text"],
                        "inputs": json.dumps(source["inputs"], ensure_ascii=False),
                        "model_input": model_input(source), "weak_label": row["weak_label"],
                        "label_id": LABEL2ID[row["weak_label"]]})
    if {row["label_id"] for row in cleaned} != {0, 1}:
        raise ValueError("非弃权的训练弱标签必须同时包含 0 和 1")
    exact_input = lambda row: (row["text"], tuple(parse_inputs(row["inputs"])["entities"]))
    source_paths = [args.weak_labels, metrics_path, metadata_path,
                    *[args.data_dir / f"{name}.csv" for name in splits]]
    summary = {
        "lf_version": args.version,
        "train_rows_before_cleaning": len(splits["train"]),
        "train_abstain_removed": counts["abstain"], "train_rows_after_cleaning": len(cleaned),
        "train_class_counts": {label: counts[label] for label in LABEL2ID},
        "ignored_weak_rows": len(weak) - len(splits["train"]),
        "valid_rows": len(splits["valid"]), "test_rows": len(splits["test"]),
        "label2id": LABEL2ID, "model_input_format": INPUT_FORMAT,
        "training_target": "hard weak_label from train only; p_0/p_1 are not training targets",
        "validation_role": "gold selects C only; not used to fit TF-IDF or logistic coefficients",
        "upstream_weak_label_validation_gold_usage": source_metrics["label_model"].get("validation_gold_usage"),
        "test_role": "predict all 500 rows after C selection; gold for final metrics only",
        "data_split": expected,
        "overlap_audit": {
            "test_rows_with_exact_text_and_entities_in_cleaned_train": overlap_count(cleaned, splits["test"], exact_input),
            "valid_rows_with_exact_text_and_entities_in_cleaned_train": overlap_count(cleaned, splits["valid"], exact_input),
            "valid_rows_with_same_text_ignoring_whitespace_in_cleaned_train": overlap_count(
                cleaned, splits["valid"], lambda row: re.sub(r"\s+", "", row["text"])),
            "policy": "retain the confirmed frozen split; report existing source overlap without resplitting",
        },
        "input_sha256": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
    }
    return cleaned, splits["valid"], splits["test"], summary


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def classification_metrics(labels, probabilities):
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    predicted = probabilities.argmax(axis=1)
    return {
        "n_samples": len(labels), "accuracy": float(accuracy_score(labels, predicted)),
        "positive_f1": float(f1_score(labels, predicted, pos_label=1, zero_division=0)),
        "macro_f1": float(f1_score(labels, predicted, labels=[0, 1], average="macro", zero_division=0)),
        "classification_report": classification_report(labels, predicted, labels=[0, 1],
                                                         target_names=["0", "1"], output_dict=True, zero_division=0),
        "confusion_matrix_labels": ["0", "1"],
        "confusion_matrix_rows_true_columns_predicted": confusion_matrix(labels, predicted, labels=[0, 1]).tolist(),
    }


def prediction_rows(rows, probabilities):
    predicted = probabilities.argmax(axis=1)
    return [{"id": row["id"], "text": row["text"],
             "inputs": json.dumps(row["inputs"], ensure_ascii=False),
             "pred_label": ID2LABEL[int(predicted[i])], "pred_label_id": int(predicted[i]),
             "p_0": float(probabilities[i, 0]), "p_1": float(probabilities[i, 1]),
             "true_label": row["label"], "correct": bool(predicted[i] == int(row["label"]))}
            for i, row in enumerate(rows)]


def train_and_predict(args, prepared):
    import joblib
    import numpy as np
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    train, valid, test, summary = prepared
    output = args.output_dir.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"输出目录非空，请用 --output-dir 指定新目录：{output}")
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, args.ngram_max),
                                 max_features=args.max_features, sublinear_tf=True, lowercase=True)
    # TF-IDF 的词表与 IDF 只拟合非弃权训练文本；验证、测试均只 transform。
    X_train = vectorizer.fit_transform([row["model_input"] for row in train])
    X_valid = vectorizer.transform([model_input(row) for row in valid])
    y_train = np.array([row["label_id"] for row in train], dtype=int)
    y_valid = np.array([int(row["label"]) for row in valid], dtype=int)
    tuning = []
    best_model, best_c, best_f1 = None, None, -1.0
    for c in args.c_values:
        model = LogisticRegression(C=c, solver="liblinear", max_iter=args.max_iter, random_state=args.seed)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(X_train, y_train)
        if list(model.classes_) != [0, 1]:
            raise RuntimeError("Logistic Regression 的概率类别顺序不正确")
        metrics = classification_metrics(y_valid, model.predict_proba(X_valid))
        tuning.append({"C": c, "valid_positive_f1": metrics["positive_f1"],
                       "valid_macro_f1": metrics["macro_f1"], "valid_accuracy": metrics["accuracy"],
                       "n_iter": model.n_iter_.tolist()})
        print(f"{args.version} C={c:g}: valid positive F1={metrics['positive_f1']:.4f}, "
              f"Macro-F1={metrics['macro_f1']:.4f}, accuracy={metrics['accuracy']:.4f}", flush=True)
        # 同分时采用输入列表中第一个 C，完全沿用 SMS 的选择规则。
        if metrics["positive_f1"] > best_f1:
            best_model, best_c, best_f1 = model, c, metrics["positive_f1"]
    pipeline = Pipeline([("tfidf", vectorizer), ("logreg", best_model)])
    valid_probs = pipeline.predict_proba([model_input(row) for row in valid])
    test_probs = pipeline.predict_proba([model_input(row) for row in test])
    if not (np.isfinite(valid_probs).all() and np.isfinite(test_probs).all()):
        raise RuntimeError("模型输出存在无效概率")
    # 测试标签仅在选定 C、完成预测后用于最终评分。
    y_test = np.array([int(row["label"]) for row in test], dtype=int)
    result = {
        "lf_version": args.version, "best_C": best_c, "best_valid_positive_f1": best_f1,
        "selection_metric": "valid positive F1 (label 1); ties use first C in input order",
        "prediction_rule": "argmax over [p_0, p_1]; no abstention",
        "valid": classification_metrics(y_valid, valid_probs),
        "test": classification_metrics(y_test, test_probs),
    }
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "train_cleaned.csv", train)
    write_csv(output / "valid_predictions.csv", prediction_rows(valid, valid_probs))
    write_csv(output / "test_predictions.csv", prediction_rows(test, test_probs))
    write_json(output / "data_summary.json", summary)
    write_json(output / "run_config.json", {
        **{key: str(value.resolve()) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "sklearn_version": sklearn.__version__, "reference": "SMS/versions/v15/train_logistic.py",
        "vectorizer": {"analyzer": "char", "ngram_range": [1, args.ngram_max],
                       "max_features": args.max_features, "sublinear_tf": True, "lowercase": True},
        "logistic_regression": {"solver": "liblinear", "max_iter": args.max_iter, "random_state": args.seed},
        "model_input_format": INPUT_FORMAT, "vectorizer_vocabulary_size": len(vectorizer.vocabulary_),
    })
    write_json(output / "tuning_history.json", tuning)
    write_json(output / "metrics.json", result)
    joblib.dump(pipeline, output / "model.joblib")
    lines = [f"# FinNSP {args.version}：TF-IDF + 逻辑回归", "",
             f"弱标签训练样本 {len(train):,} 条，移除弃权 {summary['train_abstain_removed']:,} 条；最佳 C={best_c:g}。", "",
             "沿用 SMS：只用训练硬弱标签拟合，验证正类 F1 选 C，最后在完整测试集评价。", "",
             f"中文适配：正文和候选实体序列化为输入，字符 1–{args.ngram_max} gram；TF-IDF 词表与 IDF 仅在非弃权训练样本上拟合。", "",
             "| 集合 | 条数 | 准确率 | 正类 F1 | Macro-F1 |", "| --- | ---: | ---: | ---: | ---: |"]
    for name, title in (("valid", "验证"), ("test", "测试")):
        values = result[name]
        lines.append(f"| {title} | {values['n_samples']} | {values['accuracy']:.2%} | {values['positive_f1']:.2%} | {values['macro_f1']:.2%} |")
    lines.extend(["", f"测试集中有 {summary['overlap_audit']['test_rows_with_exact_text_and_entities_in_cleaned_train']} 条与本版训练输入完全重复，沿用已确认划分，未重分或去重。", "",
                  "`model.joblib` 包含 TF-IDF 与已选逻辑回归模型；推理输入按 `run_config.json` 的 model_input_format 序列化。", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{args.version}: best C={best_c:g}, test accuracy={result['test']['accuracy']:.4f}, "
          f"positive F1={result['test']['positive_f1']:.4f}, Macro-F1={result['test']['macro_f1']:.4f}", flush=True)
    print(f"Saved: {output}", flush=True)
    return result


def main(version_dir=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(version_dir)
    prepared = prepare_data(args)
    summary = prepared[-1]
    print(f"{args.version}: keep {summary['train_rows_after_cleaning']} train rows, "
          f"remove {summary['train_abstain_removed']} abstentions, "
          f"valid={summary['valid_rows']}, test={summary['test_rows']}", flush=True)
    if not args.check_data:
        train_and_predict(args, prepared)


if __name__ == "__main__":
    main()
