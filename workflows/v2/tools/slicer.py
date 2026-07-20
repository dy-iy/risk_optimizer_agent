from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    from .common import normalize_str_col, read_csv_file, safe_float, write_csv_file, write_json_file
    from .paths import resolve_project_root, resolve_versions_root
except ImportError:
    from common import normalize_str_col, read_csv_file, safe_float, write_csv_file, write_json_file
    from paths import resolve_project_root, resolve_versions_root


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
    type_mismatch_breakdown: dict = field(default_factory=dict)
    primary_policy_counterfactual: dict = field(default_factory=dict)


def reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    把最重要的字段放前面，方便人工查看。
    """
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
        "type_mismatch_kind",
        "type_mismatch_pair",
        "gold_reason",
        "gold_confidence",
        "gold_summary",
    ]
    front_cols = [c for c in front_cols if c in df.columns]
    other_cols = [c for c in df.columns if c not in front_cols]
    return df[front_cols + other_cols]


def slice_errors(
    merged_csv: str | Path,
    false_positive_csv: str | Path,
    false_negative_csv: str | Path,
    type_mismatch_csv: str | Path,
    score_diff_top_csv: str | Path,
    log_json: Optional[str | Path] = None,
    topn_score_diff: int = 200,
) -> SliceResult:
    """
    从 merged.csv 中切出四类样本：
    1) false_positive: gold=low, rule in {medium, high}
    2) false_negative: gold=high, rule=low
    3) type_mismatch: label 相同，但主类别不同
    4) score_diff_top: 分数差最大的 TopN

    说明：
    当前 merged.csv 已经是 inner merge 后的结果，
    整张表直接参与切片，不再依赖 _merge 列。
    """
    merged_csv = Path(merged_csv).resolve()
    false_positive_csv = Path(false_positive_csv).resolve()
    false_negative_csv = Path(false_negative_csv).resolve()
    type_mismatch_csv = Path(type_mismatch_csv).resolve()
    score_diff_top_csv = Path(score_diff_top_csv).resolve()
    if log_json is not None:
        log_json = Path(log_json).resolve()

    if not merged_csv.exists():
        return SliceResult(
            success=False,
            merged_csv=str(merged_csv),
            false_positive_csv=str(false_positive_csv),
            false_negative_csv=str(false_negative_csv),
            type_mismatch_csv=str(type_mismatch_csv),
            score_diff_top_csv=str(score_diff_top_csv),
            total_rows=0,
            matched_rows=0,
            false_positive_rows=0,
            false_negative_rows=0,
            type_mismatch_rows=0,
            score_diff_top_rows=0,
            error_message=f"merged 文件不存在: {merged_csv}",
        )

    df = read_csv_file(merged_csv)

    total_rows = len(df)

    if df.empty:
        return SliceResult(
            success=False,
            merged_csv=str(merged_csv),
            false_positive_csv=str(false_positive_csv),
            false_negative_csv=str(false_negative_csv),
            type_mismatch_csv=str(type_mismatch_csv),
            score_diff_top_csv=str(score_diff_top_csv),
            total_rows=0,
            matched_rows=0,
            false_positive_rows=0,
            false_negative_rows=0,
            type_mismatch_rows=0,
            score_diff_top_rows=0,
            error_message="merged 文件为空，无法切片",
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
            error_message=f"merged 文件缺少列: {missing_cols}",
        )

    matched_df = df.copy()
    matched_rows = len(matched_df)

    # 规范字段
    matched_df["gold_risk_label"] = normalize_str_col(matched_df, "gold_risk_label").str.lower()
    matched_df["rule_risk_label"] = normalize_str_col(matched_df, "rule_risk_label").str.lower()
    matched_df["gold_primary_risk_type"] = normalize_str_col(matched_df, "gold_primary_risk_type")
    matched_df["rule_primary_risk_type"] = normalize_str_col(matched_df, "rule_primary_risk_type")

    matched_df["gold_risk_score"] = matched_df["gold_risk_score"].apply(safe_float)
    matched_df["rule_risk_score"] = matched_df["rule_risk_score"].apply(safe_float)

    # 如果 merged 里没 score_diff，这里现算
    if "score_diff" not in matched_df.columns:
        matched_df["score_diff"] = (matched_df["gold_risk_score"] - matched_df["rule_risk_score"]).abs()
    else:
        matched_df["score_diff"] = matched_df["score_diff"].apply(safe_float)

    # 如果 merged 里没辅助列，这里补
    if "label_match" not in matched_df.columns:
        matched_df["label_match"] = matched_df["gold_risk_label"] == matched_df["rule_risk_label"]

    # 始终重算，避免 CSV 往返后布尔列被读成字符串。
    matched_df["primary_type_match"] = (
        matched_df["gold_primary_risk_type"] == matched_df["rule_primary_risk_type"]
    )

    no_risk_type = "无明显风险"
    gold_no_risk = matched_df["gold_primary_risk_type"] == no_risk_type
    rule_no_risk = matched_df["rule_primary_risk_type"] == no_risk_type
    matched_df["type_mismatch_kind"] = "matched"
    matched_df.loc[gold_no_risk & ~rule_no_risk, "type_mismatch_kind"] = "overassigned_weak_type"
    matched_df.loc[~gold_no_risk & rule_no_risk, "type_mismatch_kind"] = "missed_primary_type"
    matched_df.loc[~gold_no_risk & ~rule_no_risk & ~matched_df["primary_type_match"], "type_mismatch_kind"] = (
        "cross_type_confusion"
    )
    matched_df["type_mismatch_pair"] = (
        matched_df["gold_primary_risk_type"] + " -> " + matched_df["rule_primary_risk_type"]
    )

    # 1) 误报：银标 low，规则 medium/high
    false_positive_df = matched_df[
        (matched_df["gold_risk_label"] == "low") &
        (matched_df["rule_risk_label"].isin(["medium", "high"]))
    ].copy()

    # 2) 漏报：银标 high，规则 low
    false_negative_df = matched_df[
        (matched_df["gold_risk_label"] == "high") &
        (matched_df["rule_risk_label"] == "low")
    ].copy()

    # 3) 类别错配：label 相同，但主类别不同
    type_mismatch_df = matched_df[
        (matched_df["gold_risk_label"] == matched_df["rule_risk_label"]) &
        (matched_df["gold_primary_risk_type"] != matched_df["rule_primary_risk_type"])
    ].copy()

    # 反事实诊断：仅让 low 等级不再输出弱主类别，不改变任何 scorer、分数或风险等级。
    # 该指标可快速判断 type_mismatch 是否主要来自主类别仲裁，而不是 scorer 本身。
    counterfactual_primary = matched_df["rule_primary_risk_type"].copy()
    counterfactual_primary.loc[matched_df["rule_risk_label"] == "low"] = no_risk_type
    same_label = matched_df["gold_risk_label"] == matched_df["rule_risk_label"]
    counterfactual_mismatch = same_label & (
        matched_df["gold_primary_risk_type"] != counterfactual_primary
    )
    counterfactual_accuracy = float(
        (matched_df["gold_primary_risk_type"] == counterfactual_primary).mean()
    )
    baseline_primary_accuracy = float(matched_df["primary_type_match"].mean())

    # 4) 分数差最大的 TopN
    score_diff_top_df = matched_df.sort_values("score_diff", ascending=False).head(topn_score_diff).copy()

    # 排序
    false_positive_df = false_positive_df.sort_values(
        by=["score_diff", "rule_risk_score"],
        ascending=[False, False]
    )
    false_negative_df = false_negative_df.sort_values(
        by=["score_diff", "gold_risk_score"],
        ascending=[False, False]
    )
    type_mismatch_df = type_mismatch_df.sort_values(
        by=["score_diff", "gold_risk_score"],
        ascending=[False, False]
    )

    # 调整列顺序
    false_positive_df = reorder_columns(false_positive_df)
    false_negative_df = reorder_columns(false_negative_df)
    type_mismatch_df = reorder_columns(type_mismatch_df)
    score_diff_top_df = reorder_columns(score_diff_top_df)

    # 保存
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
        matched_rows=matched_rows,
        false_positive_rows=len(false_positive_df),
        false_negative_rows=len(false_negative_df),
        type_mismatch_rows=len(type_mismatch_df),
        score_diff_top_rows=len(score_diff_top_df),
        error_message="",
        type_mismatch_breakdown={
            str(k): int(v)
            for k, v in type_mismatch_df["type_mismatch_kind"].value_counts().items()
        },
        primary_policy_counterfactual={
            "policy": "force_no_risk_primary_when_rule_label_low",
            "baseline_type_mismatch_rows": int(len(type_mismatch_df)),
            "counterfactual_type_mismatch_rows": int(counterfactual_mismatch.sum()),
            "type_mismatch_delta": int(counterfactual_mismatch.sum() - len(type_mismatch_df)),
            "baseline_primary_type_accuracy": round(baseline_primary_accuracy, 6),
            "counterfactual_primary_type_accuracy": round(counterfactual_accuracy, 6),
            "changes_score_or_label": False,
        },
    )

    if log_json is not None:
        write_json_file(log_json, asdict(result))

    return result


if __name__ == "__main__":
    project_root = resolve_project_root()

    version_dir = resolve_versions_root(project_root) / "v20"
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
