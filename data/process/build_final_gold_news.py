from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


# ============================================================
# 配置区：只改这里
# ============================================================

# 默认：脚本位于 data/process，输入输出位于其下的 output 目录
BASE_DIR = Path(__file__).resolve().parent

# 输入文件
AGREEMENT_PATH = BASE_DIR / "output" / "consistency_check" / "candidate_gold_agreement.csv"
PRIORITY_ADJUDICATED_PATH = BASE_DIR / "output" / "consistency_check" / "adjudicated_human_result.csv"
LLM_ADJUDICATED_PATH = BASE_DIR /"output" / "consistency_check" / "adjudicated_llm_result.csv"
SAMPLE_PATH = BASE_DIR /"output" / "consistency_check" / "cleared_news_v2_deepseek_1000_labeled.csv"
HUMAN_REVIEW_PATH = BASE_DIR / "output" / "human_review_priority_119.csv"
CODEX_REVIEW_PATH = BASE_DIR / "output" / "codex_review_priority_119.csv"

# 输出文件
OUT_PATH = BASE_DIR / "output" / "final_gold_news_1000.csv"
OUT_WITH_SOURCE_PATH = BASE_DIR / "output" / "final_gold_news_1000_with_source.csv"
MANIFEST_PATH = BASE_DIR / "output" / "final_gold_news_1000_manifest.json"

# 是否用 cleared_news_v2 样例校验新闻 id 是否完全一致
VALIDATE_WITH_SAMPLE = True

# 是否额外输出带 gold_source 的调试版
WRITE_WITH_SOURCE = True

# 期望最终 gold 新闻数量
EXPECTED_ROWS = 1000


# ============================================================
# 固定配置：一般不用改
# ============================================================

NO_RISK = "无明显风险"

ALLOWED_RISK_TYPES = {
    "链上漏洞 / 攻击风险",
    "诈骗 / 跑路 / Rug Pull 风险",
    "监管与法律风险",
    "交易所与系统运维风险",
    "稳定币异常风险",
    "爆仓 / 清算风险",
    "大额转账 / 巨鲸行为风险",
    "异常行情波动风险",
    "项目治理 / 团队异常风险",
    "偿付能力 / 储备 / 流动性风险",
    "基础设施 / 协议层异常风险",
    "宏观 / 政策冲击风险",
}

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

CONFIDENCE_MAP = {
    "high": 0.95,
    "medium": 0.80,
    "low": 0.60,
}


def parse_list(value: Any) -> list[str]:
    """把 JSON / Python list 字符串解析成 list[str]。"""
    if value is None or pd.isna(value):
        return []

    if isinstance(value, list):
        raw = value
    else:
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return []

        try:
            raw = json.loads(text)
        except Exception:
            try:
                raw = ast.literal_eval(text)
            except Exception:
                raw = [text]

    if not isinstance(raw, list):
        raw = [raw]

    cleaned: list[str] = []
    for item in raw:
        if item is None or pd.isna(item):
            continue
        s = str(item).strip()
        if s and s.lower() != "nan":
            cleaned.append(s)

    return cleaned


def normalize_risk_types(types: list[str]) -> list[str]:
    """
    对齐 cleared_news_v2 风格：
    - 无明显风险：risk_types = []
    - 有风险：去重后保留风险类型
    """
    cleaned: list[str] = []

    for risk_type in types:
        risk_type = str(risk_type).strip()
        if not risk_type or risk_type == NO_RISK:
            continue
        if risk_type not in cleaned:
            cleaned.append(risk_type)

    return cleaned


def dumps_types(types: list[str]) -> str:
    return json.dumps(types, ensure_ascii=False)


def score_to_label(score_100: float) -> str:
    """
    按 cleared_news_v2 的 0-100 分制生成风险等级：
    - low:    0-39
    - medium: 40-69
    - high:   70-100
    """
    if score_100 >= 70:
        return "high"
    if score_100 >= 40:
        return "medium"
    return "low"


