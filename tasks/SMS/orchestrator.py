"""询问起始版本和追加轮数，然后逐轮分析、修改 LF、聚合评价。"""

import csv
from datetime import datetime, timedelta, timezone
from functools import partial
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

# 直接运行入口时也不生成本项目的 __pycache__。
sys.dont_write_bytecode = True

if __package__:
    from .tools.analyzer import AnalyzerResponseError, analyze, build_feedback
    from .tools.llm import LLMAPIError, LLMResponseError, call_llm, check_config, show_token_summary
    from .tools.patcher import patch, validate_changes
    from .tools.runner import run_version
else:
    from tools.analyzer import AnalyzerResponseError, analyze, build_feedback
    from tools.llm import LLMAPIError, LLMResponseError, call_llm, check_config, show_token_summary
    from tools.patcher import patch, validate_changes
    from tools.runner import run_version


# 常用参数集中在这里，直接修改即可。
MAX_LF_CHANGES = 5
MAX_EXAMPLES_PER_GROUP = 30
FEEDBACK_SEED = 42
LOW_COVERAGE_THRESHOLD = 0.7  # 训练集或验证集 LF 覆盖率低于此值，优先提高覆盖率，按证据选择新增或修改等操作。


def percent(value):
    return "—" if value is None else f"{value:.2%}"


def show_result(metrics):
    for key, title in [("valid", "验证集"), ("test", "测试集")]:
        item = metrics[key]
        print(f"{title}：覆盖率 {percent(item['coverage'])} · 全量准确率 {percent(item['all_samples']['accuracy'])} · 全量 Macro-F1 {percent(item['all_samples']['macro_f1'])}", flush=True)


def update_iterations(folder, metrics):
    """每个版本一行；测试指标只写入结果，不参与迭代决策。"""
    def prior_columns(model_info):
        prior = model_info.get("class_balance", [None, None])
        return {
            "类别先验来源": model_info.get("class_balance_source", "fixed"),
            "先验_ham": percent(prior[0]), "先验_spam": percent(prior[1]),
        }

    row = {"版本": metrics["lf_version"], "LF 数量": metrics["n_lfs"]}
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
        for label in ("ham", "spam"):
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
            existing = [item for item in csv.DictReader(stream) if item["版本"] != row["版本"]]
        for item in existing:
            metric_path = folder / "versions" / item["版本"] / "results" / "metrics.json"
            if metric_path.exists():
                previous_metrics = json.loads(metric_path.read_text(encoding="utf-8"))
                item.update(prior_columns(previous_metrics.get("label_model", {})))
    existing.append(row)
    existing.sort(key=lambda item: int(item["版本"][1:]))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerows(existing)


