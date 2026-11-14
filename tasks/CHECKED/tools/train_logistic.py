"""参考 Finnsp：非弃权训练硬弱标签拟合 TF-IDF + LR，验证选 C，完整测试评价。"""

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
    from .data import load_data, split_identity
    from .task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, DATASET
else:
    from data import load_data, split_identity
    from task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, DATASET


TASK_DIR = Path(__file__).resolve().parents[1]
LABEL2ID = {name: i for i, name in enumerate(CLASS_NAMES)}
DEFAULT_ANALYZER = "char" if DATASET == "CHECKED" else "word"
SELECTION_METRIC = "positive_f1" if CARDINALITY == 2 else "macro_f1"
SELECTION_TITLE = f"{CLASS_TITLES[1]} F1" if CARDINALITY == 2 else "Macro-F1"


def parse_args(version_dir=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=version_dir.name if version_dir else None)
    parser.add_argument("--weak-labels", type=Path)
    parser.add_argument("--data-dir", type=Path, default=TASK_DIR / "data")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--c-values", type=float, nargs="+", default=[0.1, 1.0, 10.0],
                        help=f"按完整验证集 {SELECTION_TITLE} 选 C，同分取列表中第一个")
    parser.add_argument("--ngram-max", type=int, default=2)
    parser.add_argument("--max-features", type=int, default=100000)
    parser.add_argument("--max-iter", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--check-data", action="store_true", help="只核验输入，不训练或写文件")
    args = parser.parse_args()
    if not args.version or not re.fullmatch(r"v[1-9]\d*", args.version):
        parser.error("请指定 --version v10 等有效版本")
    if (any(not (0 < value < float("inf")) for value in args.c_values)
            or min(args.ngram_max, args.max_features, args.max_iter) < 1):
        parser.error("C 必须有限且大于 0；ngram-max/max-features/max-iter 必须为正整数")
    args.version_dir = TASK_DIR / "versions" / args.version
    args.weak_labels = args.weak_labels or args.version_dir / "results/weak_labels.csv"
    args.output_dir = args.output_dir or args.version_dir / "tfidf_lr_results"
    return args


def read_weak_labels(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not {"split", "id", "text", "weak_label"} <= set(header):
            raise ValueError("弱标签文件表头缺失必需字段或含重复字段")
        rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("弱标签文件为空或列数不一致")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids) or any(not value.strip() for value in ids):
        raise ValueError("弱标签文件存在重复或空 ID")
    if any(row["split"] not in {"train", "valid", "test"} for row in rows):
        raise ValueError("弱标签文件含未知 split")
    return rows


def overlap_count(left, right, normalize=False):
    key = (lambda row: re.sub(r"\s+", "", row["text"])) if normalize else (lambda row: row["text"])
    seen = {key(row) for row in left}
    return sum(key(row) in seen for row in right)


def prepare_data(args):
    if args.data_dir.resolve().name != "data":
        raise ValueError("--data-dir 必须指向包含冻结清单的 data 目录")
    splits = load_data(args.data_dir.resolve().parent)
    metadata_path = args.data_dir / "split.json"
    source_metrics_path = args.version_dir / "results/metrics.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_metrics = json.loads(source_metrics_path.read_text(encoding="utf-8"))
    expected = split_identity(metadata)
    if source_metrics.get("lf_version") != args.version or source_metrics.get("data_split") != expected:
        raise ValueError("当前版本的上游指标与冻结划分不一致，请先评价对应 LF 版本")
    weak = read_weak_labels(args.weak_labels)
    by_split = {split: {row["id"]: row for row in weak if row["split"] == split} for split in splits}
    for split, source in splits.items():
        by_id = by_split[split]
        if set(by_id) != {row["id"] for row in source}:
            raise ValueError(f"弱标签 {split} ID 与冻结数据不一致")
        if any(by_id[row["id"]]["text"] != row["text"] for row in source):
            raise ValueError(f"弱标签 {split} 正文与冻结数据不一致")
    weak_train = by_split["train"]
    counts = Counter(row["weak_label"] for row in weak_train.values())
    if set(counts) - {*CLASS_NAMES, "abstain"}:
        raise ValueError("训练硬弱标签超出任务类别范围")
    cleaned = []
    for source in splits["train"]:
        label = weak_train[source["id"]]["weak_label"]
        if label != "abstain":
            cleaned.append({"split": "train", "id": source["id"], "text": source["text"],
                            "weak_label": label, "label_id": LABEL2ID[label]})
    if {row["label_id"] for row in cleaned} != set(range(CARDINALITY)):
        raise ValueError("非弃权训练弱标签必须包含所有任务类别")
    if (len(cleaned) != source_metrics["train"]["n_labeled"]
            or counts["abstain"] != source_metrics["train"]["n_abstained"]):
        raise ValueError("弱标签训练条数与当前上游指标不一致")
    source_paths = [args.weak_labels, source_metrics_path, metadata_path,
                    args.version_dir / "lfs.py", *[args.data_dir / f"{split}.csv" for split in splits]]
    summary = {
        "dataset": DATASET, "lf_version": args.version,
        "train_rows_before_cleaning": len(splits["train"]),
        "train_abstain_removed": counts["abstain"], "train_rows_after_cleaning": len(cleaned),
        "train_class_counts": {CLASS_TITLES[i]: counts[name] for i, name in enumerate(CLASS_NAMES)},
        "ignored_weak_rows": len(weak) - len(weak_train),
        "valid_rows": len(splits["valid"]), "test_rows": len(splits["test"]),
        "label2id": {title: i for i, title in enumerate(CLASS_TITLES)},
        "probability_class_order": list(CLASS_TITLES), "model_input_format": "text only",
        "training_target": "non-abstained train hard weak_label only; probability columns are not targets",
        "validation_role": f"gold selects C by {SELECTION_TITLE}; TF-IDF and LR fit only cleaned train",
        "test_role": "predict all test rows after C selection; test gold used for final scoring only",
        "upstream_weak_label_validation_gold_usage": source_metrics["label_model"].get("validation_gold_usage"),
        "upstream_class_balance": source_metrics["label_model"].get("class_balance"),
        "upstream_gold_used_for_fit": source_metrics["label_model"].get("gold_used_for_fit"),
        "data_split": expected,
        "overlap_audit": {
            "valid_rows_with_exact_text_in_cleaned_train": overlap_count(cleaned, splits["valid"]),
            "test_rows_with_exact_text_in_cleaned_train": overlap_count(cleaned, splits["test"]),
            "valid_rows_with_same_text_ignoring_whitespace_in_cleaned_train": overlap_count(cleaned, splits["valid"], True),
            "test_rows_with_same_text_ignoring_whitespace_in_cleaned_train": overlap_count(cleaned, splits["test"], True),
            "policy": "preserve the confirmed frozen split; report overlap without deduplicating or resplitting",
        },
        "input_sha256": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
    }
    return cleaned, splits["valid"], splits["test"], summary, source_metrics


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def classification_metrics(labels, probabilities):
    import numpy as np
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    probabilities = np.asarray(probabilities)
    if (probabilities.shape != (len(labels), CARDINALITY)
            or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()
            or not np.allclose(probabilities.sum(axis=1), 1)):
        raise ValueError("预测概率形状、范围或总和无效")
    predicted = probabilities.argmax(axis=1)
    result = {
        "n_samples": len(labels), "coverage": 1.0,
        "accuracy": float(accuracy_score(labels, predicted)),
        "macro_f1": float(f1_score(labels, predicted, labels=list(range(CARDINALITY)), average="macro", zero_division=0)),
        "classification_report": classification_report(
            labels, predicted, labels=list(range(CARDINALITY)), target_names=list(CLASS_TITLES),
            output_dict=True, zero_division=0),
        "confusion_matrix_labels": list(CLASS_TITLES),
        "confusion_matrix_rows_true_columns_predicted": confusion_matrix(
            labels, predicted, labels=list(range(CARDINALITY))).tolist(),
    }
    if CARDINALITY == 2:
        result.update(positive_label=CLASS_TITLES[1],
                      positive_f1=float(f1_score(labels, predicted, pos_label=1, zero_division=0)))
    return result


def prediction_rows(rows, probabilities):
    predicted = probabilities.argmax(axis=1)
    return [{
        "id": row["id"], "text": row["text"],
        "pred_label": CLASS_TITLES[int(predicted[i])], "pred_label_id": int(predicted[i]),
        **{f"p_{label}": float(probabilities[i, label]) for label in range(CARDINALITY)},
        "true_label": CLASS_TITLES[int(row["label"])], "true_label_id": int(row["label"]),
        "correct": bool(predicted[i] == int(row["label"])),
    } for i, row in enumerate(rows)]


def train_and_predict(args, prepared):
    import joblib
    import numpy as np
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.pipeline import Pipeline

    train, valid, test, summary, upstream_metrics = prepared
    output = args.output_dir.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"输出目录非空，请通过 --output-dir 指定新目录：{output}")
    vectorizer = TfidfVectorizer(
        analyzer=DEFAULT_ANALYZER, ngram_range=(1, args.ngram_max),
        max_features=args.max_features, sublinear_tf=True, lowercase=True)
    X_train = vectorizer.fit_transform([row["text"] for row in train])
    X_valid = vectorizer.transform([row["text"] for row in valid])
    y_train = np.asarray([row["label_id"] for row in train], dtype=int)
    y_valid = np.asarray([row["label"] for row in valid], dtype=int)
    tuning = []
    best_model, best_c, best_score = None, None, -1.0
    for c in args.c_values:
        binary = LogisticRegression(C=c, solver="liblinear", max_iter=args.max_iter, random_state=args.seed)
        model = OneVsRestClassifier(binary, n_jobs=1) if CARDINALITY > 2 else binary
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(X_train, y_train)
        if list(model.classes_) != list(range(CARDINALITY)):
            raise RuntimeError("逻辑回归概率列的类别顺序异常")
        score = classification_metrics(y_valid, model.predict_proba(X_valid))
        n_iter = [estimator.n_iter_.tolist() for estimator in model.estimators_] if CARDINALITY > 2 else model.n_iter_.tolist()
        tuning.append({"C": c, "selection_score": score[SELECTION_METRIC],
                       "valid_macro_f1": score["macro_f1"], "valid_accuracy": score["accuracy"],
                       **({"valid_positive_f1": score["positive_f1"]} if CARDINALITY == 2 else {}),
                       "n_iter": n_iter})
        print(f"{DATASET} {args.version} C={c:g}: valid {SELECTION_TITLE}={score[SELECTION_METRIC]:.4f}, "
              f"Macro-F1={score['macro_f1']:.4f}, Acc={score['accuracy']:.4f}", flush=True)
        # 严格大于才更新，同分取输入顺序中第一个 C，与 Finnsp 一致。
        if score[SELECTION_METRIC] > best_score:
            best_model, best_c, best_score = model, c, score[SELECTION_METRIC]
    pipeline = Pipeline([("tfidf", vectorizer), ("logreg", best_model)])
    valid_probs = pipeline.predict_proba([row["text"] for row in valid])
    # 最佳 C 已锁定，此后才预测和评价完整测试集。
    test_probs = pipeline.predict_proba([row["text"] for row in test])
    y_test = np.asarray([row["label"] for row in test], dtype=int)
    result = {
        "dataset": DATASET, "lf_version": args.version, "best_C": best_c,
        "best_valid_selection_score": best_score,
        "selection_metric": f"valid {SELECTION_TITLE}; ties use first C in input order",
        "prediction_rule": "argmax over probability columns in label-id order; no abstention",
        "valid": classification_metrics(y_valid, valid_probs),
        "test": classification_metrics(y_test, test_probs),
    }
    config = {
        **{key: str(value.resolve()) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "reference": "Finnsp/tools/train_logistic.py", "sklearn_version": sklearn.__version__,
        "selection_metric": SELECTION_TITLE, "model_input_format": "text only",
        "vectorizer": {"analyzer": DEFAULT_ANALYZER, "ngram_range": [1, args.ngram_max],
                       "max_features": args.max_features, "sublinear_tf": True, "lowercase": True,
                       "vocabulary_size": len(vectorizer.vocabulary_)},
        "logistic_regression": {"solver": "liblinear", "strategy": "OneVsRestClassifier" if CARDINALITY > 2 else "binary",
                                "max_iter": args.max_iter, "random_state": args.seed,
                                "class_weight": None, "C": best_c},
        "probability_class_order": list(CLASS_TITLES),
    }
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "train_cleaned.csv", train)
    write_csv(output / "valid_predictions.csv", prediction_rows(valid, valid_probs))
    write_csv(output / "test_predictions.csv", prediction_rows(test, test_probs))
    write_json(output / "data_summary.json", summary)
    write_json(output / "run_config.json", config)
    write_json(output / "tuning_history.json", tuning)
    write_json(output / "metrics.json", result)
    joblib.dump(pipeline, output / "model.joblib")
    write_report(output, args, summary, result, upstream_metrics)
    print(f"{DATASET} {args.version}: best C={best_c:g}, test Acc={result['test']['accuracy']:.4f}, "
          f"Macro-F1={result['test']['macro_f1']:.4f}; saved {output}", flush=True)
    return result


