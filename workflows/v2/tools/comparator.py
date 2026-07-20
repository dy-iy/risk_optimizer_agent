from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional


TARGET_ERROR_METRICS = (
    "false_positive_rows",
    "false_negative_rows",
    "type_mismatch_rows",
)

try:
    from .common import ensure_parent_dir, read_json_file, write_json_file
except ImportError:
    from common import ensure_parent_dir, read_json_file, write_json_file


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
    candidate_accepted: bool = False
    decision: str = "rejected"
    error_message: str = ""


def read_json(path: Path) -> dict:
    return read_json_file(path)


def safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def load_optional_json(path: Optional[str | Path]) -> Optional[dict]:
    if not path:
        return None
    p = Path(path).resolve()
    if not p.exists():
        return None
    return read_json(p)


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


def summarize_eval(eval_data: dict) -> dict:
    return {
        "score_mae": to_float(safe_get(eval_data, "score_metrics", "mae", default=0.0)),
        "score_rmse": to_float(safe_get(eval_data, "score_metrics", "rmse", default=0.0)),
        "label_accuracy": to_float(safe_get(eval_data, "label_metrics", "accuracy", default=0.0)),
        "primary_type_accuracy": to_float(safe_get(eval_data, "primary_type_metrics", "accuracy", default=0.0)),
        "matched_rows": int(to_float(safe_get(eval_data, "matched_rows", default=0))),
        "total_rows": int(to_float(safe_get(eval_data, "total_rows", default=0))),
    }


