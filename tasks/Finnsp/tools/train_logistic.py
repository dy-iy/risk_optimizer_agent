"""Train TF-IDF + logistic regression for this task using simple_vote and Snorkel hard weak labels.

Run this file directly to train two LR models, one per aggregator.
By default each aggregator selects its best LF version using validation Macro-F1.
Examples:
    python tools/train_logistic.py
    python tools/train_logistic.py --version v5 --aggregator Snorkel
    python tools/train_logistic.py --plan
    python tools/train_logistic.py --check-data

Only non-abstained training weak labels are fitted. Test labels are for final evaluation.
Results: versions/vN/{simple_vote,Snorkel}/tfidf_lr_results/.
Existing results are replaced only after successful training and reload verification.
Use --no-overwrite to prevent replacement, or --no-resume to retrain completed models.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from functools import lru_cache
import gc
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import warnings
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True

import numpy as np

TASK_DIR = Path(__file__).resolve().parents[1]
TASK_NAME = TASK_DIR.name
AGGREGATORS = ("simple_vote", "Snorkel")
SPLITS = ("train", "valid", "test")
OUTPUTS = {"lr": "tfidf_lr_results"}
MODEL = "lr"
SCHEMA = 1


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    if not rows or any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError(f"Empty or malformed CSV: {path}")
    return rows


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def object_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(TASK_DIR).as_posix()


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def load_task():
    """???????????????????? python -m ???"""
    folder = TASK_DIR
    for filename in ("split.json", "train.csv", "valid.csv", "test.csv"):
        if not (folder / "data" / filename).is_file():
            raise FileNotFoundError(folder / "data" / filename)
    data_module = (importlib.import_module(".data", __package__) if __package__
                   else importlib.import_module("data"))
    splits = data_module.load_data(folder)
    metadata = read_json(folder / "data/split.json")
    if TASK_NAME in ("benchmark", "YouTube"):
        names = titles = ("ham", "spam")
    elif TASK_NAME == "Finnsp":
        names = titles = ("0", "1")
    else:
        module = (importlib.import_module(".task", __package__) if __package__
                  else importlib.import_module("task"))
        names, titles = module.CLASS_NAMES, module.CLASS_TITLES
    if any("label" in row for row in splits["train"]):
        raise ValueError(f"{TASK_NAME}: training data must not contain gold labels")
    lookup = {**{s: i for i, s in enumerate(titles)},
              **{s: i for i, s in enumerate(names)}}
    return {"task": TASK_NAME, "folder": folder, "splits": splits, "metadata": metadata,
            "names": list(names), "titles": list(titles), "lookup": lookup}


def score(gold, predictions, names):
    """Explicit class metrics: upstream abstention is a false negative."""
    gold, predictions = np.asarray(gold), np.asarray(predictions)
    if len(gold) == 0 or gold.shape != predictions.shape:
        raise ValueError("Gold/prediction length mismatch")
    if not np.isin(gold, range(len(names))).all():
        raise ValueError("Unknown gold class")
    if not np.isin(predictions, [-1, *range(len(names))]).all():
        raise ValueError("Unknown predicted class")
    result = {"n_samples": len(gold), "accuracy": float((gold == predictions).mean())}
    for i, name in enumerate(names):
        tp = int(((gold == i) & (predictions == i)).sum())
        fp = int(((gold != i) & (predictions == i)).sum())
        fn = int(((gold == i) & (predictions != i)).sum())
        result[name] = {"precision": tp / (tp + fp) if tp + fp else 0.0,
                        "recall": tp / (tp + fn) if tp + fn else 0.0,
                        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    result["macro_f1"] = float(np.mean([result[n]["f1"] for n in names]))
    result["confusion_matrix"] = [
        [int(((gold == i) & (predictions == j)).sum()) for j in range(len(names))]
        for i in range(len(names))]
    return result


def gold_labels(task, split):
    return np.array([task["lookup"][r["label"]] for r in task["splits"][split]])


def validate_upstream(task, version, aggregator):
    path = version / aggregator / "metrics.json"
    if task["task"] == "Finnsp" and not path.exists():
        path = version / "results/metrics.json"
    metrics = read_json(path)
    if metrics["lf_version"] != version.name:
        raise ValueError(f"Wrong LF version: {path}")
    if metrics["data_split"]["csv_sha256"] != task["metadata"]["csv_sha256"]:
        raise ValueError(f"Frozen split mismatch: {path}")
    if metrics.get("aggregator", aggregator) != aggregator:
        raise ValueError(f"Wrong aggregator: {path}")
    return path, metrics


def finnsp_votes(task, version):
    """Reconstruct unweighted majority vote; ties and no votes abstain."""
    rows = read_csv(version / "results/lf_votes.csv")
    columns = [key for key in rows[0] if key not in ("split", "id")]
    upstream = read_json(version / "results/metrics.json")
    if not columns or len(columns) != upstream["n_lfs"]:
        raise ValueError(f"LF column count mismatch: {version}")
    expected = {(split, r["id"]) for split in SPLITS for r in task["splits"][split]}
    actual = [(r["split"], r["id"]) for r in rows]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError(f"LF vote ID mismatch: {version}")
    result = {}
    for split in SPLITS:
        selected = [r for r in rows if r["split"] == split]
        matrix = np.asarray([[int(r[c]) for c in columns] for r in selected])
        if not np.isin(matrix, [-1, 0, 1]).all():
            raise ValueError(f"Invalid LF votes: {version}")
        counts = np.column_stack([(matrix == i).sum(axis=1) for i in (0, 1)])
        totals = counts.sum(axis=1, keepdims=True)
        probs = np.full(counts.shape, 0.5, dtype=float)
        np.divide(counts, totals, out=probs, where=totals != 0)
        predicted = counts.argmax(axis=1)
        predicted[counts[:, 0] == counts[:, 1]] = -1
        result[split] = {r["id"]: (int(p), probability.tolist())
                         for r, p, probability in zip(selected, predicted, probs)}
    return result


def select_versions(aggregators, version_name=None):
    selections = []
    name = TASK_NAME
    task = load_task()
    versions = sorted((p for p in (task["folder"] / "versions").iterdir()
                       if p.is_dir() and re.fullmatch(r"v[1-9]\d*", p.name)),
                      key=lambda p: int(p.name[1:]))
    if version_name is not None:
        versions = [v for v in versions if v.name == version_name]
    for aggregator in aggregators:
        candidates = []
        for version in versions:
            # Incomplete LF iterations are not valid selection candidates.
            legacy = name == "Finnsp"
            source = version / ("results" if legacy else aggregator) / "metrics.json"
            weak = source.parent / "weak_labels.csv"
            required = [source, weak, version / "lfs.py"]
            if legacy and aggregator == "simple_vote":
                required.append(version / "results/lf_votes.csv")
            if not all(p.is_file() for p in required):
                continue
            _, metrics = validate_upstream(task, version, "Snorkel" if legacy else aggregator)
            if legacy and aggregator == "simple_vote":
                votes = finnsp_votes(task, version)["valid"]
                predictions = [votes[r["id"]][0] for r in task["splits"]["valid"]]
                value = score(gold_labels(task, "valid"), predictions, task["names"])["macro_f1"]
            else:
                value = metrics["valid"]["all_samples"]["macro_f1"]
            if not math.isfinite(value):
                raise ValueError(f"Invalid validation score: {source}")
            candidates.append({"version": version.name, "valid_macro_f1": value})
        if not candidates:
            raise ValueError(f"No completed LF versions: {name}/{aggregator}/{version_name or 'best'}")
        best = max(candidates, key=lambda c: c["valid_macro_f1"])
        selections.append({"task": name, "aggregator": aggregator, **best,
                           "metric": "explicit --version" if version_name else "valid.all_samples.macro_f1",
                           "tie_break": "earliest_version", "test_used_for_selection": False,
                           "candidates": candidates})
    return selections


def model_input(task, row):
    if task["task"] == "Finnsp":
        inputs = row["inputs"]
        inputs = json.loads(inputs) if isinstance(inputs, str) else inputs
        return "正文：" + row["text"] + "\n候选实体：" + "；".join(inputs["entities"])
    return row["text"]


def prepare(selection):
    task = load_task()
    version = task["folder"] / "versions" / selection["version"]
    aggregator = selection["aggregator"]
    legacy = task["task"] == "Finnsp"
    source, upstream = validate_upstream(task, version, "Snorkel" if legacy else aggregator)
    sources = [source, version / "lfs.py", task["folder"] / "data/split.json",
               *[task["folder"] / "data" / f"{split}.csv" for split in SPLITS]]
    generated = legacy and aggregator == "simple_vote"
    if generated:
        votes = finnsp_votes(task, version)
        sources.append(version / "results/lf_votes.csv")
        weak_rows = []
        for split in SPLITS:
            for row in task["splits"][split]:
                label, probs = votes[split][row["id"]]
                inputs = row["inputs"]
                weak_rows.append({"split": split, "id": row["id"], "text": row["text"],
                                  "inputs": json.dumps(inputs, ensure_ascii=False),
                                  "weak_label": "abstain" if label == -1 else str(label),
                                  "p_0": probs[0], "p_1": probs[1]})
        upstream = {"lf_version": version.name, "aggregator": aggregator,
                    "data_split": upstream["data_split"],
                    "label_model": {"library": "simple_vote", "vote_weight": 1,
                                    "tie_break_policy": "abstain", "all_abstain_policy": "abstain",
                                    "gold_used_for_fit": False},
                    "source_lf_votes": relative(version / "results/lf_votes.csv")}
    else:
        weak_path = source.parent / "weak_labels.csv"
        sources.append(weak_path)
        weak_rows = read_csv(weak_path)
    by_key = {(r["split"], r["id"]): r for r in weak_rows}
    expected = {(s, r["id"]) for s in SPLITS for r in task["splits"][s]}
    if len(by_key) != len(weak_rows) or set(by_key) != expected:
        raise ValueError(f"Weak-label ID mismatch: {version}/{aggregator}")
    labels = {"abstain": -1, **{n: i for i, n in enumerate(task["names"])}}
    for split in SPLITS:
        for row in task["splits"][split]:
            weak = by_key[split, row["id"]]
            if weak["weak_label"] not in labels or weak["text"] != row["text"]:
                raise ValueError(f"Weak-label text/class mismatch: {row['id']}")
            if legacy and json.loads(weak["inputs"]) != row["inputs"]:
                raise ValueError(f"Weak-label candidate entity mismatch: {row['id']}")
    train = [r for r in task["splits"]["train"]
             if by_key["train", r["id"]]["weak_label"] != "abstain"]
    targets = np.array([labels[by_key["train", r["id"]]["weak_label"]] for r in train])
    if set(targets) != set(range(len(task["names"]))):
        raise ValueError(f"Missing weak training classes: {version}/{aggregator}")
    if not generated and len(train) != upstream["train"]["n_labeled"]:
        raise ValueError(f"Weak training count mismatch: {version}/{aggregator}")
    manifest = [{"id": r["id"], "weak_label": task["names"][int(y)]}
                for r, y in zip(train, targets)]
    seen = {model_input(task, r) for r in train}
    return {"task": task, "selection": selection, "version": version,
            "train": train, "targets": targets, "manifest": manifest,
            "train_summary": {"total": len(task["splits"]["train"]), "used": len(train),
                              "abstained_removed": len(task["splits"]["train"]) - len(train),
                              "class_counts": dict(Counter(r["weak_label"] for r in manifest))},
            "overlap_audit": {s: sum(model_input(task, r) in seen for r in task["splits"][s])
                              for s in ("valid", "test")},
            "input_sha256": {relative(p): digest(p) for p in sources},
            "upstream": upstream, "generated_weak_rows": weak_rows if generated else None}


def selection_score(metrics, names):
    return metrics[names[1]]["f1"] if len(names) == 2 else metrics["macro_f1"]


def config_for(job, args):
    import sklearn
    task = job["task"]
    config = {"schema_version": SCHEMA, "task": task["task"],
              "lf_version": job["selection"]["version"], "aggregator": job["selection"]["aggregator"],
              "model": MODEL, "seed": args.seed, "class_order": task["names"],
              "class_titles": task["titles"], "train_gold_used": False, "test_used_for_selection": False,
              "training_target": "non-abstained train hard weak labels",
              "selection": "valid class-1 F1" if len(task["names"]) == 2 else "valid Macro-F1",
              "lf_selection_sha256": object_digest(job["selection"]),
              "input_sha256": job["input_sha256"], "train_manifest_sha256": object_digest(job["manifest"]),
              "training_script_sha256": digest(Path(__file__)),
              "sklearn_version": sklearn.__version__}
    config.update({"c_values": args.c_values, "solver": "liblinear" if len(task["names"]) == 2 else "lbfgs",
                   "max_iter": 2000, "class_weight": None,
                   "vectorizer": {"analyzer": "char" if task["task"] == "Finnsp" else "word",
                                  "ngram_range": [1, 2], "max_features": 100000,
                                  "sublinear_tf": True, "lowercase": True}})

    return config


def train_lr(job, config, output):
    import joblib
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    task = job["task"]
    vectorizer_config = {**config["vectorizer"], "ngram_range": tuple(config["vectorizer"]["ngram_range"])}
    vectorizer = TfidfVectorizer(**vectorizer_config)
    x_train = vectorizer.fit_transform([model_input(task, r) for r in job["train"]])
    x_valid = vectorizer.transform([model_input(task, r) for r in task["splits"]["valid"]])
    best, best_score, history = None, -1.0, []
    for c in config["c_values"]:
        model = LogisticRegression(C=c, solver=config["solver"], max_iter=config["max_iter"],
                                   random_state=config["seed"], class_weight=None)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, job["targets"])
        metrics = score(gold_labels(task, "valid"), model.predict(x_valid), task["names"])
        value = selection_score(metrics, task["names"])
        history.append({"C": c, "selection_score": value, "valid": metrics})
        print(f"  C={c:g}: valid selection score={value:.6f}", flush=True)
        if value > best_score:
            best, best_score = model, value
    pipeline = Pipeline([("tfidf", vectorizer), ("logreg", best)])
    probabilities = {s: pipeline.predict_proba([model_input(task, r) for r in task["splits"][s]])
                     for s in ("valid", "test")}
    joblib.dump(pipeline, output / "model.joblib", compress=3)
    # Verify that the persisted artifact reproduces all probabilities.
    restored = joblib.load(output / "model.joblib")
    for split, probs in probabilities.items():
        actual = restored.predict_proba([model_input(task, r) for r in task["splits"][split]])
        np.testing.assert_allclose(actual, probs, rtol=0, atol=1e-12)
    return probabilities, history, {"best_C": best.C, "vocabulary_size": len(vectorizer.vocabulary_)}





def finish(job, model, config, output, probabilities, history, extras):
    task, selection = job["task"], job["selection"]
    result = {"task": task["task"], "lf_version": selection["version"],
              "aggregator": selection["aggregator"], "model": model,
              "train": job["train_summary"], "overlap_audit": job["overlap_audit"],
              "input_sha256": job["input_sha256"], **extras}
    for split, probs in probabilities.items():
        if probs.shape != (len(task["splits"][split]), len(task["names"])):
            raise ValueError(f"Wrong probability shape: {split}")
        if not np.isfinite(probs).all() or (probs < 0).any():
            raise ValueError(f"Invalid probabilities: {split}")
        np.testing.assert_allclose(probs.sum(axis=1), 1, rtol=1e-5, atol=1e-5)
        predicted = probs.argmax(axis=1)
        result[split] = score(gold_labels(task, split), predicted, task["names"])
        write_csv(output / f"{split}_predictions.csv", [
            {"id": r["id"], "gold": r["label"], "prediction": task["names"][int(predicted[i])],
             **{f"p_{n}": float(probs[i, j]) for j, n in enumerate(task["names"])}}
            for i, r in enumerate(task["splits"][split])])
    write_csv(output / "train_manifest.csv", job["manifest"])
    write_json(output / "metrics.json", result)
    write_json(output / "run_config.json", config)
    write_json(output / "selection.json", selection)
    write_json(output / "upstream_config.json", job["upstream"]["label_model"])
    write_json(output / "tuning_history.json", history)
    lines = [f"# {task['task']} / {selection['version']} / {selection['aggregator']} / {model}", "",
             f"训练 {len(job['train']):,}/{len(task['splits']['train']):,} 条非弃权硬弱标签；seed={config['seed']}。", "",
             "LF 版本按验证集全量 Macro-F1 选择，同分取较早版本。下游按验证集正类 F1（二分类）或 Macro-F1（多分类）选参；测试集仅作最终评价。", "",
             "| 集合 | Accuracy | Macro-F1 | " + " | ".join(f"{n} F1" for n in task["titles"]) + " |",
             "| --- | ---: | ---: | " + " | ".join("---:" for _ in task["names"]) + " |"]
    for split in ("valid", "test"):
        m = result[split]
        lines.append("| " + " | ".join([split, f"{m['accuracy']:.2%}", f"{m['macro_f1']:.2%}",
                                         *[f"{m[n]['f1']:.2%}" for n in task["names"]]]) + " |")
    lines.extend(["", f"训练输入与验证/测试完全相同的记录数：{job['overlap_audit']}。沿用冻结划分，不另行去重。", "",
                  "配置、输入哈希、训练 ID/弱标签、候选版本和逐轮结果见同目录 JSON/CSV。"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Written last: this marker denotes a complete, reload-verified run.
    write_json(output / "completed.json", {
        "completed_at": now(), "config_sha256": object_digest(config),
        "artifact_sha256": {p.relative_to(output).as_posix(): digest(p)
                            for p in sorted(output.rglob("*")) if p.is_file() and p.name != "completed.json"}})
    return result


def complete_matches(output, config):
    marker = output / "completed.json"
    if not marker.is_file():
        return False
    try:
        completed = read_json(marker)
        artifacts = completed["artifact_sha256"]
        if completed.get("config_sha256") != object_digest(config) or not artifacts:
            return False
        return all((output / name).is_file() and digest(output / name) == value
                   for name, value in artifacts.items())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def materialize_finnsp_vote(job):
    if job["generated_weak_rows"] is None:
        return
    output = job["version"] / "simple_vote"
    output.mkdir(exist_ok=True)
    write_csv(output / "weak_labels.csv", job["generated_weak_rows"])
    # Keep generated upstream scores separate from the original Snorkel results.
    metrics = {**job["upstream"], "train": {"n_samples": job["train_summary"]["total"],
                                           "n_labeled": job["train_summary"]["used"]}}
    task = job["task"]
    by_key = {(r["split"], r["id"]): r for r in job["generated_weak_rows"]}
    for split in ("valid", "test"):
        labels = [by_key[split, r["id"]]["weak_label"] for r in task["splits"][split]]
        predicted = [-1 if label == "abstain" else int(label) for label in labels]
        metrics[split] = {"all_samples": score(gold_labels(task, split), predicted, task["names"]),
                          "coverage": float(np.mean(np.array(predicted) != -1))}
    write_json(output / "metrics.json", metrics)


def write_summary(selections):
    rows = []
    for selection in selections:
        task = load_task()
        for model, dirname in OUTPUTS.items():
            output = task["folder"] / "versions" / selection["version"] / selection["aggregator"] / dirname
            if not (output / "completed.json").is_file():
                continue
            metrics = read_json(output / "metrics.json")
            rows.append({"task": selection["task"], "version": selection["version"],
                         "aggregator": selection["aggregator"], "model": model,
                         "lf_valid_macro_f1": selection["valid_macro_f1"],
                         "train_used": metrics["train"]["used"],
                         "valid_macro_f1": metrics["valid"]["macro_f1"],
                         "test_accuracy": metrics["test"]["accuracy"],
                         "test_macro_f1": metrics["test"]["macro_f1"],
                         "report": relative(output / "report.md")})
    if not rows:
        return
    folder = TASK_DIR
    # A subset invocation keeps previously summarized tasks/aggregators.
    previous_path = folder / "tfidf_lr_summary.json"
    if previous_path.is_file():
        updated = {(s["task"], s["aggregator"]) for s in selections}
        previous = read_json(previous_path)["results"]
        rows.extend(r for r in previous if (r["task"], r["aggregator"]) not in updated
                    and (TASK_DIR / r["report"]).is_file())
    rows.sort(key=lambda r: (AGGREGATORS.index(r["aggregator"]),
                            list(OUTPUTS).index(r["model"])))
    write_csv(folder / "tfidf_lr_summary.csv", rows)
    write_json(folder / "tfidf_lr_summary.json", {"generated_at": now(), "results": rows})
    lines = ["# 最佳 LF 版本的 LR / BERT 下游结果", "", f"更新时间：{now()}。仅列出已完成并保存的模型。", "",
             "| 任务 | LF 版本 | 聚合器 | 模型 | 训练数 | 验证 Macro-F1 | 测试 Accuracy | 测试 Macro-F1 |",
             "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for r in rows:
        link = os.path.relpath(TASK_DIR / r["report"], folder).replace("\\", "/")
        lines.append(f"| {r['task']} | [{r['version']}]({link}) | {r['aggregator']} | {r['model']} | "
                     f"{r['train_used']:,} | {r['valid_macro_f1']:.2%} | "
                     f"{r['test_accuracy']:.2%} | {r['test_macro_f1']:.2%} |")
    (folder / "tfidf_lr_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="LF version such as v5; default: best validation Macro-F1 per aggregator")
    parser.add_argument("--aggregator", "--aggregators", dest="aggregators", nargs="+",
                        choices=AGGREGATORS, default=list(AGGREGATORS))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Print selected versions without training or writes")
    mode.add_argument("--check-data", action="store_true", help="Validate data without training or writes")
    parser.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=True,
                        help="Replace existing outputs after successful training; --no-overwrite prevents replacement")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                        help="Skip verified completed runs; --no-resume forces retraining")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--c-values", nargs="+", type=float, default=[0.1, 1.0, 10.0])
    args = parser.parse_args(argv)
    if args.version is not None and not re.fullmatch(r"v[1-9]\d*", args.version):
        parser.error("--version must be v1, v5, etc.; omit it to select the best version")
    if not 0 <= args.seed < 2**32:
        parser.error("--seed must be in [0, 2**32)")
    if any(not math.isfinite(c) or c <= 0 for c in args.c_values):
        parser.error("C values must be finite and positive")
    args.aggregators = list(dict.fromkeys(args.aggregators))
    return args


def main(version_dir=None, argv=None):
    # Preserve FinNSP's existing versions/vN/train_logistic.py launcher API.
    if version_dir is not None:
        argv = ["--version", Path(version_dir).name, *(sys.argv[1:] if argv is None else argv)]
    args = parse_args(argv)

    selections = select_versions(args.aggregators, args.version)
    for selection in selections:
        print(f"{selection['task']:10s} {selection['aggregator']:12s} {selection['version']:4s} "
              f"LF valid macro-F1={selection['valid_macro_f1']:.6f}", flush=True)
    if args.plan:
        print(f"Plan: {len(selections)} LR model(s); no training or writes.")
        return
    jobs = [prepare(selection) for selection in selections]
    for job in jobs:
        s = job["selection"]
        print(f"Checked {s['task']}/{s['version']}/{s['aggregator']}: {job['train_summary']}", flush=True)
    if args.check_data:
        print("All selected inputs passed; no training or files written.")
        return
    work = []
    for job in jobs:
        config = config_for(job, args)
        output = job["version"] / job["selection"]["aggregator"] / OUTPUTS[MODEL]
        if args.resume and complete_matches(output, config):
            print(f"Skip verified completed run: {relative(output)}", flush=True)
            continue
        if output.exists() and any(output.iterdir()) and not args.overwrite:
            raise FileExistsError(f"Existing output: {output}; use --overwrite to replace it.")
        work.append((job, config, output))
    for job, config, output in work:
        print(f"\nTraining {relative(output)}", flush=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{OUTPUTS[MODEL]}-", dir=output.parent) as temp:
            stage = Path(temp)
            probabilities, history, extras = train_lr(job, config, stage)
            result = finish(job, MODEL, config, stage, probabilities, history, extras)
            materialize_finnsp_vote(job)
            expected_parent = (job["version"] / job["selection"]["aggregator"]).resolve()
            if (not output.resolve().is_relative_to(TASK_DIR) or output.resolve().parent != expected_parent
                    or output.name != OUTPUTS[MODEL]):
                raise ValueError(f"Unsafe output path: {output}")
            if output.exists():
                shutil.rmtree(output)
            output.mkdir()
            for artifact in stage.iterdir():
                shutil.move(str(artifact), output / artifact.name)
        write_summary(selections)
        print(f"Saved: {relative(output)}; test macro-F1={result['test']['macro_f1']:.6f}", flush=True)
        gc.collect()
    write_summary(selections)
    print(f"Finished. Summary: {TASK_DIR / 'tfidf_lr_summary.md'}", flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted; managed temporary files were cleaned. Completed runs remain available for --resume.", file=sys.stderr)
        sys.exit(130)