def normalize_score(value: Any, *, source: str) -> float:
    """
    分数统一到 0-100：
    - candidate_gold_agreement.csv: risk_score 已经是 0-100
    - adjudicated_human_result.csv / adjudicated_llm_result.csv: final_risk_score 通常是 0-10，需要乘 10
    """
    if value is None or pd.isna(value):
        raise ValueError(f"Missing score in {source}")

    score = float(value)

    if source != "agreement_candidate":
        if 0 <= score <= 10:
            score *= 10

    if not 0 <= score <= 100:
        raise ValueError(f"Score out of 0-100 range after normalization: {score} ({source})")

    return int(score) if score.is_integer() else score


def normalize_confidence(value: Any) -> float:
    """把 high/medium/low 或 0.95 / 95 统一成 0-1 小数。"""
    if value is None or pd.isna(value):
        return 0.80

    if isinstance(value, (int, float)):
        v = float(value)
        return round(v / 100, 4) if v > 1 else round(v, 4)

    text = str(value).strip().lower()

    if text in CONFIDENCE_MAP:
        return CONFIDENCE_MAP[text]

    try:
        v = float(text)
        return round(v / 100, 4) if v > 1 else round(v, 4)
    except Exception:
        return 0.80


def to_bool(value: Any) -> bool:
    """兼容 CSV 里 True/False、true/false、1/0 等写法。"""
    if isinstance(value, bool):
        return value

    if value is None or pd.isna(value):
        return False

    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "是"}


def pick_adjudicated_summary(row: pd.Series) -> str:
    """
    adjudicated 文件没有 final_summary，所以从 a_summary / b_summary 里选一个。
    """
    decision = str(row.get("adjudicator_decision", "")).strip()
    a_summary = str(row.get("a_summary", "") or "").strip()
    b_summary = str(row.get("b_summary", "") or "").strip()

    if decision == "adopt_a" and a_summary:
        return a_summary

    if decision == "adopt_b" and b_summary:
        return b_summary

    a_ok = to_bool(row.get("llm_a_is_reasonable", False))
    b_ok = to_bool(row.get("llm_b_is_reasonable", False))

    if a_ok and a_summary:
        return a_summary

    if b_ok and b_summary:
        return b_summary

    candidates = [s for s in [a_summary, b_summary] if s]
    if candidates:
        return min(candidates, key=len)

    return str(row.get("内容", ""))[:120]


