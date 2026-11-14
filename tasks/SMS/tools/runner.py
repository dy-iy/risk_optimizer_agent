"""运行一个 SMS LF 版本：训练集拟合 Snorkel，验证集和测试集分别评价。"""

import argparse
import csv
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
from types import ModuleType

sys.dont_write_bytecode = True

import numpy as np

if __package__:
    from .data import load_data
    from .evaluate import evaluate
    from .label_model import estimate_class_balance, predict_labels, snorkel_aggregate
else:
    from data import load_data
    from evaluate import evaluate
    from label_model import estimate_class_balance, predict_labels, snorkel_aggregate


LABELS = {-1: "abstain", 0: "ham", 1: "spam"}


class LFValidationError(ValueError):
    """候选 LF 的代码、接口或投票不符合约定。"""


def percent(value):
    return "—" if value is None else f"{value:.2%}"


def load_lfs(path):
    """每次读取当前文件，不复用其他版本的模块，也不写 LF 字节码缓存。"""
    source = path.read_text(encoding="utf-8-sig")
    module = ModuleType("sms_lfs")
    module.__file__ = str(path)
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    except Exception as error:
        raise LFValidationError(f"lfs.py 无法加载（{type(error).__name__}）") from None
    lfs = getattr(module, "LFS", None)
    if not isinstance(lfs, (list, tuple)) or len(lfs) < 3:
        raise LFValidationError("lfs.py 的 LFS 列表至少需要 3 个普通 LF 函数")
    if any(not callable(lf) or not hasattr(lf, "__name__") for lf in lfs):
        raise LFValidationError("LFS 中每一项都必须是命名函数")
    names = [lf.__name__ for lf in lfs]
    if len(names) != len(set(names)):
        raise LFValidationError("LFS 中的 LF 名称必须唯一")
    return lfs


def apply_lfs(rows, lfs):
    votes = np.empty((len(rows), len(lfs)), dtype=int)
    for i, row in enumerate(rows):
        for j, lf in enumerate(lfs):
            try:
                vote = lf(row["text"])
            except Exception as error:
                # 错误可能来自测试样本，修复反馈不能带出样本正文或编号。
                raise LFValidationError(f"LF {lf.__name__} 无法处理所有输入（{type(error).__name__}）") from None
            if isinstance(vote, (bool, np.bool_)) or not isinstance(vote, (int, np.integer)) or vote not in (-1, 0, 1):
                raise LFValidationError(f"LF {lf.__name__} 的输出无效；只能返回整数 -1、0、1")
            votes[i, j] = vote
    return votes


def vote_stats(L, predictions):
    covered = (L != -1).any(axis=1)
    conflict = (L == 0).any(axis=1) & (L == 1).any(axis=1)
    return {
        "n_samples": len(L),
        "n_labeled": int((predictions != -1).sum()),
        "n_abstained": int((predictions == -1).sum()),
        "lf_coverage": float(covered.mean()),
        "coverage": float((predictions != -1).mean()),
        "conflict_rate": float(conflict.mean()),
    }


def lf_quality(lf_names, gold, L_valid, L_train):
    quality = []
    for i, name in enumerate(lf_names):
        votes = L_valid[:, i] != -1
        other_ham = (L_train == 0).any(axis=1)
        other_spam = (L_train == 1).any(axis=1)
        conflict = ((L_train[:, i] == 0) & other_spam) | ((L_train[:, i] == 1) & other_ham)
        quality.append({
            "lf_name": name,
            "valid_votes": int(votes.sum()),
            "valid_accuracy": float((L_valid[votes, i] == gold[votes]).mean()) if votes.any() else None,
            "train_coverage": float((L_train[:, i] != -1).mean()),
            "train_conflict_rate": float(conflict.mean()),
        })
    return quality


