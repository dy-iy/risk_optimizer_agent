from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from tqdm import tqdm


class StageProgress:
    def __init__(self, total: int, enabled: bool = True, desc: str = "Analyzing"):
        self.enabled = enabled
        self.total = total
        self.current = 0
        self.desc = desc
        self.bar = None

        if self.enabled and tqdm is not None:
            self.bar = tqdm(total=total, desc=desc, ncols=100)
        elif self.enabled:
            print(f"[0/{total}] {desc}")

    def update(self, message: str) -> None:
        self.current += 1
        if self.bar is not None:
            self.bar.set_postfix_str(message)
            self.bar.update(1)
        elif self.enabled:
            print(f"[{self.current}/{self.total}] {message}")

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()
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


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_env_file(env_path: str | Path = ".env") -> None:
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_str_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return df[col].fillna("").astype(str).str.strip()


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def read_csv_safe(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(path)


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


def build_representative_cases(
    df: pd.DataFrame,
    category: str,
    max_rows: int = DEFAULT_SAMPLE_ROWS_PER_BUCKET,
    text_limit: int = DEFAULT_TEXT_LIMIT,
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

    cases = []
    for _, row in selected.iterrows():
        cases.append(
            {
                "news_id": str(row.get("新闻id", "")),
                "title": clip_text(row.get("标题", ""), min(120, text_limit)),
                "content": clip_text(row.get("内容", ""), text_limit),
                "gold_risk_score": safe_float(row.get("gold_risk_score", 0.0)),
                "rule_risk_score": safe_float(row.get("rule_risk_score", row.get("risk", 0.0))),
                "score_diff": round(safe_float(row.get("score_diff", 0.0)), 4),
                "gold_risk_label": str(row.get("gold_risk_label", "")),
                "rule_risk_label": str(row.get("rule_risk_label", row.get("rule_label", ""))),
                "gold_primary_risk_type": str(row.get("gold_primary_risk_type", "")),
                "rule_primary_risk_type": str(row.get("rule_primary_risk_type", "")),
                "gold_risk_types": clip_text(row.get("gold_risk_types", ""), 160),
                "rule_risk_types": clip_text(row.get("rule_risk_types", ""), 160),
                "top_triggered_scores": top_triggered_scores_for_row(row, threshold=0.30, topn=4),
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


def build_analysis_payload(
    fp_df: pd.DataFrame,
    fn_df: pd.DataFrame,
    tm_df: pd.DataFrame,
    sd_df: pd.DataFrame,
    sample_rows: int = DEFAULT_SAMPLE_ROWS_PER_BUCKET,
    text_limit: int = DEFAULT_TEXT_LIMIT,
) -> dict:
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
            },
            "score_diff_analysis": {
                "trigger_scores_top": score_trigger_summary(sd_df, threshold=0.30),
                "rule_primary_type_top": value_counts_top(normalize_str_col(sd_df, "rule_primary_risk_type"), topn=15),
                "gold_primary_type_top": value_counts_top(normalize_str_col(sd_df, "gold_primary_risk_type"), topn=15),
            },
        },
        "findings_non_llm": infer_findings_non_llm(fp_df, fn_df, tm_df, sd_df),
        "sample_cases": {
            "false_positive": build_representative_cases(fp_df, "false_positive", max_rows=sample_rows, text_limit=text_limit),
            "false_negative": build_representative_cases(fn_df, "false_negative", max_rows=sample_rows, text_limit=text_limit),
            "type_mismatch": build_representative_cases(tm_df, "type_mismatch", max_rows=sample_rows, text_limit=text_limit),
            "score_diff": build_representative_cases(sd_df, "score_diff", max_rows=sample_rows, text_limit=text_limit),
        },
    }
    return payload


def require_openai_client():
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(
            "未安装 openai 包。请先执行: pip install openai"
        ) from e
    return OpenAI


def extract_json_block(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM 返回为空")

    fenced = re.search(r"```json\s*(\{.*\})\s*```", text, flags=re.S | re.I)
    if fenced:
        return fenced.group(1)

    direct = re.search(r"(\{.*\})", text, flags=re.S)
    if direct:
        return direct.group(1)

    raise ValueError("未从 LLM 返回中提取到 JSON")


def call_llm_for_error_patterns(
    payload: dict,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_retries: int = 2,
) -> dict:
    load_env_file()
    OpenAI = require_openai_client()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("运行前请先设置 DEEPSEEK_API_KEY（可放在 .env 文件中）。")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    client = OpenAI(api_key=api_key, base_url=base_url)

    payload_str = json.dumps(payload, ensure_ascii=False, indent=2)

    system_prompt = (
        "你是一个专门分析加密货币新闻风险标注错误的诊断助手。"
        "你会同时参考统计结果和样本案例，总结规则系统的错误模式。"
        "必须严格基于输入证据，不要编造未出现的现象。"
        "输出必须是纯 JSON，不要输出 markdown，不要解释。"
    )

    user_prompt = f"""
请分析下面这份规则系统误差诊断材料，输出结构化 JSON。

你的任务：
1. 识别 false_positive / false_negative / type_mismatch / score_diff 四类错误中最重要的错误模式。
2. 总结“哪些 scorer 最可能有问题、问题是什么、应该怎么修”。
3. patch 建议必须尽量具体，最好能落到“收紧关键词 / 增加否定词 / 增加金额门槛 / 调整阈值 / 修正主类别选择逻辑 / 区分强弱触发”这类层面。
4. 只能根据提供的统计结果与样本来推断，不要发散。
5. 如果证据不足，就明确写“证据不足”。

输出 JSON schema：
{{
  "executive_summary": ["一句话结论1", "一句话结论2"],
  "patterns": [
    {{
      "category": "false_positive 或 false_negative 或 type_mismatch 或 score_diff",
      "pattern_name": "错误模式名",
      "affected_scorers": ["score_whale", "score_liquidation"],
      "evidence": ["证据1", "证据2"],
      "likely_root_cause": "根因总结",
      "patch_suggestions": ["建议1", "建议2"],
      "priority": "high 或 medium 或 low"
    }}
  ],
  "scorer_diagnosis": [
    {{
      "score_col": "score_xxx",
      "problem_type": "过宽触发 / 漏召回 / 强度映射偏高 / 主类别竞争失败 / 证据不足",
      "why": "为什么这么判断",
      "recommendation": "怎么改"
    }}
  ],
  "patch_plan": [
    {{
      "step": 1,
      "target": "要先修的 scorer 或逻辑",
      "action": "修复动作",
      "expected_benefit": "预期收益"
    }}
  ],
  "confidence": "high 或 medium 或 low"
}}

下面是材料：
{payload_str}
""".strip()

    last_error = None
    for _ in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = resp.choices[0].message.content or ""
            return json.loads(extract_json_block(content))
        except Exception as e:
            last_error = e

    raise RuntimeError(f"LLM 分析失败: {last_error}")


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

    lines.append("## LLM 总结")
    lines.append("")
    for item in llm.get("executive_summary", []):
        lines.append(f"- {item}")
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
        lines.append("")

    lines.append("## Scorer 级诊断")
    lines.append("")
    for item in llm.get("scorer_diagnosis", []):
        lines.append(f"- **{item.get('score_col', '')}**: {item.get('problem_type', '')}")
        if item.get("why"):
            lines.append(f"  - 原因: {item.get('why')}")
        if item.get("recommendation"):
            lines.append(f"  - 建议: {item.get('recommendation')}")
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
        fp_df = read_csv_safe(false_positive_csv)
        fn_df = read_csv_safe(false_negative_csv)
        tm_df = read_csv_safe(type_mismatch_csv)
        sd_df = read_csv_safe(score_diff_top_csv)
        progress.update("错误样本读取完成")

        # 3) 构建 payload
        payload = build_analysis_payload(
            fp_df=fp_df,
            fn_df=fn_df,
            tm_df=tm_df,
            sd_df=sd_df,
            sample_rows=sample_rows,
            text_limit=text_limit,
        )
        progress.update("分析 payload 构建完成")

        # 4) 保存 payload
        ensure_parent_dir(llm_payload_json)
        with open(llm_payload_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        progress.update("payload 文件写入完成")

        # 5) 调 LLM
        llm_analysis = call_llm_for_error_patterns(payload=payload, model=model)
        progress.update("LLM 诊断完成")

        # 6) 写 analysis_json
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

        ensure_parent_dir(analysis_json)
        with open(analysis_json, "w", encoding="utf-8") as f:
            json.dump(full_analysis, f, ensure_ascii=False, indent=2)
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent

    false_positive_csv = Path(args.false_positive_csv) if args.false_positive_csv else project_root / "reports" / "errors" / "risk_labeler_v1_false_positive.csv"
    false_negative_csv = Path(args.false_negative_csv) if args.false_negative_csv else project_root / "reports" / "errors" / "risk_labeler_v1_false_negative.csv"
    type_mismatch_csv = Path(args.type_mismatch_csv) if args.type_mismatch_csv else project_root / "reports" / "errors" / "risk_labeler_v1_type_mismatch.csv"
    score_diff_top_csv = Path(args.score_diff_top_csv) if args.score_diff_top_csv else project_root / "reports" / "errors" / "risk_labeler_v1_score_diff_top.csv"
    analysis_json = Path(args.analysis_json) if args.analysis_json else project_root / "reports" / "analysis" / "risk_labeler_v1_analysis_llm.json"
    analysis_markdown = Path(args.analysis_markdown) if args.analysis_markdown else project_root / "reports" / "analysis" / "risk_labeler_v1_analysis_llm.md"
    llm_payload_json = Path(args.llm_payload_json) if args.llm_payload_json else project_root / "reports" / "analysis" / "risk_labeler_v1_analysis_llm_payload.json"

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
