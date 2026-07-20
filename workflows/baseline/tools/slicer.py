from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    from .common import normalize_str_col, read_csv_file, safe_float, write_csv_file, write_json_file
    from .paths import resolve_project_root, resolve_versions_dir
    from .schema import MERGED_FRONT_COLUMNS, MERGED_REQUIRED_COLUMNS
except ImportError:
    from common import normalize_str_col, read_csv_file, safe_float, write_csv_file, write_json_file
    from paths import resolve_project_root, resolve_versions_dir
    from schema import MERGED_FRONT_COLUMNS, MERGED_REQUIRED_COLUMNS


@dataclass
class SliceResult:
    success: bool
    merged_csv: str
    false_positive_csv: str
    false_negative_csv: str
    type_mismatch_csv: str
    score_diff_top_csv: str
    total_rows: int
    matched_rows: int
    false_positive_rows: int
    false_negative_rows: int
    type_mismatch_rows: int
    score_diff_top_rows: int
    error_message: str = ""


def reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    front_cols = [col for col in MERGED_FRONT_COLUMNS if col in df.columns]
    other_cols = [col for col in df.columns if col not in front_cols]
    return df[front_cols + other_cols]


def _empty_result(
    *,
    merged_csv: Path,
    false_positive_csv: Path,
    false_negative_csv: Path,
    type_mismatch_csv: Path,
    score_diff_top_csv: Path,
    total_rows: int = 0,
    error_message: str,
) -> SliceResult:
    return SliceResult(
        success=False,
        merged_csv=str(merged_csv),
        false_positive_csv=str(false_positive_csv),
        false_negative_csv=str(false_negative_csv),
        type_mismatch_csv=str(type_mismatch_csv),
        score_diff_top_csv=str(score_diff_top_csv),
        total_rows=total_rows,
        matched_rows=0,
        false_positive_rows=0,
        false_negative_rows=0,
        type_mismatch_rows=0,
        score_diff_top_rows=0,
        error_message=error_message,
    )


def _normalize_matched_frame(df: pd.DataFrame) -> pd.DataFrame:
    matched_df = df.copy()
    matched_df["gold_risk_label"] = normalize_str_col(matched_df, "gold_risk_label").str.lower()
    matched_df["rule_risk_label"] = normalize_str_col(matched_df, "rule_risk_label").str.lower()
    matched_df["gold_primary_risk_type"] = normalize_str_col(matched_df, "gold_primary_risk_type")
    matched_df["rule_primary_risk_type"] = normalize_str_col(matched_df, "rule_primary_risk_type")
    matched_df["gold_risk_score"] = matched_df["gold_risk_score"].apply(safe_float)
    matched_df["rule_risk_score"] = matched_df["rule_risk_score"].apply(safe_float)

    if "score_diff" not in matched_df.columns:
        matched_df["score_diff"] = (matched_df["gold_risk_score"] - matched_df["rule_risk_score"]).abs()
    else:
        matched_df["score_diff"] = matched_df["score_diff"].apply(safe_float)

    if "label_match" not in matched_df.columns:
        matched_df["label_match"] = matched_df["gold_risk_label"] == matched_df["rule_risk_label"]

    if "primary_type_match" not in matched_df.columns:
        matched_df["primary_type_match"] = (
            matched_df["gold_primary_risk_type"] == matched_df["rule_primary_risk_type"]
        )

    return matched_df


def _build_error_slices(matched_df: pd.DataFrame, topn_score_diff: int) -> dict[str, pd.DataFrame]:
    false_positive_df = matched_df[
        (matched_df["gold_risk_label"] == "low") &
        (matched_df["rule_risk_label"].isin(["medium", "high"]))
    ].copy()

    false_negative_df = matched_df[
        (matched_df["gold_risk_label"] == "high") &
        (matched_df["rule_risk_label"] == "low")
    ].copy()

    type_mismatch_df = matched_df[
        (matched_df["gold_risk_label"] == matched_df["rule_risk_label"]) &
        (matched_df["gold_primary_risk_type"] != matched_df["rule_primary_risk_type"])
    ].copy()

    score_diff_top_df = matched_df.sort_values("score_diff", ascending=False).head(topn_score_diff).copy()

    return {
        "false_positive": false_positive_df.sort_values(
            by=["score_diff", "rule_risk_score"],
            ascending=[False, False],
        ),
        "false_negative": false_negative_df.sort_values(
            by=["score_diff", "gold_risk_score"],
            ascending=[False, False],
        ),
        "type_mismatch": type_mismatch_df.sort_values(
            by=["score_diff", "gold_risk_score"],
            ascending=[False, False],
        ),
        "score_diff_top": score_diff_top_df,
    }


