from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

import pandas as pd

try:
    from .common import read_csv_file, safe_float, write_csv_file, write_json_file
except ImportError:
    from common import read_csv_file, safe_float, write_csv_file, write_json_file


TARGET_ERROR_TYPES = ("false_positive", "false_negative", "type_mismatch")
DEFAULT_FORBIDDEN_TRANSITIONS = (
    "correct->false_positive",
    "correct->false_negative",
    "correct->type_mismatch",
    "false_positive->type_mismatch",
    "false_negative->type_mismatch",
)


@dataclass
class ImpactAnalysisResult:
    success: bool
    baseline_merged_csv: str
    candidate_merged_csv: str
    report_json: str
    changed_rows_csv: str
    changed_rows: int
    preflight_passed: bool
    error_message: str = ""


def _norm(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _bool_series(df: pd.DataFrame, column: str, fallback: pd.Series) -> pd.Series:
    if column not in df.columns:
        return fallback
    values = df[column]
    if values.dtype == bool:
        return values.fillna(False)
    return _norm(values).str.lower().isin({"true", "1", "yes"})


def classify_outcomes(df: pd.DataFrame) -> pd.Series:
    """Classify rows with the same mutually exclusive priority as tools.slicer."""
    gold_label = _norm(df["gold_risk_label"]).str.lower()
    rule_label = _norm(df["rule_risk_label"]).str.lower()
    gold_type = _norm(df["gold_primary_risk_type"])
    rule_type = _norm(df["rule_primary_risk_type"])

    label_match = gold_label == rule_label
    type_match = gold_type == rule_type
    outcome = pd.Series("other_label_mismatch", index=df.index, dtype="object")
    outcome.loc[label_match & type_match] = "correct"
    outcome.loc[(gold_label == "low") & rule_label.isin(["medium", "high"])] = "false_positive"
    outcome.loc[(gold_label == "high") & (rule_label == "low")] = "false_negative"
    outcome.loc[label_match & ~type_match] = "type_mismatch"
    return outcome


def _resolve_id_column(df: pd.DataFrame) -> str:
    for candidate in ("新闻id", "news_id", "id"):
        if candidate in df.columns:
            return candidate
    for column in df.columns:
        if str(column).lower().endswith("id"):
            return str(column)
    return str(df.columns[0])


def _transition_counts(before: pd.Series, after: pd.Series) -> list[dict[str, Any]]:
    counts = (
        pd.DataFrame({"before": before, "after": after})
        .groupby(["before", "after"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    return counts.to_dict(orient="records")


def _target_counts(outcomes: pd.Series) -> dict[str, int]:
    values = outcomes.value_counts()
    return {name: int(values.get(name, 0)) for name in TARGET_ERROR_TYPES}


def _load_contract(analysis_json: str | Path | None) -> dict:
    if not analysis_json:
        return {}
    path = Path(analysis_json).resolve()
    if not path.exists():
        return {}
    import json

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    contract = data.get("patch_contract", {})
    return contract if isinstance(contract, dict) else {}


def analyze_candidate_impact(
    baseline_merged_csv: str | Path,
    candidate_merged_csv: str | Path,
    report_json: str | Path,
    changed_rows_csv: str | Path,
    analysis_json: str | Path | None = None,
) -> ImpactAnalysisResult:
    baseline_path = Path(baseline_merged_csv).resolve()
    candidate_path = Path(candidate_merged_csv).resolve()
    report_path = Path(report_json).resolve()
    rows_path = Path(changed_rows_csv).resolve()

    def failed(message: str) -> ImpactAnalysisResult:
        return ImpactAnalysisResult(
            success=False,
            baseline_merged_csv=str(baseline_path),
            candidate_merged_csv=str(candidate_path),
            report_json=str(report_path),
            changed_rows_csv=str(rows_path),
            changed_rows=0,
            preflight_passed=False,
            error_message=message,
        )

    if not baseline_path.exists():
        return failed(f"baseline merged file not found: {baseline_path}")
    if not candidate_path.exists():
        return failed(f"candidate merged file not found: {candidate_path}")

    baseline = read_csv_file(baseline_path)
    candidate = read_csv_file(candidate_path)
    required = {
        "gold_risk_label",
        "rule_risk_label",
        "gold_primary_risk_type",
        "rule_primary_risk_type",
    }
    missing = sorted((required - set(baseline.columns)) | (required - set(candidate.columns)))
    if missing:
        return failed(f"merged files missing required columns: {missing}")

    baseline_id = _resolve_id_column(baseline)
    candidate_id = _resolve_id_column(candidate)
    baseline[baseline_id] = _norm(baseline[baseline_id])
    candidate[candidate_id] = _norm(candidate[candidate_id])
    baseline = baseline.drop_duplicates(baseline_id, keep="first").set_index(baseline_id, drop=False)
    candidate = candidate.drop_duplicates(candidate_id, keep="first").set_index(candidate_id, drop=False)
    shared_ids = baseline.index.intersection(candidate.index)
    baseline = baseline.loc[shared_ids].copy()
    candidate = candidate.loc[shared_ids].copy()

    before_outcome = classify_outcomes(baseline)
    after_outcome = classify_outcomes(candidate)
    before_counts = _target_counts(before_outcome)
    after_counts = _target_counts(after_outcome)
    deltas = {key: after_counts[key] - before_counts[key] for key in TARGET_ERROR_TYPES}

    tracked_columns = [
        "rule_risk_score",
        "rule_risk_label",
        "rule_primary_risk_type",
        *[column for column in baseline.columns if str(column).startswith("score_")],
    ]
    tracked_columns = [
        column for column in dict.fromkeys(tracked_columns)
        if column in baseline.columns and column in candidate.columns
    ]
    changed_mask = before_outcome.ne(after_outcome)
    for column in tracked_columns:
        changed_mask |= _norm(baseline[column]).ne(_norm(candidate[column]))

    changed = pd.DataFrame(index=baseline.index[changed_mask])
    changed[baseline_id] = changed.index
    for column in (
        "gold_risk_label",
        "gold_primary_risk_type",
        "gold_risk_score",
        "rule_risk_score",
        "rule_risk_label",
        "rule_primary_risk_type",
    ):
        if column in baseline.columns:
            changed[f"before_{column}"] = baseline.loc[changed.index, column]
        if column in candidate.columns and column.startswith("rule_"):
            changed[f"after_{column}"] = candidate.loc[changed.index, column]
    changed["before_outcome"] = before_outcome.loc[changed.index]
    changed["after_outcome"] = after_outcome.loc[changed.index]
    changed["transition"] = changed["before_outcome"] + "->" + changed["after_outcome"]
    if "rule_risk_score" in baseline.columns and "rule_risk_score" in candidate.columns:
        changed["score_delta"] = [
            safe_float(candidate.at[index, "rule_risk_score"])
            - safe_float(baseline.at[index, "rule_risk_score"])
            for index in changed.index
        ]

    contract = _load_contract(analysis_json)
    forbidden = contract.get("forbidden_transitions", DEFAULT_FORBIDDEN_TRANSITIONS)
    if not isinstance(forbidden, list):
        forbidden = list(DEFAULT_FORBIDDEN_TRANSITIONS)
    max_regression = contract.get("max_target_regression", {})
    if not isinstance(max_regression, dict):
        max_regression = {}

    transition_counts = _transition_counts(before_outcome, after_outcome)
    transition_map = {
        f"{item['before']}->{item['after']}": int(item["count"])
        for item in transition_counts
    }
    violations: list[dict[str, Any]] = []
    for metric, delta in deltas.items():
        allowed = int(max_regression.get(metric, 0) or 0)
        if delta > allowed:
            violations.append(
                {
                    "kind": "target_metric_regression",
                    "metric": metric,
                    "delta": delta,
                    "allowed": allowed,
                }
            )
    for transition in forbidden:
        count = transition_map.get(str(transition), 0)
        if count:
            violations.append(
                {
                    "kind": "forbidden_transition",
                    "transition": str(transition),
                    "count": count,
                }
            )

    report = {
        "policy": "changed_row_preflight_v1",
        "preflight_passed": not violations,
        "matched_rows": int(len(shared_ids)),
        "changed_rows": int(changed_mask.sum()),
        "baseline_target_counts": before_counts,
        "candidate_target_counts": after_counts,
        "target_count_deltas": deltas,
        "transition_matrix": transition_counts,
        "forbidden_transitions": forbidden,
        "violations": violations,
        "patch_contract": contract,
        "top_changed_rows": changed.head(50).to_dict(orient="records"),
    }
    write_json_file(report_path, report)
    write_csv_file(changed.reset_index(drop=True), rows_path)
    return ImpactAnalysisResult(
        success=True,
        baseline_merged_csv=str(baseline_path),
        candidate_merged_csv=str(candidate_path),
        report_json=str(report_path),
        changed_rows_csv=str(rows_path),
        changed_rows=int(changed_mask.sum()),
        preflight_passed=not violations,
    )
