"""报告与续跑工具：指标表、报告、检查点读取和轮次恢复。"""
import csv
import re

from .common import read_json
from .model import read_calls

METRICS = ("micro_f1", "macro_f1", "accuracy", "level_macro_f1", "high_recall", "high_f1", "positive_f1")


def write_results(directory, report, evaluations, comparisons):
    usage = report["usage"]
    fields = ["iteration", "attempt", "version", "split", "selection_split", "accepted", "dataset_sha256", *METRICS,
              "input_tokens", "output_tokens", "total_tokens", "unknown_calls"]
    rounds = sorted({0, *[r["iteration"] for r in evaluations if r["split"] in ("train", "dev")],
                     *[r["iteration"] for r in comparisons], *map(int, usage["by_iteration"])})
    with (directory / "iterations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for iteration in rounds:
            candidates = [r for r in evaluations if r["iteration"] == iteration and r["split"] in ("train", "dev")]
            decisions = [r for r in comparisons if r["iteration"] == iteration]
            decision = decisions[-1] if decisions else {}
            selection_split = decision.get("selection_split", "dev") if decision else (
                "dev" if any(r["split"] == "dev" for r in candidates) else "train")
            candidates = [r for r in candidates if r["split"] == selection_split]
            row = candidates[-1] if candidates else {}
            if decision and row.get("attempt") != decision["attempt"]:
                row = {}  # The final attempt may have failed before producing metrics.
            writer.writerow({**{k: row.get(k, "") for k in fields}, "iteration": iteration,
                             "attempt": decision.get("attempt", row.get("attempt", "")),
                             "accepted": decision.get("accepted", ""),
                             "selection_split": selection_split,
                             **{key: usage["by_iteration"].get(str(iteration), {}).get(key, 0)
                                for key in ("input_tokens", "output_tokens", "total_tokens", "unknown_calls")}})
    version = report.get("current_version", "v1")
    current = version if (directory / "versions" / version / "rules.py").exists() else "尚未生成完整首版"
    lines = [f"# {report['task']} / {report['run_id']}", "",
             f"状态：{report['stop_reason']}；目标 {report['target_iterations']} 轮，完成 "
             f"{report['completed_iterations']} 轮，接受 {report['accepted_versions']} 个新版本。", "",
             f"当前版本：{current}。", "",
             f"当前数据清单 SHA256：`{report.get('dataset_sha256', '未记录')}`。", "",
             "历史轮次保留当时的指标；数据哈希不同的分数不可直接比较。下表全部脚本指标已按当前数据重新评估。", ""]
    if report.get("error_message") or report.get("error_type"):
        lines += [f"失败原因：{report.get('error_message') or report['error_type']}", ""]
    if report.get("source_script"):
        lines += [f"起始脚本：`{report['source_script']}`", ""]
    lines += ["| 用量 | 输入 token | 输出 token | 已知合计 | 未知请求 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for key, label in (("initialization", "初始化"), ("iterations", "迭代"), ("total", "总计")):
        row = usage[key]
        lines.append(f"| {label} | {row['input_tokens']} | {row['output_tokens']} | {row['total_tokens']} | {row['unknown_calls']} |")
    lines += ["", f"模型调用 {usage['total']['calls']} 次；耗时 {report.get('elapsed_seconds', 0):.1f} 秒。", "",
              "| 轮次/尝试 | 数据哈希（前12位） | 选择依据 | 接受 | 综合分（前 → 后） |", "| --- | --- | --- | --- | --- |"]
    for row in comparisons:
        lines.append(f"| {row['iteration']}/{row['attempt']} | {row.get('dataset_sha256', 'historical-unknown')[:12]} | {row.get('selection_split', 'dev (historical)')} | {row['accepted']} | "
                     f"{row.get('previous_score', '无效')} → {row.get('candidate_score', '无效')} |")
    script_rows = report.get("script_evaluations", [])
    with (directory / "script_metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["script", "dataset_sha256", "split", "rows", *METRICS])
        writer.writeheader()
        for entry in script_rows:
            for split in ("train", "test"):
                writer.writerow({"script": entry["script"], "dataset_sha256": entry.get("dataset_sha256", report.get("dataset_sha256", "")), "split": split,
                                 **{key: entry[split].get(key, "") for key in ("rows", *METRICS)}})
    lines += ["", "## 每个脚本的训练集与测试集指标", "",
              "本次迭代结束后统一评估；测试集指标不参与修改、重试或版本选择。", "",
              "| 脚本 | 数据集 | 条数 | " + " | ".join(METRICS) + " |",
              "| --- | --- | ---: | " + " | ".join("---:" for _ in METRICS) + " |"]
    for entry in script_rows:
        for split in ("train", "test"):
            values = " | ".join(str(entry[split].get(key, "-")) for key in METRICS)
            lines.append(f"| {entry['script']} | {split} | {entry[split]['rows']} | {values} |")
    test = report.get("test_comparison")
    if test:
        lines += ["", "| Test 指标 | 首版 | 最终版 |", "| --- | ---: | ---: |"]
        for metric in METRICS:
            if metric in test["baseline"]:
                lines.append(f"| {metric} | {test['baseline'][metric]} | {test['selected'][metric]} |")
    if report.get("historically_exposed"):
        lines += ["", "已有规则曾接触旧训练数据；重新划分的测试集对这些规则仅作兼容性对照。"]
    lines += ["", "`iterations.csv` 每轮一行：最后一次候选评估及整轮 token；第 0 行是初始化与首版评估。"
              "`tokens.csv` 为逐请求账本；`changes.md` 查看分析、修改和拒绝原因；`versions/` 保存规则。"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_checkpoint(directory):
    path = directory / "checkpoint.json"
    if path.exists():
        return read_json(path)
    versions = [p.parent.name for p in (directory / "versions").glob("v*/rules.py")
                if re.fullmatch(r"v\d+", p.parent.name)]
    if not versions:
        raise ValueError(f"No complete accepted version to resume: {directory}")
    report_path = directory / "report.json"
    report = read_json(report_path) if report_path.exists() else {}
    current = report.get("current_version") or max(versions, key=lambda v: int(v[1:]))
    evaluations, comparisons = [], []
    table = directory / "iterations.csv"
    if table.exists():
        with table.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                iteration = int(row["iteration"])
                attempt = int(row.get("attempt") or 0)
                if row.get("version"):
                    evaluations.append({"iteration": iteration, "attempt": attempt,
                        "version": row["version"], "split": row.get("split") or "dev",
                        "dataset_sha256": row.get("dataset_sha256", "historical-unknown"),
                        **{k: float(row[k]) for k in (
                            "micro_f1", "macro_f1", "accuracy", "level_macro_f1",
                            "high_recall", "high_f1", "positive_f1") if row.get(k)}})
                if row.get("accepted") in ("True", "False"):
                    comparisons.append({"iteration": iteration, "attempt": attempt,
                                        "selection_split": row.get("selection_split") or row.get("split") or "dev",
                                        "dataset_sha256": row.get("dataset_sha256", "historical-unknown"),
                                        "accepted": row["accepted"] == "True"})
    if not table.exists():
        for path in sorted((directory / "versions" / "iterations").glob("*/attempt_*/comparison.json")):
            comparisons.append({"iteration": int(path.parents[1].name),
                                "attempt": int(path.parent.name.split("_")[1]), **read_json(path)})
    return {"state": {"current_version": current, "version_number": int(current[1:]),
                "completed_iterations": report.get("completed_iterations", len({r["iteration"] for r in comparisons})),
                "accepted_versions": report.get("accepted_versions", len(versions) - 1)},
            "evaluations": evaluations, "comparisons": comparisons,
            "debug": not (directory / "tokens.csv").exists() and (directory / "usage_calls.jsonl").exists(),
            "elapsed_seconds": report.get("elapsed_seconds", 0)}


def next_iteration(directory, checkpoint):
    used = [r["iteration"] for key in ("evaluations", "comparisons") for r in checkpoint.get(key, [])]
    for filename in ("tokens.csv", "usage_calls.jsonl"):
        path = directory / filename
        if path.exists():
            used.extend(r["iteration"] for r in read_calls(path))
    return max([checkpoint["state"].get("completed_iterations", 0), *used]) + 1
