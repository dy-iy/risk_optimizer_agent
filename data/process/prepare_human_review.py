from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROCESS_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = PROCESS_DIR / "output" / "consistency_check" / "adjudicated_human_result.csv"
DEFAULT_OUTPUT = PROCESS_DIR / "output" / "human_review_priority_119.csv"

NO_RISK = "无明显风险"


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


def score_to_label(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def build_review_table(source: pd.DataFrame) -> pd.DataFrame:
    required = [
        "新闻id",
        "时间",
        "内容",
        "a_risk_score",
        "a_risk_types",
        "a_primary_risk_type",
        "b_risk_score",
        "b_risk_types",
        "b_primary_risk_type",
        "final_risk_score",
        "final_risk_types",
        "adjudicator_reasoning",
    ]
    missing = [column for column in required if column not in source.columns]
    if missing:
        raise ValueError(f"严重冲突裁决文件缺少列：{missing}")

    rows: list[dict[str, Any]] = []
    for _, row in source.iterrows():
        llm_types = [risk_type for risk_type in parse_list(row["final_risk_types"]) if risk_type != NO_RISK]
        llm_score = float(row["final_risk_score"])
        if 0 <= llm_score <= 10:
            llm_score *= 10
        llm_primary = str(row.get("final_primary_risk_type", "")).strip()
        if llm_primary not in llm_types:
            llm_primary = llm_types[0] if llm_types else NO_RISK

        rows.append(
            {
                "新闻id": row["新闻id"],
                "时间": row["时间"],
                "内容": row["内容"],
                "consistency_level": row.get("consistency_level", "C_severe_conflict"),
                "score_diff": row.get("score_diff", ""),
                "a_risk_score": row["a_risk_score"],
                "a_risk_label": row.get("a_risk_label", ""),
                "a_risk_types": row["a_risk_types"],
                "a_primary_risk_type": row["a_primary_risk_type"],
                "a_reason": row.get("a_reason", ""),
                "b_risk_score": row["b_risk_score"],
                "b_risk_label": row.get("b_risk_label", ""),
                "b_risk_types": row["b_risk_types"],
                "b_primary_risk_type": row["b_primary_risk_type"],
                "b_reason": row.get("b_reason", ""),
                "llm_adjudicated_score": int(llm_score) if llm_score.is_integer() else llm_score,
                "llm_adjudicated_label": score_to_label(llm_score),
                "llm_adjudicated_types": json.dumps(llm_types, ensure_ascii=False),
                "llm_adjudicated_primary_type": llm_primary,
                "llm_adjudicator_decision": row.get("adjudicator_decision", ""),
                "llm_adjudicator_confidence": row.get("adjudicator_confidence", ""),
                "llm_adjudicator_reasoning": row["adjudicator_reasoning"],
                # 以下字段由人工填写。只有 status=approved 的行才应进入正式 gold。
                "human_review_status": "pending",
                "human_risk_score": "",
                "human_risk_label": "",
                "human_risk_types": "",
                "human_primary_risk_type": "",
                "human_reason": "",
                "human_summary": "",
                "human_reviewer": "",
                "human_reviewed_at": "",
            }
        )

    result = pd.DataFrame(rows).sort_values(
        ["score_diff", "新闻id"],
        ascending=[False, True],
        kind="stable",
    )
    if result["新闻id"].duplicated().any():
        raise ValueError("人审表存在重复新闻id")
    return result.reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成严重冲突样本的人审工作表 CSV")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="允许覆盖已有的人审表")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.output.exists() and not args.force:
        raise FileExistsError(f"人审表已存在，为保护人工填写内容不会覆盖：{args.output}")

    source = pd.read_csv(args.input, encoding="utf-8-sig")
    review = build_review_table(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"人审表已生成：{args.output}，共 {len(review)} 条")


if __name__ == "__main__":
    main()
