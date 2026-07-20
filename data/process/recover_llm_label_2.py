from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROCESS_DIR / "output"
CHECK_DIR = OUTPUT_DIR / "consistency_check"

RAW_PATH = REPO_ROOT / "data" / "input" / "raw_1000_news.csv"
LABEL_A_PATH = OUTPUT_DIR / "LLM_label_1.csv"
REPORT_PATH = CHECK_DIR / "consistency_report.csv"
AGREEMENT_PATH = CHECK_DIR / "candidate_gold_agreement.csv"
MINOR_PATH = CHECK_DIR / "need_llm_adjudication.csv"
SEVERE_PATH = CHECK_DIR / "need_human_review_priority.csv"

DEFAULT_OUTPUT_PATH = OUTPUT_DIR / "LLM_label_2_recovered.csv"
DEFAULT_MANIFEST_PATH = OUTPUT_DIR / "LLM_label_2_recovered_manifest.json"

ID_COL = "新闻id"
TARGET_COLUMNS = [
    "新闻id",
    "时间",
    "内容",
    "risk_score",
    "risk_label",
    "risk_types",
    "primary_risk_type",
    "reason",
    "confidence",
    "summary",
]


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [text]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_conflict_rows(minor: pd.DataFrame, severe: pd.DataFrame) -> pd.DataFrame:
    conflict = pd.concat([minor, severe], ignore_index=True)
    rename = {
        "b_risk_score": "risk_score",
        "b_risk_label": "risk_label",
        "b_risk_types": "risk_types",
        "b_primary_risk_type": "primary_risk_type",
        "b_reason": "reason",
        "b_confidence": "confidence",
        "b_summary": "summary",
    }
    result = conflict.rename(columns=rename)
    return result[TARGET_COLUMNS].copy()


def build_agreement_rows(
    report: pd.DataFrame,
    agreement: pd.DataFrame,
    label_a: pd.DataFrame,
) -> pd.DataFrame:
    agreement_ids = set(agreement[ID_COL])
    rows = report[report[ID_COL].isin(agreement_ids)].copy()

    a_confidence = label_a[[ID_COL, "confidence"]].rename(
        columns={"confidence": "a_confidence"}
    )
    merged_confidence = agreement[[ID_COL, "confidence"]].rename(
        columns={"confidence": "merged_confidence"}
    )
    rows = rows.merge(a_confidence, on=ID_COL, how="left", validate="one_to_one")
    rows = rows.merge(merged_confidence, on=ID_COL, how="left", validate="one_to_one")

    def recover_confidence(row: pd.Series) -> float:
        a_confidence_value = float(row["a_confidence"])
        merged_confidence_value = float(row["merged_confidence"])
        estimate = 2 * merged_confidence_value - a_confidence_value

        candidate_reason = str(row.get("candidate_reason", ""))
        candidate_summary = str(row.get("candidate_summary", ""))
        a_selected = (
            candidate_reason == str(row.get("reason_a", ""))
            and candidate_reason != str(row.get("reason_b", ""))
        ) or (
            candidate_summary == str(row.get("summary_a", ""))
            and candidate_summary != str(row.get("summary_b", ""))
        )
        b_selected = (
            candidate_reason == str(row.get("reason_b", ""))
            and candidate_reason != str(row.get("reason_a", ""))
        ) or (
            candidate_summary == str(row.get("summary_b", ""))
            and candidate_summary != str(row.get("summary_a", ""))
        )

        # 历史 merge 逻辑在 A confidence >= B confidence 时选择 A。
        # 在 0.001 网格中寻找既能重现两位平均值、又能重现 A/B 选择结果的值。
        candidates = []
        for integer_value in range(1001):
            b_confidence_value = integer_value / 1000
            if round((a_confidence_value + b_confidence_value) / 2, 2) != merged_confidence_value:
                continue
            if b_selected and not b_confidence_value > a_confidence_value:
                continue
            if a_selected and not b_confidence_value <= a_confidence_value:
                continue
            candidates.append(b_confidence_value)

        if not candidates:
            raise ValueError(
                f"无法恢复 新闻id={row[ID_COL]} 的 B confidence："
                f"A={a_confidence_value}, merged={merged_confidence_value}"
            )
        return min(candidates, key=lambda value: abs(value - estimate))

    selected_annotation = agreement[[ID_COL, "reason", "summary"]].rename(
        columns={"reason": "candidate_reason", "summary": "candidate_summary"}
    )
    rows = rows.merge(
        selected_annotation,
        on=ID_COL,
        how="left",
        validate="one_to_one",
    )
    rows["confidence"] = rows.apply(recover_confidence, axis=1)
    if not rows["confidence"].between(0, 1).all():
        bad = rows.loc[~rows["confidence"].between(0, 1), [ID_COL, "confidence"]]
        raise ValueError(f"反推的 B 路 confidence 超出 0-1：{bad.head().to_dict('records')}")

    rename = {
        "score_b": "risk_score",
        "label_b": "risk_label",
        "types_b": "risk_types",
        "primary_b": "primary_risk_type",
        "reason_b": "reason",
        "summary_b": "summary",
    }
    rows = rows.rename(columns=rename)
    return rows[TARGET_COLUMNS].copy()


