from __future__ import annotations

import argparse
import ast
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from tools.common import (
    StageProgress,
    call_chat_json,
    ensure_parent_dir,
    normalize_str_col,
    read_csv_file,
    safe_float,
    write_json_file,
)
from tools.paths import resolve_project_root
from prompts.analyzer import build_analyzer_messages
# =========================
# 基础配置
# =========================
SCORE_COLS = [
    "score_hack",
    "score_fraud",
    "score_regulatory",
    "score_outage",
    "score_stablecoin",
    "score_liquidation",
    "score_whale",
    "score_volatility",
    "score_team",
    "score_solvency",
    "score_infra",
    "score_macro",
]

DEFAULT_RISK_NAME_MAP = {
    "score_hack": "链上漏洞 / 攻击风险",
    "score_fraud": "诈骗 / 跑路 / Rug Pull 风险",
    "score_regulatory": "监管与法律风险",
    "score_outage": "交易所与系统运维风险",
    "score_stablecoin": "稳定币异常风险",
    "score_liquidation": "爆仓 / 清算风险",
    "score_whale": "大额转账 / 巨鲸行为风险",
    "score_volatility": "异常行情波动风险",
    "score_team": "项目治理 / 团队异常风险",
    "score_solvency": "偿付能力 / 储备 / 流动性风险",
    "score_infra": "基础设施 / 协议层异常风险",
    "score_macro": "宏观 / 政策冲击风险",
}
DEFAULT_RISK_TYPE_TO_SCORE_COL = {v: k for k, v in DEFAULT_RISK_NAME_MAP.items()}

DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_SAMPLE_ROWS_PER_BUCKET = int(os.environ.get("LLM_SAMPLE_ROWS_PER_BUCKET", "12"))
DEFAULT_TEXT_LIMIT = int(os.environ.get("LLM_TEXT_LIMIT", "220"))

@dataclass
class AnalyzeLLMResult:
    success: bool
    false_positive_csv: str
    false_negative_csv: str
    type_mismatch_csv: str
    score_diff_top_csv: str
    analysis_json: str
    analysis_markdown: str
    llm_payload_json: str
    false_positive_rows: int
    false_negative_rows: int
    type_mismatch_rows: int
    score_diff_top_rows: int
    error_message: str = ""
    model: str = ""


def value_counts_top(series: pd.Series, topn: int = 15) -> list[dict]:
    s = series.fillna("").astype(str).str.strip()
    vc = s.value_counts(dropna=False).head(topn)
    return [{"name": str(k), "count": int(v)} for k, v in vc.items()]


def score_trigger_summary(df: pd.DataFrame, threshold: float = 0.30) -> list[dict]:
    result = []
    for col in SCORE_COLS:
        if col not in df.columns:
            continue
        vals = df[col].apply(safe_float)
        cnt = int((vals >= threshold).sum())
        mean_val = float(vals.mean()) if len(vals) > 0 else 0.0
        max_val = float(vals.max()) if len(vals) > 0 else 0.0
        result.append(
            {
                "score_col": col,
                "trigger_count": cnt,
                "mean_value": round(mean_val, 4),
                "max_value": round(max_val, 4),
            }
        )
    result.sort(key=lambda x: x["trigger_count"], reverse=True)
    return result


def round_float(value: Any, digits: int = 4) -> float:
    return round(safe_float(value), digits)


def pair_mismatch_top(df: pd.DataFrame, gold_col: str, rule_col: str, topn: int = 20) -> list[dict]:
    if gold_col not in df.columns or rule_col not in df.columns:
        return []

    temp = df[[gold_col, rule_col]].copy()
    temp[gold_col] = temp[gold_col].fillna("").astype(str).str.strip()
    temp[rule_col] = temp[rule_col].fillna("").astype(str).str.strip()

    grp = (
        temp.groupby([gold_col, rule_col])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(topn)
    )
    return grp.to_dict(orient="records")


def clip_text(text: Any, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    s = str(text or "")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def top_triggered_scores_for_row(row: pd.Series, threshold: float = 0.30, topn: int = 4) -> list[dict]:
    hits = []
    for col in SCORE_COLS:
        if col not in row.index:
            continue
        val = safe_float(row.get(col, 0.0))
        if val >= threshold:
            hits.append({"score_col": col, "value": round(val, 4)})
    hits.sort(key=lambda x: x["value"], reverse=True)
    return hits[:topn]


def all_score_values_for_row(row: pd.Series, topn: int = 8) -> list[dict]:
    values = []
    for col in SCORE_COLS:
        if col not in row.index:
            continue
        values.append({"score_col": col, "value": round_float(row.get(col, 0.0))})
    values.sort(key=lambda x: x["value"], reverse=True)
    return values[:topn]


def add_score_diff_direction_fields(row: pd.Series) -> tuple[float, str]:
    rule_score = safe_float(row.get("rule_risk_score", row.get("risk", 0.0)))
    gold_score = safe_float(row.get("gold_risk_score", 0.0))
    rule_minus_gold = rule_score - gold_score
    if rule_minus_gold > 0:
        direction = "overestimate"
    elif rule_minus_gold < 0:
        direction = "underestimate"
    else:
        direction = "equal"
    return round(rule_minus_gold, 4), direction


def score_col_for_risk_type(risk_type: Any, risk_type_to_score_col: Optional[dict[str, str]] = None) -> str:
    risk_type_str = str(risk_type or "").strip()
    if not risk_type_str:
        return ""
    mapping = risk_type_to_score_col or DEFAULT_RISK_TYPE_TO_SCORE_COL
    if risk_type_str in mapping:
        return mapping[risk_type_str]
    if risk_type_str.startswith("score_"):
        return risk_type_str
    return "score_" + risk_type_str


def primary_type_competition_for_row(
    row: pd.Series,
    risk_type_to_score_col: Optional[dict[str, str]] = None,
) -> dict:
    rule_type = str(row.get("rule_primary_risk_type", "") or "").strip()
    gold_type = str(row.get("gold_primary_risk_type", "") or "").strip()
    rule_score_col = score_col_for_risk_type(rule_type, risk_type_to_score_col)
    gold_score_col = score_col_for_risk_type(gold_type, risk_type_to_score_col)

    if not rule_score_col or rule_score_col not in row.index:
        return {
            "rule_primary_score": None,
            "gold_type_rule_score": None,
            "primary_vs_gold_gap": None,
            "runner_up_score_col": None,
            "runner_up_value": None,
            "primary_margin": None,
            "competition_note": "evidence_insufficient",
        }

    score_values = all_score_values_for_row(row, topn=len(SCORE_COLS))
    rule_primary_score = round_float(row.get(rule_score_col, 0.0))
    gold_type_rule_score = round_float(row.get(gold_score_col, 0.0)) if gold_score_col in row.index else None
    runner_up = next((item for item in score_values if item["score_col"] != rule_score_col), None)
    runner_up_score_col = runner_up["score_col"] if runner_up else None
    runner_up_value = runner_up["value"] if runner_up else None
    primary_vs_gold_gap = (
        round(rule_primary_score - gold_type_rule_score, 4)
        if gold_type_rule_score is not None
        else None
    )
    primary_margin = (
        round(rule_primary_score - runner_up_value, 4)
        if runner_up_value is not None
        else None
    )

    if gold_type_rule_score is None:
        competition_note = "gold_type_score_missing"
    elif primary_margin is not None and primary_margin <= 0.08:
        competition_note = "close_competition"
    elif primary_vs_gold_gap is not None and primary_vs_gold_gap <= 0.08:
        competition_note = "rule primary only slightly higher than gold type"
    else:
        competition_note = "rule primary clearly higher than alternatives"

    return {
        "rule_primary_score": rule_primary_score,
        "gold_type_rule_score": gold_type_rule_score,
        "primary_vs_gold_gap": primary_vs_gold_gap,
        "runner_up_score_col": runner_up_score_col,
        "runner_up_value": runner_up_value,
        "primary_margin": primary_margin,
        "competition_note": competition_note,
    }


def ast_literal_value(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = ast_literal_value(node.left)
            right = ast_literal_value(node.right)
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            if isinstance(left, tuple) and isinstance(right, tuple):
                return left + right
    return None


def collect_names(node: ast.AST) -> list[str]:
    names = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)})
    return names


