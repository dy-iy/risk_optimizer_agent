from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    from .common import read_csv_file, write_csv_file, write_json_file
    from .paths import resolve_project_root, resolve_versions_dir
    from .schema import GOLD_RENAME_MAP, MERGED_FRONT_COLUMNS, NEWS_ID_COL, PREDICTION_RENAME_MAP, SOURCE_COLUMNS
except ImportError:
    from common import read_csv_file, write_csv_file, write_json_file
    from paths import resolve_project_root, resolve_versions_dir
    from schema import GOLD_RENAME_MAP, MERGED_FRONT_COLUMNS, NEWS_ID_COL, PREDICTION_RENAME_MAP, SOURCE_COLUMNS


@dataclass
class MergeResult:
    success: bool
    gold_csv: str
    pred_csv: str
    merged_csv: str
    gold_rows: int
    pred_rows: int
    merged_rows: int
    error_message: str = ""


def _rename_existing_columns(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    return df.rename(columns={key: value for key, value in mapping.items() if key in df.columns})


def _ordered_columns(df: pd.DataFrame) -> list[str]:
    front_cols = [col for col in MERGED_FRONT_COLUMNS if col in df.columns]
    other_cols = [col for col in df.columns if col not in front_cols]
    return front_cols + other_cols


def merge_gold_and_predictions(
    gold_csv: str | Path,
    pred_csv: str | Path,
    merged_csv: str | Path,
    log_json: Optional[str | Path] = None,
) -> MergeResult:
    gold_csv = Path(gold_csv).resolve()
    pred_csv = Path(pred_csv).resolve()
    merged_csv = Path(merged_csv).resolve()
    if log_json is not None:
        log_json = Path(log_json).resolve()

    if not gold_csv.exists():
        return MergeResult(False, str(gold_csv), str(pred_csv), str(merged_csv), 0, 0, 0, f"银标文件不存在: {gold_csv}")

    if not pred_csv.exists():
        return MergeResult(False, str(gold_csv), str(pred_csv), str(merged_csv), 0, 0, 0, f"规则输出文件不存在: {pred_csv}")

    gold_df = read_csv_file(gold_csv)
    pred_df = read_csv_file(pred_csv)

    gold_rows = len(gold_df)
    pred_rows = len(pred_df)

    if NEWS_ID_COL not in gold_df.columns:
        return MergeResult(False, str(gold_csv), str(pred_csv), str(merged_csv), gold_rows, pred_rows, 0, f"银标文件缺少列: {NEWS_ID_COL}")

    if NEWS_ID_COL not in pred_df.columns:
        return MergeResult(False, str(gold_csv), str(pred_csv), str(merged_csv), gold_rows, pred_rows, 0, f"规则输出文件缺少列: {NEWS_ID_COL}")

    gold_df[NEWS_ID_COL] = gold_df[NEWS_ID_COL].astype(str).str.strip()
    pred_df[NEWS_ID_COL] = pred_df[NEWS_ID_COL].astype(str).str.strip()

    gold_df = gold_df.drop_duplicates(subset=[NEWS_ID_COL], keep="first").copy()
    pred_df = pred_df.drop_duplicates(subset=[NEWS_ID_COL], keep="first").copy()

    gold_df = _rename_existing_columns(gold_df, GOLD_RENAME_MAP)
    pred_df = _rename_existing_columns(pred_df, PREDICTION_RENAME_MAP)

    pred_df = pred_df.drop(columns=[col for col in SOURCE_COLUMNS if col in pred_df.columns], errors="ignore")
    merged_df = pd.merge(gold_df, pred_df, on=NEWS_ID_COL, how="inner")

    if "gold_risk_score" in merged_df.columns and "rule_risk_score" in merged_df.columns:
        merged_df["score_diff"] = (merged_df["gold_risk_score"] - merged_df["rule_risk_score"]).abs()

    if "gold_risk_label" in merged_df.columns and "rule_risk_label" in merged_df.columns:
        merged_df["label_match"] = merged_df["gold_risk_label"].fillna("") == merged_df["rule_risk_label"].fillna("")

    if "gold_primary_risk_type" in merged_df.columns and "rule_primary_risk_type" in merged_df.columns:
        merged_df["primary_type_match"] = (
            merged_df["gold_primary_risk_type"].fillna("") == merged_df["rule_primary_risk_type"].fillna("")
        )

    merged_df = merged_df[_ordered_columns(merged_df)]
    write_csv_file(merged_df, merged_csv)

    result = MergeResult(
        success=True,
        gold_csv=str(gold_csv),
        pred_csv=str(pred_csv),
        merged_csv=str(merged_csv),
        gold_rows=gold_rows,
        pred_rows=pred_rows,
        merged_rows=len(merged_df),
        error_message="",
    )

    if log_json is not None:
        write_json_file(log_json, asdict(result))

    return result


if __name__ == "__main__":
    project_root = resolve_project_root()

    version_dir = resolve_versions_dir(project_root) / "v20"
    gold_csv = project_root / "data" / "gold" / "cleared_news_v2_deepseek_1000_labeled.csv"
    pred_csv = version_dir / "reports" / "predictions" / "risk_labeler_v20_output.csv"
    merged_csv = version_dir / "reports" / "merged" / "risk_labeler_v20_merged.csv"
    log_json = version_dir / "reports" / "merged" / "risk_labeler_v20_merge_log.json"

    result = merge_gold_and_predictions(
        gold_csv=gold_csv,
        pred_csv=pred_csv,
        merged_csv=merged_csv,
        log_json=log_json,
    )

    print("=" * 60)
    print("MERGE RESULT")
    print("=" * 60)
    print(f"success      : {result.success}")
    print(f"gold_rows    : {result.gold_rows}")
    print(f"pred_rows    : {result.pred_rows}")
    print(f"merged_rows  : {result.merged_rows}")
    if result.error_message:
        print(f"error_message: {result.error_message}")