def validate_recovered(recovered: pd.DataFrame, raw: pd.DataFrame, report: pd.DataFrame) -> None:
    if list(recovered.columns) != TARGET_COLUMNS:
        raise ValueError(f"恢复列不符合标准结构：{list(recovered.columns)}")
    if len(recovered) != len(raw):
        raise ValueError(f"恢复行数 {len(recovered)} 与原始行数 {len(raw)} 不一致")
    if recovered[ID_COL].duplicated().any():
        raise ValueError("恢复结果存在重复新闻id")
    if recovered[TARGET_COLUMNS].isna().any().any():
        nulls = recovered[TARGET_COLUMNS].isna().sum()
        raise ValueError(f"恢复结果存在空值：{nulls[nulls > 0].to_dict()}")

    metadata = recovered[[ID_COL, "时间", "内容"]].merge(
        raw[[ID_COL, "时间", "内容"]],
        on=ID_COL,
        suffixes=("_recovered", "_raw"),
        validate="one_to_one",
    )
    if not (
        (metadata["时间_recovered"] == metadata["时间_raw"]).all()
        and (metadata["内容_recovered"] == metadata["内容_raw"]).all()
    ):
        raise ValueError("恢复结果的时间或正文与 raw_1000_news.csv 不一致")

    check = recovered.merge(
        report[[ID_COL, "score_b", "label_b", "types_b", "primary_b"]],
        on=ID_COL,
        validate="one_to_one",
    )
    mismatches: list[dict[str, Any]] = []
    for _, row in check.iterrows():
        if float(row["risk_score"]) != float(row["score_b"]):
            mismatches.append({"新闻id": row[ID_COL], "field": "risk_score"})
        elif row["risk_label"] != row["label_b"]:
            mismatches.append({"新闻id": row[ID_COL], "field": "risk_label"})
        elif parse_list(row["risk_types"]) != parse_list(row["types_b"]):
            mismatches.append({"新闻id": row[ID_COL], "field": "risk_types"})
        elif row["primary_risk_type"] != row["primary_b"]:
            mismatches.append({"新闻id": row[ID_COL], "field": "primary_risk_type"})

    if mismatches:
        raise ValueError(f"恢复结果与历史一致性报告不符：{mismatches[:10]}")


def recover(output_path: Path, manifest_path: Path) -> pd.DataFrame:
    inputs = [RAW_PATH, LABEL_A_PATH, REPORT_PATH, AGREEMENT_PATH, MINOR_PATH, SEVERE_PATH]
    missing = [str(path) for path in inputs if not path.exists()]
    if missing:
        raise FileNotFoundError("缺少恢复所需文件：\n" + "\n".join(missing))

    raw = read_csv(RAW_PATH)
    label_a = read_csv(LABEL_A_PATH)
    report = read_csv(REPORT_PATH)
    agreement = read_csv(AGREEMENT_PATH)
    minor = read_csv(MINOR_PATH)
    severe = read_csv(SEVERE_PATH)

    recovered = pd.concat(
        [
            build_agreement_rows(report, agreement, label_a),
            build_conflict_rows(minor, severe),
        ],
        ignore_index=True,
    ).sort_values(ID_COL, kind="stable").reset_index(drop=True)

    validate_recovered(recovered, raw, report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    recovered.to_csv(output_path, index=False, encoding="utf-8-sig")

    manifest = {
        "schema_version": 1,
        "status": "recovered_snapshot_not_original_bytes",
        "rows": len(recovered),
        "output": str(output_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "output_sha256": sha256_file(output_path),
        "exact_fields_recovered_from_report": [
            "新闻id",
            "时间",
            "内容",
            "risk_score",
            "risk_label",
            "risk_types",
            "primary_risk_type",
            "reason",
            "summary",
        ],
        "confidence_recovery": {
            "conflict_rows": "copied exactly from historical review inputs",
            "agreement_rows": "constrained reconstruction from rounded merged confidence plus the historically selected A/B reason and summary",
        },
        "inputs": [
            {
                "path": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "sha256": sha256_file(path),
            }
            for path in inputs
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return recovered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从历史一致性产物恢复缺失的第二路标注快照")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    recovered = recover(args.output, args.manifest)
    print(f"恢复完成：{args.output}，共 {len(recovered)} 条")
    print(f"恢复清单：{args.manifest}")


if __name__ == "__main__":
    main()