def write_report(output, args, summary, result, upstream_metrics):
    gram = "字符" if DEFAULT_ANALYZER == "char" else "词"
    strategy = "One-vs-Rest 包装 liblinear" if CARDINALITY > 2 else "二分类 liblinear"
    lines = [
        f"# {DATASET} {args.version}：TF-IDF + 逻辑回归", "",
        f"训练保留 {summary['train_rows_after_cleaning']:,} 条硬弱标签，移除 {summary['train_abstain_removed']:,} 条弃权。", "",
        f"{gram} 1–{args.ngram_max} gram，最多 {args.max_features:,} 个特征；"
        f"C 网格 {args.c_values}，按验证集 {SELECTION_TITLE} 选出 C={result['best_C']:g}，同分取网格中第一个。", "",
        "TF-IDF 词表、IDF 和 LR 系数只拟合非弃权训练样本；验证金标仅用于下游 C 选择，测试金标仅用于最终评分。"
        "上游 Snorkel 已使用验证集类别比例作为先验，此信息保留在 data_summary.json。", "",
        f"完整验证/测试均输出一个类别，不弃权；使用{strategy}。", "",
        "## 下游指标", "",
        "| 集合 | 条数 | 准确率 | Macro-F1 |", "| --- | ---: | ---: | ---: |",
    ]
    for split, title in (("valid", "验证"), ("test", "测试")):
        score = result[split]
        lines.append(f"| {title} | {score['n_samples']:,} | {score['accuracy']:.2%} | {score['macro_f1']:.2%} |")
    lines.extend(["", "## 预测类别分布", "",
                  "| 集合 | " + " | ".join(CLASS_TITLES) + " |",
                  "| --- | " + " | ".join(["---:"] * CARDINALITY) + " |"])
    distribution_notes = []
    for split, title in (("valid", "验证"), ("test", "测试")):
        confusion = result[split]["confusion_matrix_rows_true_columns_predicted"]
        counts = [sum(row[i] for row in confusion) for i in range(CARDINALITY)]
        lines.append("| " + " | ".join([title, *map(str, counts)]) + " |")
        if sum(count > 0 for count in counts) == 1:
            name = CLASS_TITLES[next(i for i, count in enumerate(counts) if count)]
            distribution_notes.append(f"{title}集全部预测为 {name}，其余类别召回率为 0；整体准确率应结合每类指标解读。")
    for note in distribution_notes:
        lines.extend(["", note])
    lines.extend(["", "## 每类指标", "",
                  "| 集合 | 类别 | Precision | Recall | F1 | Support |", "| --- | --- | ---: | ---: | ---: | ---: |"])
    for split, title in (("valid", "验证"), ("test", "测试")):
        for name in CLASS_TITLES:
            score = result[split]["classification_report"][name]
            lines.append(f"| {title} | {name} | {score['precision']:.2%} | {score['recall']:.2%} | {score['f1-score']:.2%} | {int(score['support'])} |")
    for split, title in (("valid", "验证"), ("test", "测试")):
        lines.extend(["", f"## {title}混淆矩阵", "",
                      "| 真实 / 预测 | " + " | ".join(CLASS_TITLES) + " |",
                      "| --- | " + " | ".join(["---:"] * CARDINALITY) + " |"])
        for name, counts in zip(CLASS_TITLES, result[split]["confusion_matrix_rows_true_columns_predicted"]):
            lines.append("| " + " | ".join([name, *map(str, counts)]) + " |")
    lines.extend(["", "## 与上游 LF 聚合比较", "",
                  "上游为全量口径，包含弃权；下游对完整集合预测，输出覆盖率为 100%。", "",
                  "| 集合 | 方法 | 输出覆盖率 | 准确率 | Macro-F1 |",
                  "| --- | --- | ---: | ---: | ---: |"])
    for split, title in (("valid", "验证"), ("test", "测试")):
        old, new = upstream_metrics[split], result[split]
        lines.append(f"| {title} | LF + Snorkel | {old['coverage']:.2%} | {old['all_samples']['accuracy']:.2%} | {old['all_samples']['macro_f1']:.2%} |")
        lines.append(f"| {title} | TF-IDF + LR | 100.00% | {new['accuracy']:.2%} | {new['macro_f1']:.2%} |")
    audit = summary["overlap_audit"]
    lines.extend(["", "## 输入与复现", "",
                  f"保留的训练弱标签类别数：{summary['train_class_counts']}。", "",
                  f"验证/测试中正文与清洗后训练集完全相同的记录分别为 "
                  f"{audit['valid_rows_with_exact_text_in_cleaned_train']} / {audit['test_rows_with_exact_text_in_cleaned_train']} 条；"
                  "去空白后相同的记录分别为 "
                  f"{audit['valid_rows_with_same_text_ignoring_whitespace_in_cleaned_train']} / "
                  f"{audit['test_rows_with_same_text_ignoring_whitespace_in_cleaned_train']} 条。沿用冻结划分，未额外去重。", "",
                  "model.joblib 包含 TF-IDF 与 LR，直接接受正文字符串列表；概率列顺序见 run_config.json。",
                  "输入文件 SHA-256、清洗统计、类别顺序及上游金标使用情况见 data_summary.json；C 搜索记录见 tuning_history.json。", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(version_dir=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(version_dir)
    prepared = prepare_data(args)
    summary = prepared[3]
    print(f"{DATASET} {args.version}: keep {summary['train_rows_after_cleaning']} train rows, "
          f"remove {summary['train_abstain_removed']} abstentions; "
          f"valid={summary['valid_rows']}, test={summary['test_rows']}", flush=True)
    if not args.check_data:
        train_and_predict(args, prepared)


if __name__ == "__main__":
    main()
