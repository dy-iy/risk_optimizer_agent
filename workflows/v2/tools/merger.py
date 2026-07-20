from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    from .common import read_csv_file, write_csv_file, write_json_file
    from .paths import resolve_project_root, resolve_versions_root
except ImportError:
    from common import read_csv_file, write_csv_file, write_json_file
    from paths import resolve_project_root, resolve_versions_root


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

    if "新闻id" not in gold_df.columns:
        return MergeResult(False, str(gold_csv), str(pred_csv), str(merged_csv), gold_rows, pred_rows, 0, "银标文件缺少列: 新闻id")

    if "新闻id" not in pred_df.columns:
        return MergeResult(False, str(gold_csv), str(pred_csv), str(merged_csv), gold_rows, pred_rows, 0, "规则输出文件缺少列: 新闻id")

    gold_df["新闻id"] = gold_df["新闻id"].astype(str).str.strip()
    pred_df["新闻id"] = pred_df["新闻id"].astype(str).str.strip()

    gold_df = gold_df.drop_duplicates(subset=["新闻id"], keep="first").copy()
    pred_df = pred_df.drop_duplicates(subset=["新闻id"], keep="first").copy()

    gold_rename_map = {
        "risk_score": "gold_risk_score",
        "risk_label": "gold_risk_label",
        "risk_types": "gold_risk_types",
        "primary_risk_type": "gold_primary_risk_type",
        "reason": "gold_reason",
        "confidence": "gold_confidence",
        "summary": "gold_summary",
    }
    pred_rename_map = {
        "risk": "rule_risk_score",
        "rule_label": "rule_risk_label",
        "rule_types": "rule_risk_types",
        "rule_primary_type": "rule_primary_risk_type",
    }

    gold_df = gold_df.rename(columns={k: v for k, v in gold_rename_map.items() if k in gold_df.columns})
    pred_df = pred_df.rename(columns={k: v for k, v in pred_rename_map.items() if k in pred_df.columns})

    # 预测表里这些公共列和银标重复，只保留银标那一份
    dup_cols = [c for c in ["内容", "时间", "链接"] if c in pred_df.columns]
    pred_df = pred_df.drop(columns=dup_cols, errors="ignore")

    merged_df = pd.merge(gold_df, pred_df, on="新闻id", how="inner")

    if "gold_risk_score" in merged_df.columns and "rule_risk_score" in merged_df.columns:
        merged_df["score_diff"] = (merged_df["gold_risk_score"] - merged_df["rule_risk_score"]).abs()

    if "gold_risk_label" in merged_df.columns and "rule_risk_label" in merged_df.columns:
        merged_df["label_match"] = merged_df["gold_risk_label"].fillna("") == merged_df["rule_risk_label"].fillna("")

    if "gold_primary_risk_type" in merged_df.columns and "rule_primary_risk_type" in merged_df.columns:
        merged_df["primary_type_match"] = (
            merged_df["gold_primary_risk_type"].fillna("") == merged_df["rule_primary_risk_type"].fillna("")
        )

    front_cols = [
        "新闻id",
        "内容",
        "时间",
        "链接",
        "gold_risk_score",
        "rule_risk_score",
        "score_diff",
        "gold_risk_label",
        "rule_risk_label",
        "label_match",
        "gold_risk_types",
        "rule_risk_types",
        "gold_primary_risk_type",
        "rule_primary_risk_type",
        "primary_type_match",
        "gold_reason",
        "gold_confidence",
        "gold_summary",
    ]
    front_cols = [c for c in front_cols if c in merged_df.columns]
    other_cols = [c for c in merged_df.columns if c not in front_cols]
    merged_df = merged_df[front_cols + other_cols]

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

    version_dir = resolve_versions_root(project_root) / "v20"
    gold_csv = project_root / "data" / "gold" / "crypto_news_risk_gold_1000.csv"
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