def write_changes(output, old_version, new_version, changes, analysis):
    reasons = {(item["action"], item["target"]): item["reason"] for item in analysis["operations"]}
    names = {"add": "新增", "modify": "修改", "delete": "删除"}
    lines = [f"# {old_version} → {new_version}", "", "| 操作 | LF | 原因 |", "| --- | --- | --- |"]
    if "decision" in analysis:
        decision = analysis["decision"]
        lines[2:2] = [f"本轮决策：{names[decision['action']]} LF。{decision['reason']}", ""]
    for item in changes:
        reason = reasons[(item["action"], item["target"])].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {names[item['action']]} | `{item['target']}` | {reason} |")
    (output / "changes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_error(output, stage, error, attempt=None):
    path = output / "errors.json"
    errors = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    errors.append({"stage": stage, "attempt": attempt, "error": f"{type(error).__name__}: {error}"})
    path.write_text(json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_iterations(folder, current_version, rounds):
    folder = Path(folder).resolve()
    if not re.fullmatch(r"v[1-9]\d*", current_version):
        raise ValueError("当前版本请填写 v1、v2、v3 这样的名称")
    if not (folder / "versions" / current_version / "lfs.py").is_file():
        raise ValueError(f"{current_version} 没有 lfs.py")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 0:
        raise ValueError("追加轮数必须是非负整数；0 表示只评价当前版本")
    if rounds:
        check_config(folder)
    run_id = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    try:
        print(f"\n评价起点 {current_version}", flush=True)
        current = run_version(folder, current_version)
        update_iterations(folder, current["metrics"])
        show_result(current["metrics"])
        for round_index in range(1, rounds + 1):
            next_version = f"v{int(current_version[1:]) + 1}"
            print(f"\n第 {round_index}/{rounds} 轮：{current_version} → {next_version}", flush=True)
            current_code = (folder / "versions" / current_version / "lfs.py").read_text(encoding="utf-8")
            feedback = build_feedback(current, current_code, current_version, MAX_EXAMPLES_PER_GROUP, FEEDBACK_SEED)
            analysis_output = folder / "versions" / current_version / "results"
            analysis_output.mkdir(parents=True, exist_ok=True)
            (analysis_output / "analysis_invalid.json").unlink(missing_ok=True)
            output = folder / "versions" / next_version / "results"
            output.mkdir(parents=True, exist_ok=True)
            # 目标 LF 即将重写，它原有的评价和分析都已过时。
            for name in (
                "analysis.json", "analysis_raw.txt", "analysis_invalid.json", "changes.md", "errors.json", "metrics.json", "metrics.csv",
                "report.md", "lf_votes.csv", "weak_labels.csv", "lf_quality.csv", "snorkel_model.pkl",
            ):
                (output / name).unlink(missing_ok=True)
            ask = partial(call_llm, version_name=next_version, folder=folder, run_id=run_id)
            try:
                print("Analyzer 决策并分析中……", flush=True)
                analysis = analyze(
                    feedback, MAX_LF_CHANGES, partial(ask, stage="Analyzer"), LOW_COVERAGE_THRESHOLD,
                    on_response=partial((analysis_output / "analysis_raw.txt").write_text, encoding="utf-8"),
                )
            except Exception as error:
                raw_response = None
                if isinstance(error, json.JSONDecodeError):
                    raw_response = error.doc
                elif isinstance(error, AnalyzerResponseError):
                    raw_response = error.raw_response
                    (analysis_output / "analysis_invalid.json").write_text(
                        json.dumps(error.parsed_response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                elif isinstance(error, LLMResponseError) and error.content:
                    raw_response = error.content
                if raw_response is not None:
                    (analysis_output / "analysis_raw.txt").write_text(raw_response, encoding="utf-8")
                    print(f"Analyzer 原始回复已保存：{analysis_output / 'analysis_raw.txt'}", flush=True)
                save_error(output, "Analyzer", error)
                raise
            action_name = {"add": "新增", "modify": "修改", "delete": "删除"}[analysis["decision"]["action"]]
            print(f"本轮操作：{action_name} LF", flush=True)
            # 分析归被分析的版本；实际改动和评价归新生成的版本。
            (analysis_output / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            error_text, candidate = None, None
            for attempt in range(2):
                stage = "Patcher" if attempt == 0 else "Patcher 修复"
                try:
                    print(f"{stage} 生成 LF 中……", flush=True)
                    candidate = patch(
                        current_code, analysis, MAX_LF_CHANGES, partial(ask, stage=stage),
                        error=error_text, previous_candidate=candidate,
                    )
                    # 先保存候选，失败时保留该文件和错误，方便查看。
                    (output.parent / "lfs.py").write_text(candidate.rstrip() + "\n", encoding="utf-8")
                    changes = validate_changes(current_code, candidate, MAX_LF_CHANGES)
                    actual = {(item["action"], item["target"]) for item in changes}
                    proposed = {(item["action"], item["target"]) for item in analysis["operations"]}
                    if actual != proposed:
                        raise ValueError("实际 LF 增删改与 Analyzer 建议不一致，请只完成建议中的操作")
                    result = run_version(folder, next_version)
                except (LLMAPIError, OSError) as error:
                    # 接口、文件占用等问题不交给模型修改 LF。
                    save_error(output, stage, error, attempt + 1)
                    raise
                except Exception as error:
                    save_error(output, stage, error, attempt + 1)
                    if isinstance(error, LLMResponseError):
                        candidate = error.content
                    if attempt == 1:
                        raise RuntimeError(f"{next_version} 修复后仍失败，已停止，记录见 {output / 'errors.json'}") from error
                    error_text = f"{type(error).__name__}: {error}"
                    print("候选运行失败，反馈错误并额外修复 1 次。", flush=True)
                    continue
                write_changes(output, current_version, next_version, changes, analysis)
                update_iterations(folder, result["metrics"])
                show_result(result["metrics"])
                current_version, current = next_version, result
                break
        print(f"\n完成：当前版本 {current_version}，追加 {rounds} 轮。", flush=True)
        return current
    finally:
        show_token_summary(folder, run_id)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        current_version = input("当前版本（例如 v1）：").strip()
        rounds = int(input("本次追加迭代轮数（0 表示只评价）：").strip())
        run_iterations(Path(__file__).resolve().parent, current_version, rounds)
    except (EOFError, KeyboardInterrupt):
        print("\n已停止。", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\n停止：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
