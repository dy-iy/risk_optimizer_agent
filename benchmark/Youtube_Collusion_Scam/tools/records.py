"""版本汇总、修改说明和错误记录。"""

import csv
import json

if __package__:
    from .impact import AGGREGATORS
    from .task import CARDINALITY, CLASS_NAMES
else:
    from task import CARDINALITY, CLASS_NAMES
    from impact import AGGREGATORS


def percent(value):
    return "—" if value is None else f"{value:.2%}"


def show_result(metrics):
    for key, title in [("valid", "验证集"), ("test", "测试集")]:
        item = metrics[key]
        print(f"{metrics.get('aggregator', 'Snorkel')} / {title}：覆盖率 {percent(item['coverage'])} · 全量准确率 {percent(item['all_samples']['accuracy'])} · 全量 Macro-F1 {percent(item['all_samples']['macro_f1'])}", flush=True)


def update_iterations(folder, metrics):
    """每个版本、每种聚合器一行；测试指标不参与迭代决策。"""
    def prior_columns(model_info):
        prior = model_info.get("class_balance", [None] * CARDINALITY)
        return {
            "类别先验来源": model_info.get("class_balance_source", "fixed"),
            **{f"先验_{name}": percent(prior[i]) for i, name in enumerate(CLASS_NAMES)},
        }

    aggregator = metrics.get("aggregator", "Snorkel")
    row = {"版本": metrics["lf_version"], "聚合器": aggregator, "LF 数量": metrics["n_lfs"]}
    row.update(prior_columns(metrics["label_model"]))
    for key, title in [("valid", "验证集"), ("test", "测试集")]:
        item = metrics[key]
        full, covered = item["all_samples"], item["covered_only"]
        row[f"{title}_条数"] = item["n_samples"]
        row[f"{title}_LF 覆盖率"] = percent(item["lf_coverage"])
        row[f"{title}_输出覆盖率"] = percent(item["coverage"])
        row[f"{title}_准确率"] = percent(full["accuracy"])
        row[f"{title}_Macro-F1"] = percent(full["macro_f1"])
        row[f"{title}_已覆盖准确率"] = percent(covered["accuracy"])
        row[f"{title}_已覆盖 Macro-F1"] = percent(covered["macro_f1"])
        for label in CLASS_NAMES:
            for metric, name in [("precision", "Precision"), ("recall", "Recall"), ("f1", "F1")]:
                row[f"{title}_{label} {name}"] = percent(full[label][metric])
    train = metrics["train"]
    row["训练条数"] = train["n_samples"]
    row["训练 LF 覆盖率"] = percent(train["lf_coverage"])
    row["训练冲突率"] = percent(train["conflict_rate"])
    path = folder / "iterations.csv"
    existing = []
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for item in csv.DictReader(stream):
                item.setdefault("聚合器", "Snorkel")
                if (item["版本"], item["聚合器"]) != (row["版本"], aggregator):
                    existing.append(item)
        for item in existing:
            metric_path = folder / "versions" / item["版本"] / item["聚合器"] / "metrics.json"
            if metric_path.exists():
                previous_metrics = json.loads(metric_path.read_text(encoding="utf-8"))
                item.update(prior_columns(previous_metrics.get("label_model", {})))
    existing.append(row)
    existing.sort(key=lambda item: (int(item["版本"][1:]), AGGREGATORS.index(item["聚合器"])))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerows(existing)


def write_changes(output, old_version, new_version, changes, analysis):
    reasons = {item["target"]: item["reason"] for item in analysis["operations"]}
    names = {"add": "新增", "modify": "修改", "delete": "删除"}
    lines = [f"# {old_version} → {new_version}", "", "| 操作 | LF | 原因 |", "| --- | --- | --- |"]
    lines[2:2] = [f"本轮决策：{names[analysis['action']]} LF。{analysis['summary']}", ""]
    for item in changes:
        reason = reasons[item["target"]].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {names[item['action']]} | `{item['target']}` | {reason} |")
    (output / "changes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_error(output, stage, error, attempt=None):
    path = output / "errors.json"
    errors = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    errors.append({"stage": stage, "attempt": attempt, "error": f"{type(error).__name__}: {error}"})
    path.write_text(json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