def slice_errors(
    merged_csv: str | Path,
    false_positive_csv: str | Path,
    false_negative_csv: str | Path,
    type_mismatch_csv: str | Path,
    score_diff_top_csv: str | Path,
    log_json: Optional[str | Path] = None,
    topn_score_diff: int = 200,
) -> SliceResult:
    merged_csv = Path(merged_csv).resolve()
    false_positive_csv = Path(false_positive_csv).resolve()
    false_negative_csv = Path(false_negative_csv).resolve()
    type_mismatch_csv = Path(type_mismatch_csv).resolve()
    score_diff_top_csv = Path(score_diff_top_csv).resolve()
    if log_json is not None:
        log_json = Path(log_json).resolve()

    if not merged_csv.exists():
        return _empty_result(
            merged_csv=merged_csv,
            false_positive_csv=false_positive_csv,
            false_negative_csv=false_negative_csv,
            type_mismatch_csv=type_mismatch_csv,
            score_diff_top_csv=score_diff_top_csv,
            error_message=f"merged 文件不存在: {merged_csv}",
        )

    df = read_csv_file(merged_csv)
    total_rows = len(df)

    if df.empty:
        return _empty_result(
            merged_csv=merged_csv,
            false_positive_csv=false_positive_csv,
            false_negative_csv=false_negative_csv,
            type_mismatch_csv=type_mismatch_csv,
            score_diff_top_csv=score_diff_top_csv,
            error_message="merged 文件为空，无法切片",
        )

    missing_cols = [col for col in MERGED_REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        return _empty_result(
            merged_csv=merged_csv,
            false_positive_csv=false_positive_csv,
            false_negative_csv=false_negative_csv,
            type_mismatch_csv=type_mismatch_csv,
            score_diff_top_csv=score_diff_top_csv,
            total_rows=total_rows,
            error_message=f"merged 文件缺少列: {missing_cols}",
        )

    matched_df = _normalize_matched_frame(df)
    slices = _build_error_slices(matched_df, topn_score_diff)

    false_positive_df = reorder_columns(slices["false_positive"])
    false_negative_df = reorder_columns(slices["false_negative"])
    type_mismatch_df = reorder_columns(slices["type_mismatch"])
    score_diff_top_df = reorder_columns(slices["score_diff_top"])

    write_csv_file(false_positive_df, false_positive_csv)
    write_csv_file(false_negative_df, false_negative_csv)
    write_csv_file(type_mismatch_df, type_mismatch_csv)
    write_csv_file(score_diff_top_df, score_diff_top_csv)

    result = SliceResult(
        success=True,
        merged_csv=str(merged_csv),
        false_positive_csv=str(false_positive_csv),
        false_negative_csv=str(false_negative_csv),
        type_mismatch_csv=str(type_mismatch_csv),
        score_diff_top_csv=str(score_diff_top_csv),
        total_rows=total_rows,
        matched_rows=len(matched_df),
        false_positive_rows=len(false_positive_df),
        false_negative_rows=len(false_negative_df),
        type_mismatch_rows=len(type_mismatch_df),
        score_diff_top_rows=len(score_diff_top_df),
        error_message="",
    )

    if log_json is not None:
        write_json_file(log_json, asdict(result))

    return result


if __name__ == "__main__":
    project_root = resolve_project_root()

    version_dir = resolve_versions_dir(project_root) / "v20"
    merged_csv = version_dir / "reports" / "merged" / "risk_labeler_v20_merged.csv"
    false_positive_csv = version_dir / "reports" / "errors" / "risk_labeler_v20_false_positive.csv"
    false_negative_csv = version_dir / "reports" / "errors" / "risk_labeler_v20_false_negative.csv"
    type_mismatch_csv = version_dir / "reports" / "errors" / "risk_labeler_v20_type_mismatch.csv"
    score_diff_top_csv = version_dir / "reports" / "errors" / "risk_labeler_v20_score_diff_top.csv"
    log_json = version_dir / "reports" / "errors" / "risk_labeler_v20_slice_log.json"

    result = slice_errors(
        merged_csv=merged_csv,
        false_positive_csv=false_positive_csv,
        false_negative_csv=false_negative_csv,
        type_mismatch_csv=type_mismatch_csv,
        score_diff_top_csv=score_diff_top_csv,
        log_json=log_json,
        topn_score_diff=200,
    )

    print("=" * 60)
    print("SLICE RESULT")
    print("=" * 60)
    print(f"success                : {result.success}")
    print(f"merged_csv             : {result.merged_csv}")
    print(f"false_positive_csv     : {result.false_positive_csv}")
    print(f"false_negative_csv     : {result.false_negative_csv}")
    print(f"type_mismatch_csv      : {result.type_mismatch_csv}")
    print(f"score_diff_top_csv     : {result.score_diff_top_csv}")
    print(f"total_rows             : {result.total_rows}")
    print(f"matched_rows           : {result.matched_rows}")
    print(f"false_positive_rows    : {result.false_positive_rows}")
    print(f"false_negative_rows    : {result.false_negative_rows}")
    print(f"type_mismatch_rows     : {result.type_mismatch_rows}")
    print(f"score_diff_top_rows    : {result.score_diff_top_rows}")

    if result.error_message:
        print(f"error_message          : {result.error_message}")
