from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

try:
    from .common import normalize_str_col, read_csv_file, safe_float, write_json_file
    from .paths import resolve_project_root, resolve_versions_root
except ImportError:
    from common import normalize_str_col, read_csv_file, safe_float, write_json_file
    from paths import resolve_project_root, resolve_versions_root


@dataclass
class EvalResult:
    success: bool
    merged_csv: str
    eval_json: str
    eval_rows: int
    total_rows: int
    matched_rows: int
    score_mae: float
    score_rmse: float
    label_accuracy: float
    primary_type_accuracy: float
    error_message: str = ""


def compute_rmse(y_true: pd.Series, y_pred: pd.Series) -> float:
    diff = y_true - y_pred
    return float(np.sqrt(np.mean(np.square(diff))))


def value_counts_dict(series: pd.Series) -> dict:
    vc = series.value_counts(dropna=False)
    return {str(k): int(v) for k, v in vc.items()}


def confusion_matrix_dict(y_true: pd.Series, y_pred: pd.Series, labels: list[str]) -> dict:
    result = {}
    for true_label in labels:
        result[true_label] = {}
        for pred_label in labels:
            cnt = int(((y_true == true_label) & (y_pred == pred_label)).sum())
            result[true_label][pred_label] = cnt
    return result


def top_type_mismatches(df: pd.DataFrame, topn: int = 20) -> list[dict]:
    if "gold_primary_risk_type" not in df.columns or "rule_primary_risk_type" not in df.columns:
        return []

    mis_df = df[df["gold_primary_risk_type"] != df["rule_primary_risk_type"]].copy()
    if mis_df.empty:
        return []

    grp = (
        mis_df.groupby(["gold_primary_risk_type", "rule_primary_risk_type"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(topn)
    )

    return grp.to_dict(orient="records")


def evaluate_merged_file(
    merged_csv: str | Path,
    eval_json: str | Path,
    details_json: Optional[str | Path] = None,
    evaluation_context: Optional[dict[str, Any]] = None,
) -> EvalResult:
    """
    读取 merged.csv 并评估：
    - score MAE / RMSE
    - label accuracy
    - primary type accuracy
    - confusion matrix
    - top type mismatches

    说明：
    当前 merged.csv 已经是 inner merge 后的结果，
    所以整张表直接参与评估，不再依赖 _merge 列。
    """
    merged_csv = Path(merged_csv).resolve()
    eval_json = Path(eval_json).resolve()
    if details_json is not None:
        details_json = Path(details_json).resolve()

    if not merged_csv.exists():
        return EvalResult(
            success=False,
            merged_csv=str(merged_csv),
            eval_json=str(eval_json),
            eval_rows=0,
            total_rows=0,
            matched_rows=0,
            score_mae=0.0,
            score_rmse=0.0,
            label_accuracy=0.0,
            primary_type_accuracy=0.0,
            error_message=f"merged 文件不存在: {merged_csv}",
        )

    df = read_csv_file(merged_csv)

    total_rows = len(df)

    if df.empty:
        return EvalResult(
            success=False,
            merged_csv=str(merged_csv),
            eval_json=str(eval_json),
            eval_rows=0,
            total_rows=0,
            matched_rows=0,
            score_mae=0.0,
            score_rmse=0.0,
            label_accuracy=0.0,
            primary_type_accuracy=0.0,
            error_message="merged 文件为空，无法评估",
        )

    required_cols = [
        "gold_risk_score",
        "rule_risk_score",
        "gold_risk_label",
        "rule_risk_label",
        "gold_primary_risk_type",
        "rule_primary_risk_type",
    ]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        return EvalResult(
            success=False,
            merged_csv=str(merged_csv),
            eval_json=str(eval_json),
            eval_rows=0,
            total_rows=total_rows,
            matched_rows=0,
            score_mae=0.0,
            score_rmse=0.0,
            label_accuracy=0.0,
            primary_type_accuracy=0.0,
            error_message=f"merged 文件缺少列: {', '.join(missing_cols)}",
        )

    eval_df = df.copy()
    eval_rows = len(eval_df)
    matched_rows = eval_rows

    # 规范列
    eval_df["gold_risk_score"] = eval_df["gold_risk_score"].apply(safe_float)
    eval_df["rule_risk_score"] = eval_df["rule_risk_score"].apply(safe_float)

    eval_df["gold_risk_label"] = normalize_str_col(eval_df, "gold_risk_label")
    eval_df["rule_risk_label"] = normalize_str_col(eval_df, "rule_risk_label")

    eval_df["gold_primary_risk_type"] = normalize_str_col(eval_df, "gold_primary_risk_type")
    eval_df["rule_primary_risk_type"] = normalize_str_col(eval_df, "rule_primary_risk_type")

    # score_diff 若不存在则现场补
    if "score_diff" not in eval_df.columns:
        eval_df["score_diff"] = (eval_df["gold_risk_score"] - eval_df["rule_risk_score"]).abs()

    # 1) 分数误差
    score_mae = float(np.mean(np.abs(eval_df["gold_risk_score"] - eval_df["rule_risk_score"])))
    score_rmse = compute_rmse(eval_df["gold_risk_score"], eval_df["rule_risk_score"])

    # 2) label 一致率
    label_match = eval_df["gold_risk_label"] == eval_df["rule_risk_label"]
    label_accuracy = float(label_match.mean())

    # 3) 主类别一致率
    primary_type_match = eval_df["gold_primary_risk_type"] == eval_df["rule_primary_risk_type"]
    primary_type_accuracy = float(primary_type_match.mean())

    # label confusion matrix
    label_order = ["low", "medium", "high"]
    label_confusion = confusion_matrix_dict(
        eval_df["gold_risk_label"],
        eval_df["rule_risk_label"],
        label_order,
    )

    # label 分布
    gold_label_dist = value_counts_dict(eval_df["gold_risk_label"])
    rule_label_dist = value_counts_dict(eval_df["rule_risk_label"])

    # primary type 分布
    gold_primary_dist = value_counts_dict(eval_df["gold_primary_risk_type"])
    rule_primary_dist = value_counts_dict(eval_df["rule_primary_risk_type"])

    # 高频类型错配
    type_mismatch_top20 = top_type_mismatches(eval_df, topn=20)

    summary = {
        "success": True,
        "merged_csv": str(merged_csv),
        "total_rows": int(total_rows),
        "matched_rows": int(matched_rows),
        "eval_rows": int(eval_rows),
        "score_metrics": {
            "mae": score_mae,
            "rmse": score_rmse,
        },
        "label_metrics": {
            "accuracy": label_accuracy,
            "gold_distribution": gold_label_dist,
            "rule_distribution": rule_label_dist,
            "confusion_matrix": label_confusion,
        },
        "primary_type_metrics": {
            "accuracy": primary_type_accuracy,
            "gold_distribution": gold_primary_dist,
            "rule_distribution": rule_primary_dist,
            "top_mismatches": type_mismatch_top20,
        },
    }
    if evaluation_context:
        summary["evaluation_context"] = evaluation_context

    write_json_file(eval_json, summary)

    if details_json is not None:
        details = {
            "label_match_rate": label_accuracy,
            "primary_type_match_rate": primary_type_accuracy,
            "score_diff_describe": eval_df["score_diff"].describe().to_dict(),
            "label_match_counts": value_counts_dict(label_match.astype(str)),
            "primary_type_match_counts": value_counts_dict(primary_type_match.astype(str)),
        }
        write_json_file(details_json, details)

    return EvalResult(
        success=True,
        merged_csv=str(merged_csv),
        eval_json=str(eval_json),
        eval_rows=int(eval_rows),
        total_rows=int(total_rows),
        matched_rows=int(matched_rows),
        score_mae=score_mae,
        score_rmse=score_rmse,
        label_accuracy=label_accuracy,
        primary_type_accuracy=primary_type_accuracy,
        error_message="",
    )


if __name__ == "__main__":
    project_root = resolve_project_root()

    version_dir = resolve_versions_root(project_root) / "v20"
    merged_csv = version_dir / "reports" / "merged" / "risk_labeler_v20_merged.csv"
    eval_json = version_dir / "reports" / "evals" / "risk_labeler_v20_eval.json"
    details_json = version_dir / "reports" / "evals" / "risk_labeler_v20_eval_details.json"

    result = evaluate_merged_file(
        merged_csv=merged_csv,
        eval_json=eval_json,
        details_json=details_json,
    )

    print("=" * 60)
    print("EVALUATION RESULT")
    print("=" * 60)
    print(f"success                : {result.success}")
    print(f"merged_csv             : {result.merged_csv}")
    print(f"eval_json              : {result.eval_json}")
    print(f"total_rows             : {result.total_rows}")
    print(f"matched_rows           : {result.matched_rows}")
    print(f"eval_rows              : {result.eval_rows}")
    print(f"score_mae              : {result.score_mae:.4f}")
    print(f"score_rmse             : {result.score_rmse:.4f}")
    print(f"label_accuracy         : {result.label_accuracy:.4f}")
    print(f"primary_type_accuracy  : {result.primary_type_accuracy:.4f}")

    if result.error_message:
        print(f"error_message          : {result.error_message}")
