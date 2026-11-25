"""比较已保存版本的训练/验证行为；不执行 LF、不训练、不调用 LLM。"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

import numpy as np

if __package__:
    from .evaluate import classification_metrics
    from .patcher import _read_lfs
    from .task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, LABELS, VALID_VOTES, label_id
else:
    from task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, LABELS, VALID_VOTES, label_id
    from evaluate import classification_metrics
    from patcher import _read_lfs


IMPACT_FILES = ("impact.json", "impact.md", "impact_samples.csv")
AGGREGATORS = ("Snorkel", "simple_vote")
LABEL_IDS = {name: value for value, name in LABELS.items()}
PROBABILITY_TOLERANCE = 1e-6
SPLITS = ("train", "valid")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path, required):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not set(required) <= set(fields):
            raise ValueError(f"{path}: 字段缺失或重复")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path}: CSV 行字段数量不一致")
    return fields, rows


def _index(rows, description):
    indexed = {}
    for row in rows:
        key = row["id"]
        if not key or key in indexed:
            raise ValueError(f"{description}: 空编号或重复编号 {key!r}")
        indexed[key] = row
    return indexed


def _load_version(folder, version_name, data, data_hashes, aggregator="Snorkel"):
    if not re.fullmatch(r"v[1-9]\d*", version_name):
        raise ValueError("版本名必须为 v1、v2 等")
    if aggregator not in AGGREGATORS:
        raise ValueError(f"未知聚合器：{aggregator}")
    output = folder / "versions" / version_name / aggregator
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    if metrics["lf_version"] != version_name:
        raise ValueError(f"{version_name}: metrics.json 版本不一致")
    expected_library = "snorkel" if aggregator == "Snorkel" else "simple_vote"
    if (metrics.get("aggregator", aggregator) != aggregator
            or metrics["label_model"]["library"] != expected_library):
        raise ValueError(f"{version_name}: 聚合器与结果目录不一致")
    manifest = metrics["data_split"]
    for split in SPLITS:
        if (manifest["csv_sha256"][split] != data_hashes[split]
                or manifest["counts"][split] != len(data[split])):
            raise ValueError(f"{version_name}: {split} 数据与保存的评价结果不一致")
    shared = output.parent / "results"
    fields, vote_rows = _read_csv(shared / "lf_votes.csv", {"split", "id"})
    names = [name for name in fields if name not in {"split", "id"}]
    source_names, _, _ = _read_lfs((output.parent / "lfs.py").read_text(encoding="utf-8-sig"))
    model_info = metrics["label_model"]
    if (names != source_names or len(names) != metrics["n_lfs"]
            or ("lf_names" in model_info and names != model_info["lf_names"])
            or model_info.get("probability_class_order", list(CLASS_NAMES)) != list(CLASS_NAMES)):
        raise ValueError(f"{version_name}: LF 列或概率类别顺序与指标不一致")
    _, weak_rows = _read_csv(
        output / "weak_labels.csv", {"split", "id", "text", "weak_label", *[f"p_{name}" for name in CLASS_NAMES]}
    )
    result = {"lf_names": names, "splits": {}}
    for split in SPLITS:
        votes = _index((row for row in vote_rows if row["split"] == split), f"{version_name}/{split} 投票")
        weak = _index((row for row in weak_rows if row["split"] == split), f"{version_name}/{split} 弱标签")
        ids = [row["id"] for row in data[split]]
        if set(votes) != set(ids) or set(weak) != set(ids):
            raise ValueError(f"{version_name}: {split} 样本编号不完整或与数据不一致")
        if any(weak[row["id"]]["text"] != row["text"] for row in data[split]):
            raise ValueError(f"{version_name}: {split} 正文与数据不一致")
        matrix = np.array([[int(votes[key][name]) for name in names] for key in ids])
        if not np.isin(matrix, VALID_VOTES).all():
            raise ValueError(f"{version_name}: LF 投票超出任务类别范围")
        try:
            predictions = np.array([LABEL_IDS[weak[key]["weak_label"]] for key in ids])
        except KeyError as error:
            raise ValueError(f"{version_name}: 无效的弱标签 {error}") from None
        probabilities = np.array([[float(weak[key][f"p_{label}"]) for label in CLASS_NAMES] for key in ids])
        if (not np.isfinite(probabilities).all() or (probabilities < 0).any()
                or (probabilities > 1).any()
                or not np.allclose(probabilities.sum(axis=1), 1, rtol=0, atol=CARDINALITY * 1e-6)):
            raise ValueError(f"{version_name}: 无效的类别概率")
        if (predictions[(matrix == -1).all(axis=1)] != -1).any():
            raise ValueError(f"{version_name}: 所有 LF 弃权的样本必须输出弃权")
        if (metrics[split]["n_samples"] != len(ids)
                or metrics[split]["n_labeled"] != int((predictions != -1).sum())):
            raise ValueError(f"{version_name}: {split} 指标与保存的弱标签不一致")
        if split == "valid":
            gold = np.array([label_id(row["label"]) for row in data[split]])
            recomputed = classification_metrics(gold, predictions)
            if any(not np.isclose(recomputed[key], metrics[split]["all_samples"][key])
                   for key in ("accuracy", "macro_f1")):
                raise ValueError(f"{version_name}: 验证指标与弱标签不一致")
        result["splits"][split] = {"L": matrix, "predictions": predictions, "probabilities": probabilities}
    result["data_source"] = {key: manifest[key] for key in ("dataset", "revision", "scheme", "label_mapping") if key in manifest}
    result["input_sha256"] = {
        f"{aggregator}/metrics.json": _digest(output / "metrics.json"),
        "results/lf_votes.csv": _digest(shared / "lf_votes.csv"),
        f"{aggregator}/weak_labels.csv": _digest(output / "weak_labels.csv"),
    }
    return result


def compare_split(old, new, old_names, new_names, rows, *, with_gold=False):
    """输入已按同一组 ID 对齐；LF 按名称对齐，缺失列视为弃权。"""
    names = list(dict.fromkeys([*old_names, *new_names]))
    n = len(rows)

    def align(snapshot, source_names):
        matrix = np.full((n, len(names)), -1, dtype=int)
        positions = {name: i for i, name in enumerate(names)}
        for j, name in enumerate(source_names):
            matrix[:, positions[name]] = snapshot["L"][:, j]
        return matrix

    before, after = align(old, old_names), align(new, new_names)
    old_pred, new_pred = old["predictions"], new["predictions"]
    cells = before != after
    votes_changed = cells.any(axis=1)
    prediction_changed = old_pred != new_pred
    probability_delta = np.max(np.abs(new["probabilities"] - old["probabilities"]), axis=1)
    # 保存的概率保留六位小数；阈值之外的小容差消除二进制浮点比较误差。
    probability_changed = probability_delta > PROBABILITY_TOLERANCE + 1e-12
    old_covered, new_covered = (before != -1).any(axis=1), (after != -1).any(axis=1)
    old_output, new_output = old_pred != -1, new_pred != -1
    old_conflict = sum((before == label).any(axis=1).astype(int) for label in range(CARDINALITY)) > 1
    new_conflict = sum((after == label).any(axis=1).astype(int) for label in range(CARDINALITY)) > 1

    def coverage(previous, current):
        return {"before": int(previous.sum()), "after": int(current.sum()),
                "gained": int((~previous & current).sum()), "lost": int((previous & ~current).sum())}

    summary = {
        "n_samples": n,
        "votes_changed_samples": int(votes_changed.sum()),
        "lf_coverage": coverage(old_covered, new_covered),
        "output_coverage": coverage(old_output, new_output),
        "prediction_changed_samples": int(prediction_changed.sum()),
        "label_flipped_samples": int((prediction_changed & old_output & new_output).sum()),
        "prediction_changed_without_vote_change": int((prediction_changed & ~votes_changed).sum()),
        "conflicts": {"before": int(old_conflict.sum()), "after": int(new_conflict.sum())},
        "probabilities": {"changed_samples": int(probability_changed.sum()),
                          "mean_max_abs_delta": float(probability_delta.mean())},
        "changed_lfs": {name: int(cells[:, j].sum()) for j, name in enumerate(names) if cells[:, j].any()},
    }
    old_outcomes = new_outcomes = None
    if with_gold:
        gold = np.array([label_id(row["label"]) for row in rows])
        old_outcomes = np.where(old_pred == -1, -1, np.where(old_pred == gold, 1, 0))
        new_outcomes = np.where(new_pred == -1, -1, np.where(new_pred == gold, 1, 0))
        fixes = int(((old_outcomes != 1) & (new_outcomes == 1)).sum())
        regressions = int(((old_outcomes == 1) & (new_outcomes != 1)).sum())
        old_metrics, new_metrics = classification_metrics(gold, old_pred), classification_metrics(gold, new_pred)
        summary["validation_quality"] = {
            "fixed_samples": fixes, "regressed_samples": regressions,
            "newly_wrong_samples": int(((old_outcomes != 0) & (new_outcomes == 0)).sum()),
            "accuracy_before": old_metrics["accuracy"], "accuracy_after": new_metrics["accuracy"],
            "macro_f1_before": old_metrics["macro_f1"], "macro_f1_after": new_metrics["macro_f1"],
        }
    details = []
    for i, row in enumerate(rows):
        vote_changes = {names[j]: {"before": int(before[i, j]), "after": int(after[i, j])}
                        for j in np.flatnonzero(cells[i])}
        if old_pred[i] == new_pred[i]:
            output_change = "unchanged"
        elif old_pred[i] == -1:
            output_change = "gained"
        elif new_pred[i] == -1:
            output_change = "lost"
        else:
            output_change = "flipped"
        details.append({
            "id": row["id"], "text": row["text"], "gold": row["label"] if with_gold else "",
            "lf_covered_before": bool(old_covered[i]), "lf_covered_after": bool(new_covered[i]),
            "votes_changed": bool(votes_changed[i]),
            "lf_vote_changes": json.dumps(vote_changes, ensure_ascii=False, separators=(",", ":")),
            "prediction_before": LABELS[int(old_pred[i])], "prediction_after": LABELS[int(new_pred[i])],
            "output_change": output_change,
            "conflict_before": bool(old_conflict[i]), "conflict_after": bool(new_conflict[i]),
            **{f"p_{name}_{side}": float(snapshot["probabilities"][i, label])
               for label, name in enumerate(CLASS_NAMES) for side, snapshot in (("before", old), ("after", new))},
            "max_probability_delta": round(float(probability_delta[i]), 6), "probability_changed": bool(probability_changed[i]),
            "outcome_before": {-1: "abstain", 0: "wrong", 1: "correct"}[int(old_outcomes[i])] if with_gold else "",
            "outcome_after": {-1: "abstain", 0: "wrong", 1: "correct"}[int(new_outcomes[i])] if with_gold else "",
        })
    return summary, details


def _report(impact):
    lines = [f"# {impact['old_version']} → {impact['new_version']} · {impact['aggregator']}", "",
             "| 指标 | 训练集 | 验证集 |", "| --- | ---: | ---: |"]
    train, valid = (impact["splits"][key] for key in SPLITS)
    rows = [
        ("样本数", lambda s: s["n_samples"]),
        ("投票变化样本", lambda s: s["votes_changed_samples"]),
        ("LF 覆盖", lambda s: f"{s['lf_coverage']['before']} → {s['lf_coverage']['after']}"),
        ("LF 新增 / 丢失覆盖", lambda s: f"{s['lf_coverage']['gained']} / {s['lf_coverage']['lost']}"),
        ("聚合输出", lambda s: f"{s['output_coverage']['before']} → {s['output_coverage']['after']}"),
        ("新增 / 丢失输出", lambda s: f"{s['output_coverage']['gained']} / {s['output_coverage']['lost']}"),
        ("预测变化样本", lambda s: s["prediction_changed_samples"]),
        ("类别翻转", lambda s: s["label_flipped_samples"]),
        ("投票未变但聚合预测变化", lambda s: s["prediction_changed_without_vote_change"]),
        ("LF 冲突", lambda s: f"{s['conflicts']['before']} → {s['conflicts']['after']}"),
        ("概率变化样本", lambda s: s["probabilities"]["changed_samples"]),
    ]
    lines.extend(f"| {title} | {value(train)} | {value(valid)} |" for title, value in rows)
    quality = valid["validation_quality"]
    lines.extend(["", "| 验证指标 | 修改前 | 修改后 |", "| --- | ---: | ---: |",
                  f"| 全量准确率 | {quality['accuracy_before']:.2%} | {quality['accuracy_after']:.2%} |",
                  f"| 全量 Macro-F1 | {quality['macro_f1_before']:.2%} | {quality['macro_f1_after']:.2%} |", "",
                  f"验证集：修复 {quality['fixed_samples']} · 退化 {quality['regressed_samples']} · 新增误标 {quality['newly_wrong_samples']}", "",
                  "[样本明细](impact_samples.csv)", ""])
    return "\n".join(lines)


def record_impact(folder, old_version, new_version, aggregator="Snorkel"):
    """离线或迭代后调用同一路径；所有输入校验成功后才写入新版本报告。"""
    folder = Path(folder).resolve()
    if old_version == new_version:
        raise ValueError("前后版本必须不同")
    data, data_hashes = {}, {}
    for split in SPLITS:
        path = folder / "data" / f"{split}.csv"
        fields, data[split] = _read_csv(path, {"id", "text"} | ({"label"} if split == "valid" else set()))
        expected = {"id", "text"} | ({"label"} if split == "valid" else set())
        if set(fields) != expected or not data[split]:
            raise ValueError(f"{path}: 数据字段或条数不符合约定")
        _index(data[split], f"{split} 数据")
        if split == "valid" and any(row["label"] not in CLASS_TITLES for row in data[split]):
            raise ValueError("验证金标必须属于当前任务的来源标签")
        data_hashes[split] = _digest(path)
    old, new = [_load_version(folder, version_name, data, data_hashes, aggregator)
                for version_name in (old_version, new_version)]
    if old["data_source"] != new["data_source"]:
        raise ValueError("前后版本的数据来源或 revision 不一致")
    impact = {
        "schema_version": 4, "aggregator": aggregator, "old_version": old_version, "new_version": new_version,
        "input_sha256": {"before": old["input_sha256"], "after": new["input_sha256"]},
        "probability_tolerance": PROBABILITY_TOLERANCE,
        "splits": {},
    }
    details = []
    for split in SPLITS:
        summary, samples = compare_split(old["splits"][split], new["splits"][split],
                                         old["lf_names"], new["lf_names"], data[split], with_gold=split == "valid")
        impact["splits"][split] = summary
        details.extend({"split": split, **sample} for sample in samples)
    output = folder / "versions" / new_version / aggregator
    (output / "impact.json").write_text(json.dumps(impact, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "impact.md").write_text(_report(impact), encoding="utf-8")
    with (output / "impact_samples.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(details[0]))
        writer.writeheader()
        writer.writerows(details)
    return impact


def show_impact(impact):
    train, valid = (impact["splits"][key] for key in SPLITS)
    quality = valid["validation_quality"]
    print(f"修改影响 {impact['old_version']} → {impact['new_version']} / {impact['aggregator']}：训练投票变化 {train['votes_changed_samples']} 条，"
          f"聚合预测变化 {train['prediction_changed_samples']} 条；验证修复 {quality['fixed_samples']} 条，"
          f"退化 {quality['regressed_samples']} 条，新增误标 {quality['newly_wrong_samples']} 条。", flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-version", required=True, help="修改前版本，例如 v9")
    parser.add_argument("--new-version", required=True, help="修改后版本，例如 v10")
    parser.add_argument("--aggregator", choices=[*AGGREGATORS, "all"], default="all", help="默认记录两种聚合器")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1]
    aggregators = AGGREGATORS if args.aggregator == "all" else (args.aggregator,)
    for aggregator in aggregators:
        impact = record_impact(folder, args.old_version, args.new_version, aggregator)
        show_impact(impact)
        print("记录：", folder / "versions" / args.new_version / aggregator / "impact.md")


if __name__ == "__main__":
    main()
