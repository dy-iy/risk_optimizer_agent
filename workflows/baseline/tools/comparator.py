from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
    from .common import ensure_parent_dir, read_json_file, write_json_file
    from .metrics import load_optional_json, summarize_eval, summarize_slice
except ImportError:
    from common import ensure_parent_dir, read_json_file, write_json_file
    from metrics import load_optional_json, summarize_eval, summarize_slice


@dataclass
class CompareResult:
    success: bool
    baseline_eval_json: str
    candidate_eval_json: str
    baseline_slice_log_json: str
    candidate_slice_log_json: str
    compare_json: str
    compare_markdown: str
    winner: str
    baseline_name: str
    candidate_name: str
    error_message: str = ""


def read_json(path: Path) -> dict:
    return read_json_file(path)


def compare_metric(
    name: str,
    baseline_value: float,
    candidate_value: float,
    higher_is_better: bool,
) -> dict:
    if higher_is_better:
        delta = candidate_value - baseline_value
        better = "candidate" if delta > 0 else ("baseline" if delta < 0 else "tie")
    else:
        delta = baseline_value - candidate_value
        better = "candidate" if delta > 0 else ("baseline" if delta < 0 else "tie")

    return {
        "metric": name,
        "baseline": baseline_value,
        "candidate": candidate_value,
        "delta_for_candidate": round(delta, 6),
        "higher_is_better": higher_is_better,
        "better": better,
    }


def derive_winner(metric_results: list[dict]) -> tuple[str, dict]:
    score = {"baseline": 0, "candidate": 0}
    for item in metric_results:
        if item["better"] == "baseline":
            score["baseline"] += 1
        elif item["better"] == "candidate":
            score["candidate"] += 1

    if score["candidate"] > score["baseline"]:
        winner = "candidate"
    elif score["baseline"] > score["candidate"]:
        winner = "baseline"
    else:
        # 平分时按更核心的指标打破平局：
        # 1) label_accuracy
        # 2) score_mae
        # 3) primary_type_accuracy
        label_cmp = next((x for x in metric_results if x["metric"] == "label_accuracy"), None)
        mae_cmp = next((x for x in metric_results if x["metric"] == "score_mae"), None)
        primary_cmp = next((x for x in metric_results if x["metric"] == "primary_type_accuracy"), None)

        for item in [label_cmp, mae_cmp, primary_cmp]:
            if item and item["better"] in ("baseline", "candidate"):
                winner = item["better"]
                break
        else:
            winner = "tie"

    return winner, score


def build_recommendation_lines(metric_results: list[dict], baseline_name: str, candidate_name: str) -> list[str]:
    lines: list[str] = []

    for item in metric_results:
        m = item["metric"]
        better = item["better"]
        if better == "tie":
            continue

        who = candidate_name if better == "candidate" else baseline_name
        if m == "score_mae":
            lines.append(f"{who} 在风险分数 MAE 上更优。")
        elif m == "score_rmse":
            lines.append(f"{who} 在风险分数 RMSE 上更优。")
        elif m == "label_accuracy":
            lines.append(f"{who} 在风险等级准确率上更优。")
        elif m == "primary_type_accuracy":
            lines.append(f"{who} 在主风险类别准确率上更优。")
        elif m == "false_positive_rows":
            lines.append(f"{who} 的误报更少。")
        elif m == "false_negative_rows":
            lines.append(f"{who} 的漏报更少。")
        elif m == "type_mismatch_rows":
            lines.append(f"{who} 的主类别错配更少。")

    if not lines:
        lines.append("两版脚本核心指标非常接近，暂时没有明显赢家。")

    return lines


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# 脚本版本对比报告")
    lines.append("")
    lines.append(f"- Baseline: **{report['baseline_name']}**")
    lines.append(f"- Candidate: **{report['candidate_name']}**")
    lines.append(f"- Winner: **{report['winner_name']}**")
    lines.append("")

    lines.append("## 核心指标对比")
    lines.append("")
    lines.append("| Metric | Baseline | Candidate | Better |")
    lines.append("|---|---:|---:|---|")
    for item in report["metric_results"]:
        lines.append(
            f"| {item['metric']} | {item['baseline']:.6f} | {item['candidate']:.6f} | {item['better']} |"
        )
    lines.append("")

    if report.get("slice_metric_results"):
        lines.append("## 错误样本对比")
        lines.append("")
        lines.append("| Metric | Baseline | Candidate | Better |")
        lines.append("|---|---:|---:|---|")
        for item in report["slice_metric_results"]:
            lines.append(
                f"| {item['metric']} | {item['baseline']} | {item['candidate']} | {item['better']} |"
            )
        lines.append("")

    lines.append("## 结论")
    lines.append("")
    for x in report.get("recommendations", []):
        lines.append(f"- {x}")
    lines.append("")

    lines.append("## 计分")
    lines.append("")
    lines.append(f"- Baseline score: {report['scoreboard']['baseline']}")
    lines.append(f"- Candidate score: {report['scoreboard']['candidate']}")
    lines.append("")

    return "\n".join(lines).strip() + "\n"


