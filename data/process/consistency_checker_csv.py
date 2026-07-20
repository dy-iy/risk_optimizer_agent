from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


# ===== 在这里改路径和参数 =====
CSV_A_PATH = Path("data/process/output/LLM_label_1.csv")
CSV_B_PATH = Path("data/process/output/LLM_label_2.csv")
RECOVERED_CSV_B_PATH = Path("data/process/output/LLM_label_2_recovered.csv")
OUT_DIR = Path("data/process/output/consistency_check")

ID_COL = "新闻id"
TEXT_COL = "内容"
TIME_COL = "时间"

STRICT_SCORE_THRESHOLD = 10
SEVERE_SCORE_THRESHOLD = 20


NO_OBVIOUS_RISK = "无明显风险"

LABEL_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}

REQUIRED_COLS = [
    "risk_score",
    "risk_label",
    "risk_types",
    "primary_risk_type",
]


def label_from_score(score: int) -> str:
    if score <= 39:
        return "low"
    if score <= 69:
        return "medium"
    return "high"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        value = int(float(value))
    except Exception:
        return default
    return max(0, min(100, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        value = float(value)
    except Exception:
        return default
    return max(0.0, min(1.0, value))


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def read_csv_auto(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path)


def resolve_csv_b_path() -> Path:
    if CSV_B_PATH.exists():
        return CSV_B_PATH
    if RECOVERED_CSV_B_PATH.exists():
        return RECOVERED_CSV_B_PATH
    raise FileNotFoundError(
        f"找不到第二路标注：{CSV_B_PATH}；也找不到恢复快照：{RECOVERED_CSV_B_PATH}"
    )


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def parse_risk_types(value: Any) -> List[str]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass

    if isinstance(value, list):
        return dedupe_text_items(value)

    text = str(value).strip()
    if not text or text in {"[]", "nan", "None", "null"}:
        return []

    parsed = parse_list_like_text(text)
    if parsed is not None:
        return dedupe_text_items(parsed)

    for sep in ["|", "；", ";", ","]:
        if sep in text:
            return dedupe_text_items(text.split(sep))
    return dedupe_text_items([text])


def parse_list_like_text(text: str) -> List[Any] | None:
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, list):
            return parsed
    return None


def dedupe_text_items(items: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        text = safe_str(item).strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def normalize_annotation(row: pd.Series) -> Dict[str, Any]:
    risk_score = safe_int(row.get("risk_score", 0))
    risk_types = parse_risk_types(row.get("risk_types", []))
    primary = safe_str(row.get("primary_risk_type", "")).strip()

    if not risk_types:
        primary = NO_OBVIOUS_RISK
    elif primary not in risk_types:
        primary = risk_types[0]

    return {
        "risk_score": risk_score,
        "risk_label": label_from_score(risk_score),
        "raw_risk_label": safe_str(row.get("risk_label", "")) or label_from_score(risk_score),
        "risk_types": risk_types,
        "primary_risk_type": primary,
        "reason": safe_str(row.get("reason", "")),
        "confidence": safe_float(row.get("confidence", 0.0)),
        "summary": safe_str(row.get("summary", "")),
    }


def jaccard_similarity(types_a: List[str], types_b: List[str]) -> float:
    set_a = set(types_a)
    set_b = set(types_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def is_no_risk(annotation: Dict[str, Any]) -> bool:
    return not annotation["risk_types"] and annotation["primary_risk_type"] == NO_OBVIOUS_RISK


def compare_annotations(
    ann_a: Dict[str, Any],
    ann_b: Dict[str, Any],
) -> Dict[str, Any]:
    score_a = ann_a["risk_score"]
    score_b = ann_b["risk_score"]
    types_a = ann_a["risk_types"]
    types_b = ann_b["risk_types"]

    score_diff = abs(score_a - score_b)
    label_match = ann_a["risk_label"] == ann_b["risk_label"]
    primary_match = ann_a["primary_risk_type"] == ann_b["primary_risk_type"]
    types_jaccard = jaccard_similarity(types_a, types_b)
    label_distance = abs(
        LABEL_ORDER.get(ann_a["risk_label"], 0)
        - LABEL_ORDER.get(ann_b["risk_label"], 0)
    )

    set_a = set(types_a)
    set_b = set(types_b)
    one_no_risk_one_risk = is_no_risk(ann_a) != is_no_risk(ann_b)
    types_disjoint = bool(set_a and set_b and not (set_a & set_b))

    high_agreement = (
        label_match
        and score_diff <= 10
        and primary_match
        and types_jaccard >= 0.8
    )

    severe_conflict = (
        not label_match
        or score_diff > 20
        or one_no_risk_one_risk
        or (types_disjoint and score_diff > 10)
    )

    if high_agreement:
        consistency_level = "A_high_agreement"
        suggested_action = "auto_merge_candidate_gold"
    elif severe_conflict:
        consistency_level = "C_severe_conflict"
        suggested_action = "human_review_priority"
    else:
        consistency_level = "B_minor_conflict"
        suggested_action = "llm_adjudication"

    return {
        "score_a": score_a,
        "score_b": score_b,
        "score_diff": score_diff,
        "label_a": ann_a["risk_label"],
        "label_b": ann_b["risk_label"],
        "label_match": label_match,
        "label_distance": label_distance,
        "primary_a": ann_a["primary_risk_type"],
        "primary_b": ann_b["primary_risk_type"],
        "primary_match": primary_match,
        "types_a": types_a,
        "types_b": types_b,
        "types_exact_match": set_a == set_b,
        "types_jaccard": round(types_jaccard, 4),
        "one_no_risk_one_risk": one_no_risk_one_risk,
        "types_disjoint": types_disjoint,
        "consistency_level": consistency_level,
        "suggested_action": suggested_action,
    }


def json_dumps_cn(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def annotation_to_prefixed_cols(annotation: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    return {
        f"{prefix}_risk_score": annotation["risk_score"],
        f"{prefix}_risk_label": annotation["risk_label"],
        f"{prefix}_raw_risk_label": annotation["raw_risk_label"],
        f"{prefix}_risk_types": json_dumps_cn(annotation["risk_types"]),
        f"{prefix}_primary_risk_type": annotation["primary_risk_type"],
        f"{prefix}_reason": annotation["reason"],
        f"{prefix}_confidence": annotation["confidence"],
        f"{prefix}_summary": annotation["summary"],
    }


def merge_candidate_gold(
    record_id: Any,
    time_value: str,
    news_text: str,
    ann_a: Dict[str, Any],
    ann_b: Dict[str, Any],
) -> Dict[str, Any]:
    final_score = round((ann_a["risk_score"] + ann_b["risk_score"]) / 2)
    final_types = dedupe_text_items(ann_a["risk_types"] + ann_b["risk_types"])

    if not final_types:
        primary = NO_OBVIOUS_RISK
    elif ann_a["primary_risk_type"] == ann_b["primary_risk_type"]:
        primary = ann_a["primary_risk_type"]
    else:
        primary = final_types[0]

    better_ann = ann_a if ann_a["confidence"] >= ann_b["confidence"] else ann_b

    return {
        ID_COL: record_id,
        TIME_COL: time_value,
        TEXT_COL: news_text,
        "risk_score": final_score,
        "risk_label": label_from_score(final_score),
        "risk_types": json_dumps_cn(final_types),
        "primary_risk_type": primary,
        "reason": better_ann["reason"],
        "confidence": round((ann_a["confidence"] + ann_b["confidence"]) / 2, 2),
        "summary": better_ann["summary"],
        "gold_source": "agreement_candidate",
    }


def build_summary(report_df: pd.DataFrame, only_a: List[Any], only_b: List[Any]) -> Dict[str, Any]:
    total = len(report_df)
    if total == 0:
        return {
            "total_aligned": 0,
            "level_counts": {},
            "missing": {"only_in_a": len(only_a), "only_in_b": len(only_b)},
        }

    level_counts = report_df["consistency_level"].value_counts().to_dict()
    for key in ["A_high_agreement", "B_minor_conflict", "C_severe_conflict"]:
        level_counts.setdefault(key, 0)

    return {
        "total_aligned": total,
        "level_counts": level_counts,
        "level_ratios": {key: round(value / total, 4) for key, value in level_counts.items()},
        "avg_score_diff": round(float(report_df["score_diff"].mean()), 4),
        "label_agreement_rate": round(float(report_df["label_match"].mean()), 4),
        "primary_agreement_rate": round(float(report_df["primary_match"].mean()), 4),
        "types_exact_rate": round(float(report_df["types_exact_match"].mean()), 4),
        "avg_types_jaccard": round(float(report_df["types_jaccard"].mean()), 4),
        "missing": {
            "only_in_a": len(only_a),
            "only_in_b": len(only_b),
            "only_in_a_ids_preview": [str(x) for x in only_a[:30]],
            "only_in_b_ids_preview": [str(x) for x in only_b[:30]],
        },
    }


def validate_input(df: pd.DataFrame, name: str) -> None:
    required_cols = [ID_COL, TEXT_COL, *REQUIRED_COLS]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"CSV {name} 缺少必要列: {missing_cols}. 当前列: {list(df.columns)}")

    if df[ID_COL].duplicated().any():
        dup = df.loc[df[ID_COL].duplicated(), ID_COL].head(10).tolist()
        raise ValueError(f"CSV {name} 存在重复 {ID_COL}: {dup}")


def build_base_row(record_id: Any, row_a: pd.Series, row_b: pd.Series) -> Dict[str, Any]:
    return {
        ID_COL: record_id,
        TIME_COL: safe_str(row_a.get(TIME_COL, "")) or safe_str(row_b.get(TIME_COL, "")),
        TEXT_COL: safe_str(row_a.get(TEXT_COL, "")) or safe_str(row_b.get(TEXT_COL, "")),
    }


def run_consistency_check() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df_a = read_csv_auto(CSV_A_PATH)
    csv_b_path = resolve_csv_b_path()
    df_b = read_csv_auto(csv_b_path)
    validate_input(df_a, "A")
    validate_input(df_b, "B")

    # 保存本次比较所用的完整输入快照，避免上游文件被覆盖或删除后无法回放。
    write_csv(df_a, OUT_DIR / "input_a_snapshot.csv")
    write_csv(df_b, OUT_DIR / "input_b_snapshot.csv")

    ids_a = set(df_a[ID_COL].tolist())
    ids_b = set(df_b[ID_COL].tolist())
    common_ids = sorted(ids_a & ids_b)
    only_a = sorted(ids_a - ids_b)
    only_b = sorted(ids_b - ids_a)

    df_a_indexed = df_a.set_index(ID_COL, drop=False)
    df_b_indexed = df_b.set_index(ID_COL, drop=False)

    report_rows: List[Dict[str, Any]] = []
    candidate_gold_rows: List[Dict[str, Any]] = []
    adjudication_rows: List[Dict[str, Any]] = []
    human_review_rows: List[Dict[str, Any]] = []

    for record_id in common_ids:
        row_a = df_a_indexed.loc[record_id]
        row_b = df_b_indexed.loc[record_id]
        ann_a = normalize_annotation(row_a)
        ann_b = normalize_annotation(row_b)
        metrics = compare_annotations(ann_a, ann_b)
        base = build_base_row(record_id, row_a, row_b)

        report_rows.append(
            {
                **base,
                **metrics,
                "types_a": json_dumps_cn(metrics["types_a"]),
                "types_b": json_dumps_cn(metrics["types_b"]),
                "summary_a": ann_a["summary"],
                "summary_b": ann_b["summary"],
                "reason_a": ann_a["reason"],
                "reason_b": ann_b["reason"],
            }
        )

        review_row = {
            **base,
            **metrics,
            "types_a": json_dumps_cn(metrics["types_a"]),
            "types_b": json_dumps_cn(metrics["types_b"]),
            **annotation_to_prefixed_cols(ann_a, "a"),
            **annotation_to_prefixed_cols(ann_b, "b"),
        }

        if metrics["consistency_level"] == "A_high_agreement":
            candidate_gold_rows.append(
                merge_candidate_gold(base[ID_COL], base[TIME_COL], base[TEXT_COL], ann_a, ann_b)
            )
        elif metrics["consistency_level"] == "B_minor_conflict":
            adjudication_rows.append(review_row)
        else:
            human_review_rows.append(review_row)

    report_df = pd.DataFrame(report_rows)
    write_outputs(
        report_df=report_df,
        candidate_gold_df=pd.DataFrame(candidate_gold_rows),
        adjudication_df=pd.DataFrame(adjudication_rows),
        human_review_df=pd.DataFrame(human_review_rows),
        summary=build_summary(report_df, only_a, only_b),
    )


def write_outputs(
    report_df: pd.DataFrame,
    candidate_gold_df: pd.DataFrame,
    adjudication_df: pd.DataFrame,
    human_review_df: pd.DataFrame,
    summary: Dict[str, Any],
) -> None:
    write_csv(report_df, OUT_DIR / "consistency_report.csv")
    write_csv(candidate_gold_df, OUT_DIR / "candidate_gold_agreement.csv")
    write_csv(adjudication_df, OUT_DIR / "need_llm_adjudication.csv")
    write_csv(human_review_df, OUT_DIR / "need_human_review_priority.csv")

    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n输出目录: {OUT_DIR}")


if __name__ == "__main__":
    run_consistency_check()