def return_values_from_node(node: ast.AST) -> list[float]:
    values = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        value = ast_literal_value(child.value)
        if isinstance(value, (int, float)):
            values.append(round_float(value))
    return values


def clip_source(text: str, limit: int = 220) -> str:
    return clip_text(text, limit=limit)


def is_negative_list_name(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("NEG") or "_NEG" in upper or "EXCLUDE" in upper or "FALSE" in upper


def is_keyword_list(value: Any) -> bool:
    return isinstance(value, (list, tuple, set)) and all(isinstance(item, str) for item in value)


def extract_source_context(source_script: str | Path | None) -> dict:
    if not source_script:
        return {}

    source_path = Path(source_script).resolve()
    if not source_path.exists():
        return {
            "source_script": str(source_path),
            "source_available": False,
            "error": "source_script_not_found",
        }

    source_code = source_path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        return {
            "source_script": str(source_path),
            "source_available": False,
            "error": f"syntax_error: {exc}",
        }

    constants: dict[str, Any] = {}
    keyword_lists: dict[str, list[str]] = {}
    threshold_values: dict[str, float] = {}
    risk_name_map = DEFAULT_RISK_NAME_MAP.copy()

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = ast_literal_value(node.value)
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            constants[target.id] = value
            if is_keyword_list(value):
                keyword_lists[target.id] = [str(item) for item in value]
            elif isinstance(value, (int, float)) and (
                "THRESHOLD" in target.id.upper() or target.id.upper().endswith("_MIN")
            ):
                threshold_values[target.id] = round_float(value)
            elif target.id == "RISK_NAME_MAP" and isinstance(value, dict):
                parsed_map = {str(k): str(v) for k, v in value.items() if str(k).startswith("score_")}
                if parsed_map:
                    risk_name_map.update(parsed_map)

    scorer_sources: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("score_"):
            continue
        scorer_source = ast.get_source_segment(source_code, node) or ""
        referenced_names = collect_names(node)
        referenced_keyword_lists = [name for name in referenced_names if name in keyword_lists]
        early_return_guards = []

        for child in ast.walk(node):
            if not isinstance(child, ast.If):
                continue
            return_values = return_values_from_node(child)
            if not return_values or min(return_values) > 0.05:
                continue
            test_source = ast.get_source_segment(source_code, child.test) or ""
            guard_lists = [name for name in collect_names(child.test) if name in keyword_lists]
            early_return_guards.append(
                {
                    "condition": clip_source(test_source, limit=260),
                    "keyword_lists": guard_lists[:8],
                    "return_values": return_values[:4],
                }
            )

        scorer_sources[node.name] = {
            "referenced_keyword_lists": referenced_keyword_lists,
            "negative_keyword_lists": [name for name in referenced_keyword_lists if is_negative_list_name(name)],
            "positive_keyword_lists": [name for name in referenced_keyword_lists if not is_negative_list_name(name)],
            "early_return_guards": early_return_guards[:12],
            "function_preview": clip_source(scorer_source, limit=900),
        }

    risk_type_to_score_col = {v: k for k, v in risk_name_map.items()}
    return {
        "source_script": str(source_path),
        "source_available": True,
        "thresholds": threshold_values,
        "risk_name_map": risk_name_map,
        "risk_type_to_score_col": risk_type_to_score_col,
        "keyword_lists": keyword_lists,
        "scorers": scorer_sources,
        "scorer_summaries": {
            score_col: {
                "positive_keyword_lists": info.get("positive_keyword_lists", [])[:12],
                "negative_keyword_lists": info.get("negative_keyword_lists", [])[:12],
                "early_return_guards": info.get("early_return_guards", [])[:8],
            }
            for score_col, info in scorer_sources.items()
        },
    }


def terms_matching_text(terms: list[str], text: str, limit: int = 8) -> list[str]:
    text_norm = str(text or "").lower()
    hits = []
    for term in terms:
        term_text = str(term or "").strip()
        if not term_text:
            continue
        if term_text.lower() in text_norm:
            hits.append(term_text)
        if len(hits) >= limit:
            break
    return hits


def matched_keyword_lists_for_scorer(
    text: str,
    score_col: str,
    source_context: dict,
    negative: bool,
    max_lists: int = 8,
) -> list[dict]:
    scorer_info = (source_context.get("scorers", {}) or {}).get(score_col, {})
    list_names = scorer_info.get("negative_keyword_lists" if negative else "positive_keyword_lists", []) or []
    keyword_lists = source_context.get("keyword_lists", {}) or {}
    matches = []
    for list_name in list_names:
        matched_terms = terms_matching_text(keyword_lists.get(list_name, []), text, limit=8)
        if not matched_terms:
            continue
        matches.append({"list_name": list_name, "matched_terms": matched_terms})
        if len(matches) >= max_lists:
            break
    return matches


def missing_required_lists_for_guard(guard_condition: str, text: str, source_context: dict) -> list[dict]:
    keyword_lists = source_context.get("keyword_lists", {}) or {}
    missing = []
    for list_name in re.findall(r"not\s+has_any\(\s*text\s*,\s*([A-Z_][A-Z0-9_]*)", guard_condition):
        terms = keyword_lists.get(list_name, [])
        if not terms:
            continue
        matched_terms = terms_matching_text(terms, text, limit=6)
        if matched_terms:
            continue
        missing.append({"list_name": list_name, "example_terms": terms[:8]})
    return missing[:4]


def matched_early_return_guards(text: str, score_col: str, source_context: dict) -> list[dict]:
    scorer_info = (source_context.get("scorers", {}) or {}).get(score_col, {})
    keyword_lists = source_context.get("keyword_lists", {}) or {}
    matched_guards = []
    for guard in scorer_info.get("early_return_guards", []) or []:
        guard_matches = []
        for list_name in guard.get("keyword_lists", []) or []:
            matched_terms = terms_matching_text(keyword_lists.get(list_name, []), text, limit=6)
            if matched_terms:
                guard_matches.append({"list_name": list_name, "matched_terms": matched_terms})
        missing_required = missing_required_lists_for_guard(guard.get("condition", ""), text, source_context)
        if not guard_matches and not missing_required:
            continue
        matched_guards.append(
            {
                "condition": guard.get("condition", ""),
                "return_values": guard.get("return_values", []),
                "matched_keyword_lists": guard_matches,
                "missing_required_keyword_lists": missing_required,
            }
        )
        if len(matched_guards) >= 5:
            break
    return matched_guards


def score_band(value: float, primary_min: float, type_threshold: float) -> str:
    if value <= 0:
        return "zero"
    if value < primary_min:
        return "below_primary_min"
    if value < type_threshold:
        return "below_type_threshold"
    return "triggered"


def trace_target_score_cols(
    row: pd.Series,
    category: str,
    source_context: dict,
    max_scorers: int = 4,
) -> list[tuple[str, str]]:
    risk_map = source_context.get("risk_type_to_score_col") or DEFAULT_RISK_TYPE_TO_SCORE_COL
    targets: list[tuple[str, str]] = []
    gold_col = score_col_for_risk_type(row.get("gold_primary_risk_type", ""), risk_map)
    rule_col = score_col_for_risk_type(row.get("rule_primary_risk_type", ""), risk_map)
    _, direction = add_score_diff_direction_fields(row)

    if category in {"false_negative", "type_mismatch"} and gold_col in SCORE_COLS:
        targets.append((gold_col, "gold_primary"))
    if category in {"false_positive", "type_mismatch"} and rule_col in SCORE_COLS:
        targets.append((rule_col, "rule_primary"))
    if category == "score_diff":
        if direction == "underestimate" and gold_col in SCORE_COLS:
            targets.append((gold_col, "underestimated_gold_primary"))
        elif direction == "overestimate" and rule_col in SCORE_COLS:
            targets.append((rule_col, "overestimated_rule_primary"))

    for item in all_score_values_for_row(row, topn=4):
        score_col = item.get("score_col", "")
        if score_col in SCORE_COLS:
            targets.append((score_col, "top_rule_score"))

    deduped = []
    seen = set()
    for score_col, role in targets:
        if score_col in seen:
            continue
        seen.add(score_col)
        deduped.append((score_col, role))
        if len(deduped) >= max_scorers:
            break
    return deduped


def scorer_trace_for_row(row: pd.Series, category: str, source_context: dict) -> list[dict]:
    if not source_context or not source_context.get("source_available"):
        return []

    thresholds = source_context.get("thresholds", {}) or {}
    primary_min = safe_float(thresholds.get("PRIMARY_TYPE_MIN", 0.08), 0.08)
    type_threshold = safe_float(thresholds.get("TYPE_THRESHOLD", 0.30), 0.30)
    text = f"{row.get('标题', '')} {row.get('内容', '')}"
    traces = []

    for score_col, role in trace_target_score_cols(row, category, source_context):
        value = round_float(row.get(score_col, 0.0))
        traces.append(
            {
                "score_col": score_col,
                "role": role,
                "value": value,
                "score_band": score_band(value, primary_min, type_threshold),
                "distance_to_primary_min": round_float(max(primary_min - value, 0.0)),
                "distance_to_type_threshold": round_float(max(type_threshold - value, 0.0)),
                "matched_positive_keyword_lists": matched_keyword_lists_for_scorer(
                    text,
                    score_col,
                    source_context,
                    negative=False,
                ),
                "matched_negative_keyword_lists": matched_keyword_lists_for_scorer(
                    text,
                    score_col,
                    source_context,
                    negative=True,
                ),
                "matched_or_possible_early_return_guards": matched_early_return_guards(
                    text,
                    score_col,
                    source_context,
                ),
                "trace_note": "heuristic_static_trace_not_exact_runtime_branch",
            }
        )

    return traces


def score_sort_value(df: pd.DataFrame) -> pd.Series:
    if "score_diff" in df.columns:
        return df["score_diff"].apply(lambda x: abs(safe_float(x)))
    if "gold_risk_score" in df.columns and "rule_risk_score" in df.columns:
        return (df["gold_risk_score"].apply(safe_float) - df["rule_risk_score"].apply(safe_float)).abs()
    return pd.Series(range(len(df), 0, -1), index=df.index)


def choose_group_col(category: str, df: pd.DataFrame) -> Optional[str]:
    candidates = {
        "false_positive": ["rule_primary_risk_type", "rule_label", "rule_types"],
        "false_negative": ["gold_primary_risk_type", "gold_risk_label", "gold_risk_types"],
        "type_mismatch": ["gold_primary_risk_type", "rule_primary_risk_type"],
        "score_diff": ["rule_primary_risk_type", "gold_primary_risk_type"],
    }
    for col in candidates.get(category, []):
        if col in df.columns:
            return col
    return None


def score_diff_direction_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "overestimate_count": 0,
            "underestimate_count": 0,
            "equal_count": 0,
            "mean_rule_minus_gold": 0.0,
            "mean_abs_score_diff": 0.0,
            "p50_abs_score_diff": 0.0,
            "p90_abs_score_diff": 0.0,
            "overestimate_rule_primary_type_top": [],
            "underestimate_gold_primary_type_top": [],
            "overestimate_trigger_scores_top": [],
            "underestimate_trigger_scores_top": [],
        }

    temp = df.copy()
    direction_values = temp.apply(add_score_diff_direction_fields, axis=1)
    temp["__rule_minus_gold__"] = [item[0] for item in direction_values]
    temp["__score_diff_direction__"] = [item[1] for item in direction_values]
    temp["__abs_score_diff__"] = temp["__rule_minus_gold__"].abs()

    over_df = temp[temp["__score_diff_direction__"] == "overestimate"]
    under_df = temp[temp["__score_diff_direction__"] == "underestimate"]
    equal_df = temp[temp["__score_diff_direction__"] == "equal"]

    return {
        "overestimate_count": int(len(over_df)),
        "underestimate_count": int(len(under_df)),
        "equal_count": int(len(equal_df)),
        "mean_rule_minus_gold": round_float(temp["__rule_minus_gold__"].mean()),
        "mean_abs_score_diff": round_float(temp["__abs_score_diff__"].mean()),
        "p50_abs_score_diff": round_float(temp["__abs_score_diff__"].quantile(0.50)),
        "p90_abs_score_diff": round_float(temp["__abs_score_diff__"].quantile(0.90)),
        "overestimate_rule_primary_type_top": value_counts_top(
            normalize_str_col(over_df, "rule_primary_risk_type"),
            topn=15,
        ),
        "underestimate_gold_primary_type_top": value_counts_top(
            normalize_str_col(under_df, "gold_primary_risk_type"),
            topn=15,
        ),
        "overestimate_trigger_scores_top": score_trigger_summary(over_df, threshold=0.30),
        "underestimate_trigger_scores_top": score_trigger_summary(under_df, threshold=0.30),
    }