def transform_agreement(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        types = normalize_risk_types(parse_list(row.get("risk_types")))
        score = normalize_score(row.get("risk_score"), source="agreement_candidate")

        rows.append(
            {
                "新闻id": row["新闻id"],
                "时间": row["时间"],
                "内容": row["内容"],
                "risk_score": score,
                "risk_label": score_to_label(float(score)),
                "risk_types": dumps_types(types),
                # 一致性检查阶段已经显式裁决过主风险类型，不应在最终构建时
                # 再用 risk_types 的排列顺序覆盖它。
                "primary_risk_type": (
                    str(row.get("primary_risk_type", "")).strip()
                    if str(row.get("primary_risk_type", "")).strip() in types
                    else (types[0] if types else NO_RISK)
                ),
                "reason": row.get("reason", ""),
                "confidence": normalize_confidence(row.get("confidence")),
                "summary": row.get("summary", ""),
                "gold_source": "agreement_candidate",
            }
        )

    return pd.DataFrame(rows)


def transform_adjudicated(df: pd.DataFrame, *, source: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        types = normalize_risk_types(parse_list(row.get("final_risk_types")))
        score = normalize_score(row.get("final_risk_score"), source=source)

        primary = str(row.get("final_primary_risk_type", "")).strip()
        if primary not in types:
            primary = types[0] if types else NO_RISK

        rows.append(
            {
                "新闻id": row["新闻id"],
                "时间": row["时间"],
                "内容": row["内容"],
                "risk_score": score,
                "risk_label": score_to_label(float(score)),
                "risk_types": dumps_types(types),
                "primary_risk_type": primary,
                "reason": row.get("adjudicator_reasoning", ""),
                "confidence": normalize_confidence(row.get("adjudicator_confidence")),
                "summary": pick_adjudicated_summary(row),
                "gold_source": source,
            }
        )

    return pd.DataFrame(rows)


def transform_human_reviewed(
    review_df: pd.DataFrame,
    priority_df: pd.DataFrame,
) -> pd.DataFrame:
    if review_df.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS + ["gold_source"])

    priority_by_id = priority_df.set_index("新闻id", drop=False)
    rows: list[dict[str, Any]] = []
    for _, row in review_df.iterrows():
        record_id = row["新闻id"]
        source_row = priority_by_id.loc[record_id]
        score = float(row["human_risk_score"])
        if not 0 <= score <= 100:
            raise ValueError(f"Human score outside 0-100: {record_id} -> {score}")

        types = normalize_risk_types(parse_list(row["human_risk_types"]))
        primary = str(row["human_primary_risk_type"]).strip()
        if not types:
            primary = NO_RISK

        human_summary = str(row.get("human_summary", "") or "").strip()
        reviewer_kind = str(row.get("reviewer_kind", "human")).strip().lower()
        gold_source = "codex_ai_reviewed" if reviewer_kind == "ai" else "human_reviewed"
        rows.append(
            {
                "新闻id": record_id,
                "时间": row["时间"],
                "内容": row["内容"],
                "risk_score": int(score) if score.is_integer() else score,
                "risk_label": score_to_label(score),
                "risk_types": dumps_types(types),
                "primary_risk_type": primary,
                "reason": row["human_reason"],
                "confidence": 1.0,
                "summary": human_summary or pick_adjudicated_summary(source_row),
                "gold_source": gold_source,
            }
        )
    return pd.DataFrame(rows)


def active_review_path() -> Path | None:
    if CODEX_REVIEW_PATH.exists():
        return CODEX_REVIEW_PATH
    if HUMAN_REVIEW_PATH.exists():
        return HUMAN_REVIEW_PATH
    return None


def load_human_review(priority_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    review_path = active_review_path()
    if review_path is None:
        return pd.DataFrame(), priority_df

    review = pd.read_csv(review_path, encoding="utf-8-sig", keep_default_na=False)
    required = [
        "新闻id",
        "human_review_status",
        "human_risk_score",
        "human_risk_types",
        "human_primary_risk_type",
        "human_reason",
        "human_reviewer",
        "human_reviewed_at",
    ]
    missing = [column for column in required if column not in review.columns]
    if missing:
        raise ValueError(f"Human review file missing columns: {missing}")
    if review["新闻id"].duplicated().any():
        raise ValueError("Human review file contains duplicate 新闻id")

    priority_ids = set(priority_df["新闻id"])
    extra_ids = sorted(set(review["新闻id"]) - priority_ids)
    if extra_ids:
        raise ValueError(f"Human review contains IDs outside severe-conflict bucket: {extra_ids[:20]}")

    allowed_status = {"pending", "approved", "needs_revision"}
    invalid_status = sorted(set(review["human_review_status"]) - allowed_status)
    if invalid_status:
        raise ValueError(f"Invalid human_review_status values: {invalid_status}")

    approved = review[review["human_review_status"] == "approved"].copy()
    if not approved.empty:
        required_values = [
            "human_risk_score",
            "human_risk_types",
            "human_primary_risk_type",
            "human_reason",
            "human_reviewer",
            "human_reviewed_at",
        ]
        incomplete = approved[required_values].apply(
            lambda column: column.astype(str).str.strip() == ""
        ).any(axis=1)
        if incomplete.any():
            bad_ids = approved.loc[incomplete, "新闻id"].head().tolist()
            raise ValueError(f"Approved human review rows are incomplete: {bad_ids}")

    pending_priority = priority_df[~priority_df["新闻id"].isin(set(approved["新闻id"]))].copy()
    return approved, pending_priority


def validate(final_df: pd.DataFrame, sample_df: pd.DataFrame | None = None) -> None:
    missing_columns = [column for column in TARGET_COLUMNS if column not in final_df.columns]
    if missing_columns:
        raise ValueError(f"Missing target columns: {missing_columns}")

    if final_df["新闻id"].duplicated().any():
        dupes = final_df.loc[final_df["新闻id"].duplicated(), "新闻id"].tolist()
        raise ValueError(f"Duplicate 新闻id found: {dupes[:20]}")

    if len(final_df) != EXPECTED_ROWS:
        raise ValueError(f"Expected {EXPECTED_ROWS} rows, got {len(final_df)}")

    null_counts = final_df[TARGET_COLUMNS].isna().sum()
    null_counts = null_counts[null_counts > 0]
    if not null_counts.empty:
        raise ValueError(f"Found null values: {null_counts.to_dict()}")

    if sample_df is not None:
        sample_ids = set(sample_df["新闻id"])
        final_ids = set(final_df["新闻id"])

        missing = sorted(sample_ids - final_ids)
        extra = sorted(final_ids - sample_ids)

        if missing or extra:
            raise ValueError(
                f"ID mismatch vs sample. missing={missing[:20]}, extra={extra[:20]}"
            )

    bad_scores = final_df[~final_df["risk_score"].astype(float).between(0, 100)]
    if not bad_scores.empty:
        examples = bad_scores[["新闻id", "risk_score"]].head().to_dict("records")
        raise ValueError(f"Found scores outside 0-100: {examples}")

    expected_labels = final_df["risk_score"].astype(float).map(score_to_label)
    bad_labels = final_df[final_df["risk_label"] != expected_labels]
    if not bad_labels.empty:
        examples = bad_labels[["新闻id", "risk_score", "risk_label"]].head().to_dict("records")
        raise ValueError(f"Found labels inconsistent with scores: {examples}")

    confidence = pd.to_numeric(final_df["confidence"], errors="coerce")
    bad_confidence = final_df[~confidence.between(0, 1)]
    if not bad_confidence.empty:
        examples = bad_confidence[["新闻id", "confidence"]].head().to_dict("records")
        raise ValueError(f"Found confidence outside 0-1: {examples}")

    bad_primary = final_df[
        final_df["primary_risk_type"].isna()
        | (final_df["primary_risk_type"].astype(str).str.strip() == "")
    ]
    if not bad_primary.empty:
        raise ValueError(
            f"Found empty primary_risk_type: {bad_primary['新闻id'].head().tolist()}"
        )

    semantic_errors: list[dict[str, Any]] = []
    for _, row in final_df.iterrows():
        types = parse_list(row["risk_types"])
        primary = str(row["primary_risk_type"]).strip()
        unknown_types = [risk_type for risk_type in types if risk_type not in ALLOWED_RISK_TYPES]

        if unknown_types:
            semantic_errors.append({"新闻id": row["新闻id"], "unknown_types": unknown_types})
        elif not types and primary != NO_RISK:
            semantic_errors.append({"新闻id": row["新闻id"], "empty_types_primary": primary})
        elif types and primary not in types:
            semantic_errors.append(
                {"新闻id": row["新闻id"], "types": types, "primary_not_in_types": primary}
            )

    if semantic_errors:
        raise ValueError(f"Found risk type semantic errors: {semantic_errors[:10]}")


def validate_adjudication(df: pd.DataFrame, *, source: str) -> None:
    required = ["新闻id", "final_risk_score", "final_risk_types", "adjudicator_error"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{source} missing required columns: {missing}")

    if df["新闻id"].duplicated().any():
        raise ValueError(f"{source} contains duplicate 新闻id")

    errors = df["adjudicator_error"].fillna("").astype(str).str.strip()
    failed = df[errors != ""]
    if not failed.empty:
        examples = failed[["新闻id", "adjudicator_error"]].head().to_dict("records")
        raise ValueError(f"{source} contains failed adjudications: {examples}")

    if df[["final_risk_score", "final_risk_types"]].isna().any().any():
        raise ValueError(f"{source} contains incomplete adjudications")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(final: pd.DataFrame) -> None:
    inputs = [
        AGREEMENT_PATH,
        PRIORITY_ADJUDICATED_PATH,
        LLM_ADJUDICATED_PATH,
        SAMPLE_PATH,
    ]
    review_path = active_review_path()
    if review_path is not None:
        inputs.append(review_path)
    pending_human_review_rows = int(
        (final["gold_source"] == "severe_conflict_llm_adjudicated_pending_human").sum()
    )
    codex_review_rows = int((final["gold_source"] == "codex_ai_reviewed").sum())
    human_review_rows = int((final["gold_source"] == "human_reviewed").sum())
    if pending_human_review_rows:
        dataset_status = "candidate_pending_review"
        review_note = "The severe-conflict bucket still contains rows pending independent review."
    elif codex_review_rows:
        dataset_status = "training_ready_ai_reviewed"
        review_note = (
            "Severe-conflict rows were independently reviewed by Codex AI; "
            "this must not be represented as human annotation."
        )
    else:
        dataset_status = "training_ready_human_reviewed"
        review_note = "All severe-conflict rows have approved human review."

    manifest = {
        "schema_version": 1,
        "dataset_status": dataset_status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(final),
        "columns": TARGET_COLUMNS,
        "source_counts": final["gold_source"].value_counts().to_dict(),
        "risk_label_counts": final["risk_label"].value_counts().to_dict(),
        "primary_risk_type_counts": final["primary_risk_type"].value_counts().to_dict(),
        "pending_human_review_rows": pending_human_review_rows,
        "codex_ai_reviewed_rows": codex_review_rows,
        "human_reviewed_rows": human_review_rows,
        "inputs": [
            {
                "path": str(path.relative_to(BASE_DIR.parents[1])).replace("\\", "/"),
                "sha256": sha256_file(path),
            }
            for path in inputs
        ],
        "outputs": {
            "dataset": {
                "path": str(OUT_PATH.relative_to(BASE_DIR.parents[1])).replace("\\", "/"),
                "sha256": sha256_file(OUT_PATH),
            },
            "dataset_with_source": {
                "path": str(OUT_WITH_SOURCE_PATH.relative_to(BASE_DIR.parents[1])).replace("\\", "/"),
                "sha256": sha256_file(OUT_WITH_SOURCE_PATH),
            },
        },
        "notes": [review_note],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def check_input_files() -> None:
    required_files = [
        AGREEMENT_PATH,
        PRIORITY_ADJUDICATED_PATH,
        LLM_ADJUDICATED_PATH,
    ]

    if VALIDATE_WITH_SAMPLE:
        required_files.append(SAMPLE_PATH)

    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "找不到以下文件，请确认 CSV 和脚本是否放在同一个文件夹：\n"
            + "\n".join(missing)
        )


def build_final_gold() -> pd.DataFrame:
    check_input_files()

    agreement = pd.read_csv(AGREEMENT_PATH)
    priority = pd.read_csv(PRIORITY_ADJUDICATED_PATH)
    llm = pd.read_csv(LLM_ADJUDICATED_PATH)

    validate_adjudication(priority, source="priority_adjudicated")
    validate_adjudication(llm, source="llm_adjudicated")
    human_reviewed, priority_pending = load_human_review(priority)

    sample = None
    if VALIDATE_WITH_SAMPLE and SAMPLE_PATH.exists():
        sample = pd.read_csv(SAMPLE_PATH)

    final = pd.concat(
        [
            transform_agreement(agreement),
            transform_adjudicated(
                priority_pending,
                source="severe_conflict_llm_adjudicated_pending_human",
            ),
            transform_adjudicated(llm, source="minor_conflict_llm_adjudicated"),
            transform_human_reviewed(human_reviewed, priority),
        ],
        ignore_index=True,
    )

    final = final.sort_values("新闻id", kind="stable").reset_index(drop=True)
    validate(final, sample)

    return final


def main() -> None:
    final = build_final_gold()

    # 主输出：严格对齐 cleared_news_v2 的 10 列结构
    final[TARGET_COLUMNS].to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    # 调试输出：多一列 gold_source，方便追踪来源
    if WRITE_WITH_SOURCE:
        final[TARGET_COLUMNS + ["gold_source"]].to_csv(
            OUT_WITH_SOURCE_PATH,
            index=False,
            encoding="utf-8-sig",
        )

    write_manifest(final)

    print(f"写入完成：{OUT_PATH}，共 {len(final)} 条")
    if WRITE_WITH_SOURCE:
        print(f"写入完成：{OUT_WITH_SOURCE_PATH}，共 {len(final)} 条")
    print(f"清单写入完成：{MANIFEST_PATH}")

    print("\n来源分布：")
    print(final["gold_source"].value_counts().to_string())

    print("\n风险等级分布：")
    print(final["risk_label"].value_counts().to_string())


if __name__ == "__main__":
    main()