def compare_versions(
    baseline_eval_json: str | Path,
    candidate_eval_json: str | Path,
    compare_json: str | Path,
    compare_markdown: str | Path,
    baseline_slice_log_json: Optional[str | Path] = None,
    candidate_slice_log_json: Optional[str | Path] = None,
    baseline_name: str = "v1",
    candidate_name: str = "v2",
) -> CompareResult:
    baseline_eval_json = Path(baseline_eval_json).resolve()
    candidate_eval_json = Path(candidate_eval_json).resolve()
    compare_json = Path(compare_json).resolve()
    compare_markdown = Path(compare_markdown).resolve()

    if not baseline_eval_json.exists():
        return CompareResult(
            success=False,
            baseline_eval_json=str(baseline_eval_json),
            candidate_eval_json=str(candidate_eval_json),
            baseline_slice_log_json=str(baseline_slice_log_json or ""),
            candidate_slice_log_json=str(candidate_slice_log_json or ""),
            compare_json=str(compare_json),
            compare_markdown=str(compare_markdown),
            winner="",
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            error_message=f"baseline_eval_json 不存在: {baseline_eval_json}",
        )

    if not candidate_eval_json.exists():
        return CompareResult(
            success=False,
            baseline_eval_json=str(baseline_eval_json),
            candidate_eval_json=str(candidate_eval_json),
            baseline_slice_log_json=str(baseline_slice_log_json or ""),
            candidate_slice_log_json=str(candidate_slice_log_json or ""),
            compare_json=str(compare_json),
            compare_markdown=str(compare_markdown),
            winner="",
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            error_message=f"candidate_eval_json 不存在: {candidate_eval_json}",
        )

    baseline_eval = read_json(baseline_eval_json)
    candidate_eval = read_json(candidate_eval_json)

    b = summarize_eval(baseline_eval)
    c = summarize_eval(candidate_eval)

    metric_results = [
        compare_metric("score_mae", b["score_mae"], c["score_mae"], higher_is_better=False),
        compare_metric("score_rmse", b["score_rmse"], c["score_rmse"], higher_is_better=False),
        compare_metric("label_accuracy", b["label_accuracy"], c["label_accuracy"], higher_is_better=True),
        compare_metric("primary_type_accuracy", b["primary_type_accuracy"], c["primary_type_accuracy"], higher_is_better=True),
    ]

    baseline_slice = load_optional_json(baseline_slice_log_json)
    candidate_slice = load_optional_json(candidate_slice_log_json)
    bs = summarize_slice(baseline_slice)
    cs = summarize_slice(candidate_slice)

    slice_metric_results: list[dict] = []
    if baseline_slice and candidate_slice:
        for k in ["false_positive_rows", "false_negative_rows", "type_mismatch_rows"]:
            if bs[k] is not None and cs[k] is not None:
                slice_metric_results.append(
                    compare_metric(k, float(bs[k]), float(cs[k]), higher_is_better=False)
                )

    all_metric_results = metric_results + slice_metric_results
    winner, scoreboard = derive_winner(all_metric_results)
    recommendations = build_recommendation_lines(all_metric_results, baseline_name, candidate_name)

    winner_name = (
        candidate_name if winner == "candidate"
        else baseline_name if winner == "baseline"
        else "tie"
    )

    report = {
        "baseline_name": baseline_name,
        "candidate_name": candidate_name,
        "winner": winner,
        "winner_name": winner_name,
        "scoreboard": scoreboard,
        "baseline_summary": b,
        "candidate_summary": c,
        "baseline_slice_summary": bs,
        "candidate_slice_summary": cs,
        "metric_results": metric_results,
        "slice_metric_results": slice_metric_results,
        "recommendations": recommendations,
    }

    write_json_file(compare_json, report)

    ensure_parent_dir(compare_markdown)
    compare_markdown.write_text(render_markdown(report), encoding="utf-8")

    return CompareResult(
        success=True,
        baseline_eval_json=str(baseline_eval_json),
        candidate_eval_json=str(candidate_eval_json),
        baseline_slice_log_json=str(Path(baseline_slice_log_json).resolve()) if baseline_slice_log_json else "",
        candidate_slice_log_json=str(Path(candidate_slice_log_json).resolve()) if candidate_slice_log_json else "",
        compare_json=str(compare_json),
        compare_markdown=str(compare_markdown),
        winner=winner_name,
        baseline_name=baseline_name,
        candidate_name=candidate_name,
        error_message="",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较两版脚本谁更好")
    parser.add_argument("--baseline-eval-json", type=str, required=True, help="旧版 eval.json")
    parser.add_argument("--candidate-eval-json", type=str, required=True, help="新版 eval.json")
    parser.add_argument("--baseline-slice-log-json", type=str, default="", help="旧版 slicer 日志，可选")
    parser.add_argument("--candidate-slice-log-json", type=str, default="", help="新版 slicer 日志，可选")
    parser.add_argument("--compare-json", type=str, required=True, help="输出对比 json")
    parser.add_argument("--compare-markdown", type=str, required=True, help="输出对比 markdown")
    parser.add_argument("--baseline-name", type=str, default="v1", help="旧版名字")
    parser.add_argument("--candidate-name", type=str, default="v2", help="新版名字")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    result = compare_versions(
        baseline_eval_json=args.baseline_eval_json,
        candidate_eval_json=args.candidate_eval_json,
        baseline_slice_log_json=args.baseline_slice_log_json or None,
        candidate_slice_log_json=args.candidate_slice_log_json or None,
        compare_json=args.compare_json,
        compare_markdown=args.compare_markdown,
        baseline_name=args.baseline_name,
        candidate_name=args.candidate_name,
    )

    print("=" * 60)
    print("COMPARE RESULT")
    print("=" * 60)
    print(f"success                 : {result.success}")
    print(f"baseline_eval_json      : {result.baseline_eval_json}")
    print(f"candidate_eval_json     : {result.candidate_eval_json}")
    print(f"baseline_slice_log_json : {result.baseline_slice_log_json}")
    print(f"candidate_slice_log_json: {result.candidate_slice_log_json}")
    print(f"compare_json            : {result.compare_json}")
    print(f"compare_markdown        : {result.compare_markdown}")
    print(f"baseline_name           : {result.baseline_name}")
    print(f"candidate_name          : {result.candidate_name}")
    print(f"winner                  : {result.winner}")
    if result.error_message:
        print(f"error_message           : {result.error_message}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