def score_diff_cases_summary(df: pd.DataFrame, direction: str) -> dict:
    if df.empty:
        return {
            "count": 0,
            "rule_primary_type_top": [],
            "gold_primary_type_top": [],
            "trigger_scores_top": [],
        }

    temp = df.copy()
    direction_values = temp.apply(add_score_diff_direction_fields, axis=1)
    temp["__score_diff_direction__"] = [item[1] for item in direction_values]
    sub = temp[temp["__score_diff_direction__"] == direction]
    return {
        "count": int(len(sub)),
        "rule_primary_type_top": value_counts_top(normalize_str_col(sub, "rule_primary_risk_type"), topn=10),
        "gold_primary_type_top": value_counts_top(normalize_str_col(sub, "gold_primary_risk_type"), topn=10),
        "trigger_scores_top": score_trigger_summary(sub, threshold=0.30),
    }


def numeric_col_series(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series([default] * len(df), index=df.index)
    return df[col].apply(safe_float)


def mismatch_pair_score_summary(
    tm_df: pd.DataFrame,
    topn: int = 20,
    risk_type_to_score_col: Optional[dict[str, str]] = None,
) -> list[dict]:
    if (
        tm_df.empty
        or "gold_primary_risk_type" not in tm_df.columns
        or "rule_primary_risk_type" not in tm_df.columns
    ):
        return []

    temp = tm_df.copy()
    temp["gold_primary_risk_type"] = normalize_str_col(temp, "gold_primary_risk_type")
    temp["rule_primary_risk_type"] = normalize_str_col(temp, "rule_primary_risk_type")
    temp["__rule_risk_score__"] = (
        numeric_col_series(temp, "rule_risk_score")
        if "rule_risk_score" in temp.columns
        else numeric_col_series(temp, "risk")
    )
    temp["__gold_risk_score__"] = numeric_col_series(temp, "gold_risk_score")
    temp["__rule_minus_gold__"] = temp["__rule_risk_score__"] - temp["__gold_risk_score__"]
    temp["__abs_score_diff__"] = temp["__rule_minus_gold__"].abs()

    competitions = temp.apply(
        lambda row: primary_type_competition_for_row(row, risk_type_to_score_col),
        axis=1,
    )
    temp["__rule_primary_score__"] = [
        item.get("rule_primary_score") if isinstance(item, dict) else None for item in competitions
    ]
    temp["__gold_type_rule_score__"] = [
        item.get("gold_type_rule_score") if isinstance(item, dict) else None for item in competitions
    ]
    temp["__primary_vs_gold_gap__"] = [
        item.get("primary_vs_gold_gap") if isinstance(item, dict) else None for item in competitions
    ]

    rows = []
    grouped = temp.groupby(["gold_primary_risk_type", "rule_primary_risk_type"], dropna=False)
    for (gold_type, rule_type), sub in grouped:
        rows.append(
            {
                "gold_primary_risk_type": str(gold_type),
                "rule_primary_risk_type": str(rule_type),
                "count": int(len(sub)),
                "avg_rule_risk_score": round_float(sub["__rule_risk_score__"].mean()),
                "avg_gold_risk_score": round_float(sub["__gold_risk_score__"].mean()),
                "avg_abs_score_diff": round_float(sub["__abs_score_diff__"].mean()),
                "avg_rule_minus_gold": round_float(sub["__rule_minus_gold__"].mean()),
                "avg_rule_primary_score": round_float(sub["__rule_primary_score__"].mean()),
                "avg_gold_type_rule_score": round_float(sub["__gold_type_rule_score__"].mean()),
                "avg_primary_vs_gold_gap": round_float(sub["__primary_vs_gold_gap__"].mean()),
            }
        )

    rows.sort(key=lambda x: x["count"], reverse=True)
    return rows[:topn]


def build_representative_cases(
    df: pd.DataFrame,
    category: str,
    max_rows: int = DEFAULT_SAMPLE_ROWS_PER_BUCKET,
    text_limit: int = DEFAULT_TEXT_LIMIT,
    source_context: Optional[dict] = None,
) -> list[dict]:
    if df.empty:
        return []

    temp = df.copy()
    temp["__sort__"] = score_sort_value(temp)
    temp = temp.sort_values("__sort__", ascending=False)

    group_col = choose_group_col(category, temp)
    selected_indices: list[Any] = []

    if group_col:
        grp = (
            normalize_str_col(temp, group_col)
            .replace("", "<EMPTY>")
            .value_counts(dropna=False)
            .head(4)
        )
        per_group = max(2, max_rows // max(1, len(grp)))
        for group_name in grp.index.tolist():
            group_mask = normalize_str_col(temp, group_col).replace("", "<EMPTY>") == group_name
            sub = temp[group_mask].head(per_group)
            selected_indices.extend(sub.index.tolist())

    # 不足时补齐高分差样本
    if len(selected_indices) < max_rows:
        for idx in temp.index.tolist():
            if idx not in selected_indices:
                selected_indices.append(idx)
            if len(selected_indices) >= max_rows:
                break

    selected = temp.loc[selected_indices].head(max_rows)
    source_context = source_context or {}
    risk_type_to_score_col = source_context.get("risk_type_to_score_col") or DEFAULT_RISK_TYPE_TO_SCORE_COL

    cases = []
    for _, row in selected.iterrows():
        rule_minus_gold, score_diff_direction = add_score_diff_direction_fields(row)
        cases.append(
            {
                "news_id": str(row.get("新闻id", "")),
                "title": clip_text(row.get("标题", ""), min(120, text_limit)),
                "content": clip_text(row.get("内容", ""), text_limit),
                "gold_risk_score": round_float(row.get("gold_risk_score", 0.0)),
                "rule_risk_score": round_float(row.get("rule_risk_score", row.get("risk", 0.0))),
                "score_diff": round_float(row.get("score_diff", abs(rule_minus_gold))),
                "rule_minus_gold": rule_minus_gold,
                "score_diff_direction": score_diff_direction,
                "gold_risk_label": str(row.get("gold_risk_label", "")),
                "rule_risk_label": str(row.get("rule_risk_label", row.get("rule_label", ""))),
                "gold_primary_risk_type": str(row.get("gold_primary_risk_type", "")),
                "rule_primary_risk_type": str(row.get("rule_primary_risk_type", "")),
                "gold_risk_types": clip_text(row.get("gold_risk_types", ""), 160),
                "rule_risk_types": clip_text(row.get("rule_risk_types", ""), 160),
                "top_triggered_scores": top_triggered_scores_for_row(row, threshold=0.30, topn=4),
                "all_score_values": all_score_values_for_row(row, topn=8),
                "primary_type_competition": primary_type_competition_for_row(row, risk_type_to_score_col),
                "scorer_trace": scorer_trace_for_row(row, category, source_context),
            }
        )
    return cases


def infer_findings_non_llm(
    false_positive_df: pd.DataFrame,
    false_negative_df: pd.DataFrame,
    type_mismatch_df: pd.DataFrame,
    score_diff_top_df: pd.DataFrame,
) -> list[dict]:
    findings = []

    fp_rule_types = value_counts_top(normalize_str_col(false_positive_df, "rule_primary_risk_type"), topn=5)
    fn_gold_types = value_counts_top(normalize_str_col(false_negative_df, "gold_primary_risk_type"), topn=5)
    tm_pairs = pair_mismatch_top(type_mismatch_df, "gold_primary_risk_type", "rule_primary_risk_type", topn=10)

    fp_triggers = score_trigger_summary(false_positive_df, threshold=0.30)
    fn_triggers = score_trigger_summary(false_negative_df, threshold=0.30)
    sd_triggers = score_trigger_summary(score_diff_top_df, threshold=0.30)

    if fp_rule_types:
        findings.append(
            {
                "name": "top_false_positive_rule_types",
                "category": "false_positive",
                "description": "误报样本中最常见的规则主类别",
                "evidence": fp_rule_types[:5],
                "suggestion": "优先检查这些类别对应的 scorer 是否触发条件过宽。",
                "priority": "high",
            }
        )

    if fn_gold_types:
        findings.append(
            {
                "name": "top_false_negative_gold_types",
                "category": "false_negative",
                "description": "漏报样本中最常见的金标主类别",
                "evidence": fn_gold_types[:5],
                "suggestion": "优先补充这些类别的关键词、弱触发分支或语义模式。",
                "priority": "high",
            }
        )

    if tm_pairs:
        findings.append(
            {
                "name": "top_type_mismatch_pairs",
                "category": "type_mismatch",
                "description": "主类别最常见的错配方向",
                "evidence": tm_pairs[:10],
                "suggestion": "重点检查这些类别之间的边界定义和主类别选取逻辑。",
                "priority": "high",
            }
        )

    if fp_triggers:
        findings.append(
            {
                "name": "top_false_positive_trigger_scores",
                "category": "false_positive",
                "description": "误报样本中最常被触发的风险分数列",
                "evidence": fp_triggers[:6],
                "suggestion": "这些 scorer 很可能过于激进，建议优先收紧触发条件。",
                "priority": "high",
            }
        )

    if fn_triggers:
        findings.append(
            {
                "name": "false_negative_trigger_scores",
                "category": "false_negative",
                "description": "漏报样本中的风险分数列触发情况",
                "evidence": fn_triggers[:6],
                "suggestion": "若关键类别长期不触发，说明对应规则存在盲区。",
                "priority": "medium",
            }
        )

    if sd_triggers:
        findings.append(
            {
                "name": "top_score_diff_trigger_scores",
                "category": "score_diff",
                "description": "分数偏差最大的样本中，最常被触发的风险分数列",
                "evidence": sd_triggers[:6],
                "suggestion": "这些 scorer 的强度映射或阈值可能需要重新校准。",
                "priority": "medium",
            }
        )

    return findings


def load_version_metrics(path: str | Path | None) -> list[dict]:
    if not path:
        return []
    metrics_path = Path(path).resolve()
    if not metrics_path.exists():
        raise FileNotFoundError(f"version metrics json not found: {metrics_path}")

    raw = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        metrics = raw
    elif isinstance(raw, dict) and isinstance(raw.get("metrics"), list):
        metrics = raw["metrics"]
    else:
        return []

    return [item for item in metrics if isinstance(item, dict)]


def build_regression_summary(metrics: list[dict]) -> dict:
    if len(metrics) < 2:
        return {
            "improved_metrics": [],
            "worsened_metrics": [],
            "notes": ["版本指标不足，无法比较 regression。"],
        }

    prev = metrics[-2]
    curr = metrics[-1]
    improved_metrics = []
    worsened_metrics = []
    notes = []

    metric_keys = [
        key
        for key in curr.keys()
        if key != "version" and isinstance(curr.get(key), (int, float))
    ]

    for key in metric_keys:
        if key not in prev or not isinstance(prev.get(key), (int, float)):
            continue
        prev_val = safe_float(prev.get(key))
        curr_val = safe_float(curr.get(key))
        delta = round(curr_val - prev_val, 4)
        item = {
            "metric": key,
            "previous": round_float(prev_val),
            "current": round_float(curr_val),
            "delta": delta,
            "from_version": str(prev.get("version", "")),
            "to_version": str(curr.get("version", "")),
        }
        if delta < 0:
            improved_metrics.append(item)
        elif delta > 0:
            worsened_metrics.append(item)

    fp_improved = any(item["metric"] == "false_positive_rows" for item in improved_metrics)
    regression_keys = {
        "false_negative_rows",
        "type_mismatch_rows",
        "score_diff_top_rows",
        "score_diff_mean",
        "score_diff_rmse",
        "score_diff_p90",
    }
    other_worsened = [item for item in worsened_metrics if item["metric"] in regression_keys]
    if fp_improved and other_worsened:
        worsened_names = ", ".join(item["metric"] for item in other_worsened)
        notes.append(
            "false_positive 下降但 "
            f"{worsened_names} 上升，可能存在单目标优化 false_positive 导致其它指标恶化。"
        )

    if not notes:
        notes.append("未发现 false_positive 改善同时其它核心指标恶化的明显 tradeoff。")

    return {
        "improved_metrics": improved_metrics,
        "worsened_metrics": worsened_metrics,
        "notes": notes,
    }


def build_analysis_payload(
    fp_df: pd.DataFrame,
    fn_df: pd.DataFrame,
    tm_df: pd.DataFrame,
    sd_df: pd.DataFrame,
    sample_rows: int = DEFAULT_SAMPLE_ROWS_PER_BUCKET,
    text_limit: int = DEFAULT_TEXT_LIMIT,
    version_metrics: Optional[list[dict]] = None,
    source_context: Optional[dict] = None,
) -> dict:
    source_context = source_context or {}
    risk_type_to_score_col = source_context.get("risk_type_to_score_col") or DEFAULT_RISK_TYPE_TO_SCORE_COL
    payload = {
        "summary": {
            "false_positive_rows": int(len(fp_df)),
            "false_negative_rows": int(len(fn_df)),
            "type_mismatch_rows": int(len(tm_df)),
            "score_diff_top_rows": int(len(sd_df)),
        },
        "statistics": {
            "false_positive_analysis": {
                "rule_primary_type_top": value_counts_top(normalize_str_col(fp_df, "rule_primary_risk_type"), topn=15),
                "gold_primary_type_top": value_counts_top(normalize_str_col(fp_df, "gold_primary_risk_type"), topn=15),
                "trigger_scores_top": score_trigger_summary(fp_df, threshold=0.30),
            },
            "false_negative_analysis": {
                "gold_primary_type_top": value_counts_top(normalize_str_col(fn_df, "gold_primary_risk_type"), topn=15),
                "rule_primary_type_top": value_counts_top(normalize_str_col(fn_df, "rule_primary_risk_type"), topn=15),
                "trigger_scores_top": score_trigger_summary(fn_df, threshold=0.30),
            },
            "type_mismatch_analysis": {
                "gold_primary_type_top": value_counts_top(normalize_str_col(tm_df, "gold_primary_risk_type"), topn=15),
                "rule_primary_type_top": value_counts_top(normalize_str_col(tm_df, "rule_primary_risk_type"), topn=15),
                "top_mismatch_pairs": pair_mismatch_top(
                    tm_df,
                    "gold_primary_risk_type",
                    "rule_primary_risk_type",
                    topn=20,
                ),
                "mismatch_pair_score_summary": mismatch_pair_score_summary(
                    tm_df,
                    topn=20,
                    risk_type_to_score_col=risk_type_to_score_col,
                ),
            },
            "score_diff_analysis": {
                "trigger_scores_top": score_trigger_summary(sd_df, threshold=0.30),
                "rule_primary_type_top": value_counts_top(normalize_str_col(sd_df, "rule_primary_risk_type"), topn=15),
                "gold_primary_type_top": value_counts_top(normalize_str_col(sd_df, "gold_primary_risk_type"), topn=15),
                "direction_summary": score_diff_direction_summary(sd_df),
                "overestimate_cases_summary": score_diff_cases_summary(sd_df, "overestimate"),
                "underestimate_cases_summary": score_diff_cases_summary(sd_df, "underestimate"),
            },
        },
        "findings_non_llm": infer_findings_non_llm(fp_df, fn_df, tm_df, sd_df),
        "sample_cases": {
            "false_positive": build_representative_cases(
                fp_df,
                "false_positive",
                max_rows=sample_rows,
                text_limit=text_limit,
                source_context=source_context,
            ),
            "false_negative": build_representative_cases(
                fn_df,
                "false_negative",
                max_rows=sample_rows,
                text_limit=text_limit,
                source_context=source_context,
            ),
            "type_mismatch": build_representative_cases(
                tm_df,
                "type_mismatch",
                max_rows=sample_rows,
                text_limit=text_limit,
                source_context=source_context,
            ),
            "score_diff": build_representative_cases(
                sd_df,
                "score_diff",
                max_rows=sample_rows,
                text_limit=text_limit,
                source_context=source_context,
            ),
        },
    }
    if source_context:
        payload["source_code_context"] = {
            "source_script": source_context.get("source_script", ""),
            "source_available": bool(source_context.get("source_available")),
            "thresholds": source_context.get("thresholds", {}),
            "risk_name_map": source_context.get("risk_name_map", {}),
            "scorer_summaries": source_context.get("scorer_summaries", {}),
            "trace_note": "scorer_trace is heuristic static matching against scorer keyword lists and early-return guards; it is not an exact runtime branch trace.",
        }
    if version_metrics:
        payload["version_history"] = {
            "metrics": version_metrics,
            "regression_summary": build_regression_summary(version_metrics),
        }
    return payload


def build_full_analysis(
    payload: dict,
    llm_analysis: dict,
    model: str,
    sample_rows: int,
    text_limit: int,
) -> dict:
    full_analysis = {
        "summary": payload["summary"],
        "statistics": payload["statistics"],
        "findings_non_llm": payload["findings_non_llm"],
        "llm_analysis": llm_analysis,
        "meta": {
            "model": model,
            "sample_rows_per_bucket": sample_rows,
            "text_limit": text_limit,
        },
    }
    if "version_history" in payload:
        full_analysis["version_history"] = payload["version_history"]
    if "source_code_context" in payload:
        full_analysis["source_code_context"] = payload["source_code_context"]
    return full_analysis


def call_llm_for_error_patterns(
    payload: dict,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_retries: int = 2,
) -> dict:
    return call_chat_json(
        messages=build_analyzer_messages(payload),
        model=model,
        default_base_url=DEFAULT_BASE_URL,
        temperature=temperature,
        max_retries=max_retries,
        error_prefix="LLM 分析失败",
    )


def render_markdown_report(full_analysis: dict) -> str:
    llm = full_analysis.get("llm_analysis", {})
    lines: list[str] = []
    lines.append("# 风险标注错误模式分析报告（LLM版）")
    lines.append("")

    summary = full_analysis.get("summary", {})
    lines.append("## 样本规模")
    lines.append("")
    lines.append(f"- False Positive: {summary.get('false_positive_rows', 0)}")
    lines.append(f"- False Negative: {summary.get('false_negative_rows', 0)}")
    lines.append(f"- Type Mismatch: {summary.get('type_mismatch_rows', 0)}")
    lines.append(f"- Score Diff Top: {summary.get('score_diff_top_rows', 0)}")
    lines.append("")

    version_history = full_analysis.get("version_history", {})
    regression = version_history.get("regression_summary", {}) if isinstance(version_history, dict) else {}
    if version_history:
        lines.append("## 版本变化摘要")
        lines.append("")
        for item in regression.get("improved_metrics", []) or []:
            lines.append(
                f"- 改善: {item.get('metric', '')} "
                f"{item.get('previous', '')} -> {item.get('current', '')} "
                f"(delta {item.get('delta', '')})"
            )
        for item in regression.get("worsened_metrics", []) or []:
            lines.append(
                f"- 恶化: {item.get('metric', '')} "
                f"{item.get('previous', '')} -> {item.get('current', '')} "
                f"(delta {item.get('delta', '')})"
            )
        for note in regression.get("notes", []) or []:
            lines.append(f"- 提醒: {note}")
        lines.append("")

    lines.append("## LLM 总结")
    lines.append("")
    for item in llm.get("executive_summary", []):
        lines.append(f"- {item}")
    lines.append("")

    tradeoff = llm.get("metric_tradeoff_diagnosis", {}) or {}
    if tradeoff:
        lines.append("## 指标权衡诊断")
        lines.append("")
        if tradeoff.get("main_improved_metric"):
            lines.append(f"- 主要改善指标: {tradeoff.get('main_improved_metric')}")
        worsened = ", ".join(tradeoff.get("worsened_metrics", []) or [])
        if worsened:
            lines.append(f"- 恶化指标: {worsened}")
        if tradeoff.get("likely_reason"):
            lines.append(f"- 可能原因: {tradeoff.get('likely_reason')}")
        if tradeoff.get("optimization_warning"):
            lines.append(f"- 优化提醒: {tradeoff.get('optimization_warning')}")
        lines.append("")

    lines.append("## 主要错误模式")
    lines.append("")
    for i, p in enumerate(llm.get("patterns", []), start=1):
        lines.append(f"### {i}. {p.get('pattern_name', '未命名模式')} [{p.get('category', '')}]")
        lines.append("")
        scorers = ", ".join(p.get("affected_scorers", []) or [])
        if scorers:
            lines.append(f"- 影响 scorer: {scorers}")
        if p.get("likely_root_cause"):
            lines.append(f"- 根因: {p.get('likely_root_cause')}")
        if p.get("score_direction"):
            lines.append(f"- 分数方向: {p.get('score_direction')}")
        if "risky_patch" in p:
            lines.append(f"- risky_patch: {p.get('risky_patch')}")
        if p.get("priority"):
            lines.append(f"- 优先级: {p.get('priority')}")
        evidence = p.get("evidence", []) or []
        if evidence:
            lines.append("- 证据:")
            for x in evidence:
                lines.append(f"  - {x}")
        suggestions = p.get("patch_suggestions", []) or []
        if suggestions:
            lines.append("- patch 建议:")
            for x in suggestions:
                lines.append(f"  - {x}")
        side_effects = p.get("possible_side_effects", []) or []
        if side_effects:
            lines.append("- 可能副作用:")
            for x in side_effects:
                lines.append(f"  - {x}")
        lines.append("")

    lines.append("## Scorer 级诊断")
    lines.append("")
    for item in llm.get("scorer_diagnosis", []):
        lines.append(f"- **{item.get('score_col', '')}**: {item.get('problem_type', '')}")
        if item.get("why"):
            lines.append(f"  - 原因: {item.get('why')}")
        if item.get("recommendation"):
            lines.append(f"  - 建议: {item.get('recommendation')}")
        if item.get("risk_of_patch"):
            lines.append(f"  - patch 风险: {item.get('risk_of_patch')}")
    lines.append("")

    trace_diag = llm.get("scorer_trace_diagnosis", []) or []
    if trace_diag:
        lines.append("## Scorer Trace 诊断")
        lines.append("")
        for item in trace_diag:
            lines.append(f"- **{item.get('score_col', '')}**: {item.get('trace_signal', '')}")
            if item.get("code_area"):
                lines.append(f"  - 代码区域: {item.get('code_area')}")
            evidence = item.get("evidence", []) or []
            if evidence:
                lines.append("  - trace 证据:")
                for x in evidence:
                    lines.append(f"    - {x}")
            if item.get("recommendation"):
                lines.append(f"  - 建议: {item.get('recommendation')}")
            if item.get("guardrail"):
                lines.append(f"  - guardrail: {item.get('guardrail')}")
        lines.append("")

    lines.append("## 主类别诊断")
    lines.append("")
    for item in llm.get("primary_type_diagnosis", []):
        lines.append(f"- **{item.get('mismatch_pair', '')}**: {item.get('likely_reason', '')}")
        if item.get("fix"):
            lines.append(f"  - 修复: {item.get('fix')}")
        if item.get("side_effect_guardrail"):
            lines.append(f"  - guardrail: {item.get('side_effect_guardrail')}")
    lines.append("")

    lines.append("## patch 顺序")
    lines.append("")
    for item in llm.get("patch_plan", []):
        step = item.get("step", "")
        target = item.get("target", "")
        action = item.get("action", "")
        expected_benefit = item.get("expected_benefit", "")
        lines.append(f"- Step {step}: {target}")
        if action:
            lines.append(f"  - 动作: {action}")
        if expected_benefit:
            lines.append(f"  - 收益: {expected_benefit}")
        if item.get("guardrail"):
            lines.append(f"  - guardrail: {item.get('guardrail')}")
        if item.get("validation"):
            lines.append(f"  - 验证: {item.get('validation')}")
    lines.append("")

    lines.append("## 暂不建议修改")
    lines.append("")
    for item in llm.get("do_not_patch", []):
        lines.append(f"- **{item.get('target', '')}**: {item.get('reason', '')}")
    lines.append("")

    if llm.get("confidence"):
        lines.append(f"**整体置信度**: {llm.get('confidence')}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def analyze_error_files_with_llm(
    false_positive_csv: str | Path,
    false_negative_csv: str | Path,
    type_mismatch_csv: str | Path,
    score_diff_top_csv: str | Path,
    analysis_json: str | Path,
    analysis_markdown: str | Path,
    llm_payload_json: str | Path,
    model: str = DEFAULT_MODEL,
    sample_rows: int = DEFAULT_SAMPLE_ROWS_PER_BUCKET,
    text_limit: int = DEFAULT_TEXT_LIMIT,
    show_progress: bool = True,
    version_metrics_json: str | Path | None = None,
    source_script: str | Path | None = None,
) -> AnalyzeLLMResult:
    false_positive_csv = Path(false_positive_csv).resolve()
    false_negative_csv = Path(false_negative_csv).resolve()
    type_mismatch_csv = Path(type_mismatch_csv).resolve()
    score_diff_top_csv = Path(score_diff_top_csv).resolve()
    analysis_json = Path(analysis_json).resolve()
    analysis_markdown = Path(analysis_markdown).resolve()
    llm_payload_json = Path(llm_payload_json).resolve()

    progress = StageProgress(total=7, enabled=show_progress, desc="Analyzer LLM")

    try:
        # 1) 检查输入文件
        for p in [false_positive_csv, false_negative_csv, type_mismatch_csv, score_diff_top_csv]:
            if not p.exists():
                progress.close()
                return AnalyzeLLMResult(
                    success=False,
                    false_positive_csv=str(false_positive_csv),
                    false_negative_csv=str(false_negative_csv),
                    type_mismatch_csv=str(type_mismatch_csv),
                    score_diff_top_csv=str(score_diff_top_csv),
                    analysis_json=str(analysis_json),
                    analysis_markdown=str(analysis_markdown),
                    llm_payload_json=str(llm_payload_json),
                    false_positive_rows=0,
                    false_negative_rows=0,
                    type_mismatch_rows=0,
                    score_diff_top_rows=0,
                    error_message=f"文件不存在: {p}",
                    model=model,
                )
        progress.update("输入文件检查完成")

        # 2) 读取 csv
        fp_df = read_csv_file(false_positive_csv)
        fn_df = read_csv_file(false_negative_csv)
        tm_df = read_csv_file(type_mismatch_csv)
        sd_df = read_csv_file(score_diff_top_csv)
        progress.update("错误样本读取完成")

        version_metrics = load_version_metrics(version_metrics_json) if version_metrics_json else []
        source_context = extract_source_context(source_script) if source_script else {}

        # 3) 构建 payload
        payload = build_analysis_payload(
            fp_df=fp_df,
            fn_df=fn_df,
            tm_df=tm_df,
            sd_df=sd_df,
            sample_rows=sample_rows,
            text_limit=text_limit,
            version_metrics=version_metrics,
            source_context=source_context,
        )
        progress.update("分析 payload 构建完成")

        # 4) 保存 payload
        write_json_file(llm_payload_json, payload)
        progress.update("payload 文件写入完成")

        # 5) 调 LLM
        llm_analysis = call_llm_for_error_patterns(payload=payload, model=model)
        progress.update("LLM 诊断完成")

        # 6) 写 analysis_json
        full_analysis = build_full_analysis(
            payload=payload,
            llm_analysis=llm_analysis,
            model=model,
            sample_rows=sample_rows,
            text_limit=text_limit,
        )

        write_json_file(analysis_json, full_analysis)
        progress.update("analysis_json 写入完成")

        # 7) 写 markdown
        ensure_parent_dir(analysis_markdown)
        analysis_md = render_markdown_report(full_analysis)
        analysis_markdown.write_text(analysis_md, encoding="utf-8")
        progress.update("analysis_markdown 写入完成")

        progress.close()

        return AnalyzeLLMResult(
            success=True,
            false_positive_csv=str(false_positive_csv),
            false_negative_csv=str(false_negative_csv),
            type_mismatch_csv=str(type_mismatch_csv),
            score_diff_top_csv=str(score_diff_top_csv),
            analysis_json=str(analysis_json),
            analysis_markdown=str(analysis_markdown),
            llm_payload_json=str(llm_payload_json),
            false_positive_rows=len(fp_df),
            false_negative_rows=len(fn_df),
            type_mismatch_rows=len(tm_df),
            score_diff_top_rows=len(sd_df),
            error_message="",
            model=model,
        )
    except Exception:
        progress.close()
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use LLM to summarize crypto risk labeling error patterns.")
    parser.add_argument("--false-positive-csv", type=str, default="")
    parser.add_argument("--false-negative-csv", type=str, default="")
    parser.add_argument("--type-mismatch-csv", type=str, default="")
    parser.add_argument("--score-diff-top-csv", type=str, default="")
    parser.add_argument("--analysis-json", type=str, default="")
    parser.add_argument("--analysis-markdown", type=str, default="")
    parser.add_argument("--llm-payload-json", type=str, default="")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--sample-rows", type=int, default=DEFAULT_SAMPLE_ROWS_PER_BUCKET)
    parser.add_argument("--text-limit", type=int, default=DEFAULT_TEXT_LIMIT)
    parser.add_argument("--version-metrics-json", type=str, default="")
    parser.add_argument("--source-script", type=str, default="")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    project_root = resolve_project_root()
    default_version_dir = project_root / "versions" / "v1"

    false_positive_csv = Path(args.false_positive_csv) if args.false_positive_csv else default_version_dir / "reports" / "errors" / "risk_labeler_v1_false_positive.csv"
    false_negative_csv = Path(args.false_negative_csv) if args.false_negative_csv else default_version_dir / "reports" / "errors" / "risk_labeler_v1_false_negative.csv"
    type_mismatch_csv = Path(args.type_mismatch_csv) if args.type_mismatch_csv else default_version_dir / "reports" / "errors" / "risk_labeler_v1_type_mismatch.csv"
    score_diff_top_csv = Path(args.score_diff_top_csv) if args.score_diff_top_csv else default_version_dir / "reports" / "errors" / "risk_labeler_v1_score_diff_top.csv"
    analysis_json = Path(args.analysis_json) if args.analysis_json else default_version_dir / "reports" / "analysis" / "risk_labeler_v1_analysis_llm.json"
    analysis_markdown = Path(args.analysis_markdown) if args.analysis_markdown else default_version_dir / "reports" / "analysis" / "risk_labeler_v1_analysis_llm.md"
    llm_payload_json = Path(args.llm_payload_json) if args.llm_payload_json else default_version_dir / "reports" / "analysis" / "risk_labeler_v1_analysis_llm_payload.json"

    result = analyze_error_files_with_llm(
        false_positive_csv=false_positive_csv,
        false_negative_csv=false_negative_csv,
        type_mismatch_csv=type_mismatch_csv,
        score_diff_top_csv=score_diff_top_csv,
        analysis_json=analysis_json,
        analysis_markdown=analysis_markdown,
        llm_payload_json=llm_payload_json,
        model=args.model,
        sample_rows=args.sample_rows,
        text_limit=args.text_limit,
        show_progress=True,
        version_metrics_json=args.version_metrics_json or None,
        source_script=args.source_script or None,
    )

    print("=" * 60)
    print("ANALYZE LLM RESULT")
    print("=" * 60)
    print(f"success                : {result.success}")
    print(f"model                  : {result.model}")
    print(f"false_positive_csv     : {result.false_positive_csv}")
    print(f"false_negative_csv     : {result.false_negative_csv}")
    print(f"type_mismatch_csv      : {result.type_mismatch_csv}")
    print(f"score_diff_top_csv     : {result.score_diff_top_csv}")
    print(f"llm_payload_json       : {result.llm_payload_json}")
    print(f"analysis_json          : {result.analysis_json}")
    print(f"analysis_markdown      : {result.analysis_markdown}")
    print(f"false_positive_rows    : {result.false_positive_rows}")
    print(f"false_negative_rows    : {result.false_negative_rows}")
    print(f"type_mismatch_rows     : {result.type_mismatch_rows}")
    print(f"score_diff_top_rows    : {result.score_diff_top_rows}")

    if result.error_message:
        print(f"error_message          : {result.error_message}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