def metric_rows(metrics):
    full, covered = metrics["all_samples"], metrics["covered_only"]
    rows = [
        ["LF 覆盖率", percent(metrics["lf_coverage"]), "—"],
        ["输出覆盖率", percent(metrics["coverage"]), "—"],
        ["准确率", percent(full["accuracy"]), percent(covered["accuracy"])],
    ]
    for label, name in [("ham", "正常短信"), ("spam", "垃圾短信")]:
        for key, title in [("precision", "Precision"), ("recall", "Recall"), ("f1", "F1")]:
            rows.append([f"{name} {title}", percent(full[label][key]), percent(covered[label][key])])
    rows.append(["Macro-F1", percent(full["macro_f1"]), percent(covered["macro_f1"])])
    return rows


def save_results(output, metrics, quality, lf_names, splits, matrices, predictions, probabilities):
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    with (output / "lf_votes.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["split", "id", *lf_names])
        for split, rows in splits.items():
            writer.writerows([split, row["id"], *votes] for row, votes in zip(rows, matrices[split]))
    with (output / "weak_labels.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["split", "id", "text", "weak_label", "p_ham", "p_spam"])
        for split, rows in splits.items():
            for row, label, probability in zip(rows, predictions[split], probabilities[split]):
                writer.writerow([split, row["id"], row["text"], LABELS[label], f"{probability[0]:.6f}", f"{probability[1]:.6f}"])

    quality_header = ["LF", "验证集投票数", "验证集投票准确率", "训练覆盖率", "训练冲突率"]
    quality_rows = [[item["lf_name"], str(item["valid_votes"]), percent(item["valid_accuracy"]),
                     percent(item["train_coverage"]), percent(item["train_conflict_rate"])] for item in quality]
    with (output / "lf_quality.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(quality_header)
        writer.writerows(quality_rows)

    report = ["# SMS 弱监督结果", "", f"LF 版本：{metrics['lf_version']} · LF 数量：{metrics['n_lfs']} · 聚合器：Snorkel {version('snorkel')}", ""]
    model_info = metrics["label_model"]
    prior = model_info["class_balance"]
    report.extend([
        f"类别先验：ham {percent(prior[0])} / spam {percent(prior[1])}；"
        f"来源：固定验证集 {model_info['class_balance_sample_count']} 条标签的类别比例。", "",
        "训练集仅用 LF 投票拟合，验证集标签仅用于估计先验和开发反馈；测试集标签仅用于评价。", "",
    ])
    with (output / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["数据集", "指标", "全量（含弃权）", "已覆盖样本"])
        for split, name in [("valid", "验证集（开发反馈）"), ("test", "独立测试集")]:
            values = metrics[split]
            rows = metric_rows(values)
            writer.writerows([name, *row] for row in rows)
            report.extend([
                f"## {name}", "",
                f"样本 {values['n_samples']:,} 条 · 输出 {values['n_labeled']:,} 条 · 正确 {values['correct']:,} 条 · 错误 {values['incorrect_labeled']:,} 条 · 弃权 {values['n_abstained']:,} 条", "",
                "| 指标 | 全量（含弃权） | 已覆盖样本 |", "| --- | ---: | ---: |",
                *["| " + " | ".join(row) + " |" for row in rows], "",
                "| 真实标签 / 预测 | ham | spam | 弃权 |", "| --- | ---: | ---: | ---: |",
            ])
            for label in ("ham", "spam"):
                counts = values["confusion"][label]
                report.append(f"| {label} | {counts['ham']} | {counts['spam']} | {counts['abstain']} |")
            report.append("")
        values = metrics["train"]
        train_rows = [
            ["样本数", str(values["n_samples"])],
            ["LF 覆盖率", percent(values["lf_coverage"])],
            ["输出覆盖率", percent(values["coverage"])],
            ["LF 冲突率", percent(values["conflict_rate"])],
        ]
        writer.writerows(["训练集", title, value, "—"] for title, value in train_rows)
        report.extend(["## 训练集", "", "| 指标 | 结果 |", "| --- | ---: |",
                       *[f"| {title} | {value} |" for title, value in train_rows], ""])
    report.extend(["## LF 质量", "", "| " + " | ".join(quality_header) + " |",
                   "| --- | ---: | ---: | ---: | ---: |",
                   *["| " + " | ".join(row) + " |" for row in quality_rows]])
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def run_version(folder, version_name):
    """运行并保存单版结果；返回开发反馈和训练数据，不返回测试样本。"""
    folder = Path(folder).resolve()
    if not re.fullmatch(r"v[1-9]\d*", version_name):
        raise ValueError("版本名需要是 v1、v2、v3 这样的形式")
    version_dir = folder / "versions" / version_name
    lfs = load_lfs(version_dir / "lfs.py")
    lf_names = [lf.__name__ for lf in lfs]
    splits = load_data(folder)
    matrices = {name: apply_lfs(rows, lfs) for name, rows in splits.items()}

    # 先验仅由固定验证集估计；训练金标和测试标签不参与拟合或先验估计。
    valid_gold = np.array([0 if row["label"] == "ham" else 1 for row in splits["valid"]])
    class_balance = estimate_class_balance(valid_gold)
    model, train_labels, train_probabilities = snorkel_aggregate(matrices["train"], class_balance)
    predictions, probabilities = {"train": train_labels}, {"train": train_probabilities}
    metadata = json.loads((folder / "data" / "split.json").read_text(encoding="utf-8"))
    metrics = {
        "lf_version": version_name, "n_lfs": len(lfs),
        "data_split": {key: metadata[key] for key in ("dataset", "revision", "counts", "csv_sha256")},
    }
    gold = {}
    for split in ("valid", "test"):
        predictions[split], probabilities[split] = predict_labels(model, matrices[split])
        gold[split] = np.array([0 if row["label"] == "ham" else 1 for row in splits[split]])
        metrics[split] = {**evaluate(gold[split], predictions[split]), **vote_stats(matrices[split], predictions[split])}
    metrics["train"] = vote_stats(matrices["train"], predictions["train"])
    with np.errstate(divide="ignore", invalid="ignore"):
        weights = model.get_weights()
    metrics["label_model"] = {
        "library": "snorkel", "version": version("snorkel"), "device": "cpu", "n_epochs": 500, "seed": 42,
        "fit_split": "train", "gold_used_for_fit": True,
        "train_gold_used_for_fit": False, "test_gold_used_for_fit": False,
        "validation_gold_usage": "class_balance_only", "class_balance_source": "valid",
        "class_balance_sample_count": len(valid_gold),
        "class_balance_class_counts": {"ham": int((valid_gold == 0).sum()), "spam": int((valid_gold == 1).sum())},
        "class_balance": model.p.tolist(),
        "probability_class_order": ["ham", "spam"], "lf_names": lf_names,
        "lf_weights": [float(weight) if (matrices["train"][:, i] != -1).any() and np.isfinite(weight) else None
                       for i, weight in enumerate(weights)],
    }
    quality = lf_quality(lf_names, gold["valid"], matrices["valid"], matrices["train"])
    output = version_dir / "results"
    output.mkdir(parents=True, exist_ok=True)
    model.save(str(output / "snorkel_model.pkl"))
    save_results(output, metrics, quality, lf_names, splits, matrices, predictions, probabilities)
    return {
        "metrics": metrics, "lf_names": lf_names, "lf_quality": quality,
        **{split: {"rows": splits[split], "L": matrices[split], "predictions": predictions[split]}
           for split in ("valid", "train")},
    }


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v1", help="已有 LF 版本，例如 v1、v2、v3")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1]
    metrics = run_version(folder, args.version)["metrics"]
    print(f"SMS / {args.version} · {metrics['n_lfs']} 个 LF · Snorkel {version('snorkel')}")
    for split, title in [("valid", "验证集"), ("test", "测试集")]:
        item = metrics[split]
        print(f"{title}：{item['n_samples']:,} 条 · 覆盖率 {percent(item['coverage'])} · 全量 Macro-F1 {percent(item['all_samples']['macro_f1'])}")
    print("结果：", folder / "versions" / args.version / "results")


if __name__ == "__main__":
    main()