def summarize_slice(slice_data: Optional[dict]) -> dict:
    if not slice_data:
        return {
            "false_positive_rows": None,
            "false_negative_rows": None,
            "type_mismatch_rows": None,
            "score_diff_top_rows": None,
        }
    return {
        "false_positive_rows": safe_get(slice_data, "false_positive_rows", default=None),
        "false_negative_rows": safe_get(slice_data, "false_negative_rows", default=None),
        "type_mismatch_rows": safe_get(slice_data, "type_mismatch_rows", default=None),
        "score_diff_top_rows": safe_get(slice_data, "score_diff_top_rows", default=None),
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


def evaluate_acceptance_policy(
    baseline_slice: dict,
    candidate_slice: dict,
    *,
    baseline_quality: Optional[dict[str, float]] = None,
    candidate_quality: Optional[dict[str, float]] = None,
    focus_metric: str = "",
    regression_tolerance: Optional[dict[str, float]] = None,
    minimum_improvement: Optional[dict[str, float]] = None,
) -> dict:
    """Apply a constrained Pareto gate to the three optimization targets.

    A candidate is promotable only when none of the target error counts regress
    beyond its tolerance and at least one target improves.  When a metric has
    stalled, ``focus_metric`` additionally requires an actual improvement on
    that metric.  This prevents correlated auxiliary metrics from out-voting a
    regression in false positives, false negatives, or type mismatches.
    """
    regression_tolerance = regression_tolerance or {}
    minimum_improvement = minimum_improvement or {}

    metric_decisions: list[dict] = []
    regressions: list[str] = []
    improvements: list[str] = []

    for metric in TARGET_ERROR_METRICS:
        baseline_value = to_float(baseline_slice.get(metric), default=float("nan"))
        candidate_value = to_float(candidate_slice.get(metric), default=float("nan"))
        tolerance = max(0.0, to_float(regression_tolerance.get(metric, 0.0)))
        required_gain = max(0.0, to_float(minimum_improvement.get(metric, 1.0)))

        available = not (
            baseline_value != baseline_value or candidate_value != candidate_value
        )
        delta_for_candidate = baseline_value - candidate_value if available else 0.0
        regressed = available and candidate_value > baseline_value + tolerance
        improved = available and delta_for_candidate >= required_gain

        if regressed:
            regressions.append(metric)
        if improved:
            improvements.append(metric)

        metric_decisions.append(
            {
                "metric": metric,
                "baseline": baseline_value if available else None,
                "candidate": candidate_value if available else None,
                "delta_for_candidate": delta_for_candidate if available else None,
                "regression_tolerance": tolerance,
                "minimum_improvement": required_gain,
                "regressed": regressed,
                "improved": improved,
                "available": available,
            }
        )

    missing_metrics = [item["metric"] for item in metric_decisions if not item["available"]]
    quality_regressions: list[str] = []
    quality_decisions: list[dict] = []
    if baseline_quality is not None and candidate_quality is not None:
        quality_specs = [
            ("label_accuracy", True),
            ("primary_type_accuracy", True),
            ("score_mae", False),
            ("score_rmse", False),
        ]
        for metric, higher_is_better in quality_specs:
            baseline_value = to_float(baseline_quality.get(metric), default=float("nan"))
            candidate_value = to_float(candidate_quality.get(metric), default=float("nan"))
            available = not (
                baseline_value != baseline_value or candidate_value != candidate_value
            )
            if higher_is_better:
                regressed = available and candidate_value < baseline_value - 1e-12
            else:
                regressed = available and candidate_value > baseline_value + 1e-12
            if regressed:
                quality_regressions.append(metric)
            quality_decisions.append(
                {
                    "metric": metric,
                    "baseline": baseline_value if available else None,
                    "candidate": candidate_value if available else None,
                    "higher_is_better": higher_is_better,
                    "regressed": regressed,
                    "available": available,
                }
            )

        baseline_rows = int(to_float(baseline_quality.get("matched_rows"), default=-1))
        candidate_rows = int(to_float(candidate_quality.get("matched_rows"), default=-1))
        if baseline_rows < 0 or candidate_rows != baseline_rows:
            quality_regressions.append("matched_rows")
            quality_decisions.append(
                {
                    "metric": "matched_rows",
                    "baseline": baseline_rows,
                    "candidate": candidate_rows,
                    "higher_is_better": None,
                    "regressed": True,
                    "available": baseline_rows >= 0 and candidate_rows >= 0,
                }
            )
    focus_satisfied = not focus_metric or focus_metric in improvements
    accepted = (
        not missing_metrics
        and not regressions
        and not quality_regressions
        and bool(improvements)
        and focus_satisfied
    )

    reasons: list[str] = []
    if missing_metrics:
        reasons.append(f"缺少硬门槛指标: {', '.join(missing_metrics)}")
    if regressions:
        reasons.append(f"目标指标发生回归: {', '.join(regressions)}")
    if quality_regressions:
        reasons.append(f"质量护栏发生回归: {', '.join(quality_regressions)}")
    if not improvements:
        reasons.append("三个目标错误数均未达到最小改善幅度")
    if focus_metric and not focus_satisfied:
        reasons.append(f"停滞焦点 {focus_metric} 未改善")
    if accepted:
        reasons.append("通过目标错误数的 constrained-Pareto 晋级门槛")

    return {
        "policy": "constrained_pareto_v1",
        "candidate_accepted": accepted,
        "decision": "accepted" if accepted else "rejected",
        "focus_metric": focus_metric,
        "focus_satisfied": focus_satisfied,
        "metric_decisions": metric_decisions,
        "regressions": regressions,
        "quality_regressions": quality_regressions,
        "quality_decisions": quality_decisions,
        "improvements": improvements,
        "reasons": reasons,
    }


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
    lines.append(f"- Promotion decision: **{report['acceptance']['decision']}**")
    if report["acceptance"].get("focus_metric"):
        lines.append(f"- Stalled metric focus: **{report['acceptance']['focus_metric']}**")
    lines.append("")

    lines.append("## 晋级硬门槛")
    lines.append("")
    for reason in report["acceptance"].get("reasons", []):
        lines.append(f"- {reason}")
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
    focus_metric: str = "",
    regression_tolerance: Optional[dict[str, float]] = None,
    minimum_improvement: Optional[dict[str, float]] = None,
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
    scoreboard_winner, scoreboard = derive_winner(all_metric_results)
    acceptance = evaluate_acceptance_policy(
        bs,
        cs,
        baseline_quality=b,
        candidate_quality=c,
        focus_metric=focus_metric,
        regression_tolerance=regression_tolerance,
        minimum_improvement=minimum_improvement,
    )
    winner = "candidate" if acceptance["candidate_accepted"] else "baseline"
    recommendations = build_recommendation_lines(all_metric_results, baseline_name, candidate_name)
    recommendations = list(acceptance["reasons"]) + recommendations

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
        "scoreboard_winner": scoreboard_winner,
        "acceptance": acceptance,
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
        candidate_accepted=acceptance["candidate_accepted"],
        decision=acceptance["decision"],
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
