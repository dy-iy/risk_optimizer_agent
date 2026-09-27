import argparse
import ast
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from .tools.common import (
    StageProgress,
    call_chat_json,
    ensure_parent_dir,
    read_text_file,
    write_json_file,
)
from .tools.paths import resolve_project_root, resolve_versions_dir
from .prompts.patcher import build_messages, build_patch_prompt, build_repair_prompt

DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_TEMPERATURE = float(os.environ.get("PATCHER_LLM_TEMPERATURE", "0.2"))
DEFAULT_MAX_RETRIES = int(os.environ.get("PATCHER_LLM_MAX_RETRIES", "2"))
DEFAULT_SOURCE_CODE_LIMIT = int(os.environ.get("PATCHER_SOURCE_CODE_LIMIT", "240000"))
DEFAULT_ANALYSIS_LIMIT = int(os.environ.get("PATCHER_ANALYSIS_LIMIT", "120000"))
DEFAULT_PATCH_GENERATION_ATTEMPTS = max(
    2,
    int(os.environ.get("PATCHER_GENERATION_ATTEMPTS", "3")),
)

@dataclass
class PatchLLMResult:
    success: bool
    source_script: str
    analysis_json: str
    output_script: str
    patch_report_json: str
    patch_report_markdown: str
    model: str
    output_bytes: int = 0
    syntax_ok: bool = False
    error_message: str = ""


def compact_scorer_trace(trace: list[dict], max_items: int = 3) -> list[dict]:
    compact = []
    for item in trace[:max_items]:
        compact.append(
            {
                "score_col": item.get("score_col", ""),
                "role": item.get("role", ""),
                "value": item.get("value"),
                "score_band": item.get("score_band", ""),
                "matched_positive_keyword_lists": item.get("matched_positive_keyword_lists", [])[:3],
                "matched_negative_keyword_lists": item.get("matched_negative_keyword_lists", [])[:3],
                "matched_or_possible_early_return_guards": (
                    item.get("matched_or_possible_early_return_guards", [])[:3]
                ),
            }
        )
    return compact


def compact_type_sample_case(case: dict) -> dict:
    return {
        "news_id": case.get("news_id", ""),
        "title": case.get("title", ""),
        "content": case.get("content", ""),
        "gold_primary_risk_type": case.get("gold_primary_risk_type", ""),
        "rule_primary_risk_type": case.get("rule_primary_risk_type", ""),
        "all_score_values": case.get("all_score_values", [])[:6],
        "primary_type_competition": case.get("primary_type_competition", {}),
        "scorer_trace": compact_scorer_trace(case.get("scorer_trace", []) or []),
    }


def compact_score_sample_case(case: dict) -> dict:
    return {
        "news_id": case.get("news_id", ""),
        "title": case.get("title", ""),
        "content": case.get("content", ""),
        "gold_risk_score": case.get("gold_risk_score"),
        "rule_risk_score": case.get("rule_risk_score"),
        "score_diff": case.get("score_diff"),
        "rule_minus_gold": case.get("rule_minus_gold"),
        "score_diff_direction": case.get("score_diff_direction", ""),
        "gold_risk_label": case.get("gold_risk_label", ""),
        "rule_risk_label": case.get("rule_risk_label", ""),
        "gold_primary_risk_type": case.get("gold_primary_risk_type", ""),
        "rule_primary_risk_type": case.get("rule_primary_risk_type", ""),
    }


def compact_source_code_context_for_patcher(source_code_context: dict) -> dict:
    scorer_summaries = source_code_context.get("scorer_summaries", {}) or {}
    compact_summaries = {}
    for score_col, info in scorer_summaries.items():
        compact_summaries[score_col] = {
            "positive_keyword_lists": (info.get("positive_keyword_lists", []) or [])[:8],
            "negative_keyword_lists": (info.get("negative_keyword_lists", []) or [])[:8],
            "early_return_guards": [
                {
                    "condition": guard.get("condition", ""),
                    "keyword_lists": (guard.get("keyword_lists", []) or [])[:6],
                    "return_values": (guard.get("return_values", []) or [])[:3],
                }
                for guard in (info.get("early_return_guards", []) or [])[:5]
            ],
        }
    return {
        "source_script": source_code_context.get("source_script", ""),
        "source_available": source_code_context.get("source_available", False),
        "thresholds": source_code_context.get("thresholds", {}),
        "risk_name_map": source_code_context.get("risk_name_map", {}),
        "scorer_summaries": compact_summaries,
        "calibrators": source_code_context.get("calibrators", {}),
        "trace_note": source_code_context.get("trace_note", ""),
    }


def compact_type_mismatch_first_for_patcher(type_mismatch_first: dict, max_pairs: int = 12) -> dict:
    if not isinstance(type_mismatch_first, dict):
        return {}

    compact_pairs = []
    for pair in (type_mismatch_first.get("top_confusion_pairs", []) or [])[:max_pairs]:
        if not isinstance(pair, dict):
            continue
        compact_pairs.append(
            {
                "gold_primary_risk_type": pair.get("gold_primary_risk_type", ""),
                "rule_primary_risk_type": pair.get("rule_primary_risk_type", ""),
                "gold_score_col": pair.get("gold_score_col", ""),
                "rule_score_col": pair.get("rule_score_col", ""),
                "count": pair.get("count", 0),
                "avg_rule_primary_score": pair.get("avg_rule_primary_score"),
                "avg_gold_type_rule_score": pair.get("avg_gold_type_rule_score"),
                "avg_primary_vs_gold_gap": pair.get("avg_primary_vs_gold_gap"),
                "diagnostic_instruction": pair.get("diagnostic_instruction", ""),
                "gold_scorer_context": {
                    **(pair.get("gold_scorer_context", {}) or {}),
                    "function_preview": str((pair.get("gold_scorer_context", {}) or {}).get("function_preview", ""))[:1800],
                },
                "rule_scorer_context": {
                    **(pair.get("rule_scorer_context", {}) or {}),
                    "function_preview": str((pair.get("rule_scorer_context", {}) or {}).get("function_preview", ""))[:1800],
                },
                "sample_cases": [
                    compact_type_sample_case(case)
                    for case in (pair.get("sample_cases", []) or [])[:8]
                    if isinstance(case, dict)
                ],
            }
        )

    return {
        "priority": type_mismatch_first.get("priority", ""),
        "rule": type_mismatch_first.get("rule", ""),
        "stage_gate": type_mismatch_first.get("stage_gate", {}),
        "total_type_mismatch_rows": type_mismatch_first.get("total_type_mismatch_rows", 0),
        "score_calibration_policy": type_mismatch_first.get("score_calibration_policy", ""),
        "top_confusion_pairs": compact_pairs,
    }


def compact_score_calibration_scope_for_patcher(score_scope: dict, max_groups: int = 12) -> dict:
    if not isinstance(score_scope, dict):
        return {}

    compact_groups = []
    for group in (score_scope.get("target_groups", []) or [])[:max_groups]:
        if not isinstance(group, dict):
            continue
        compact_groups.append(
            {
                "score_col": group.get("score_col", ""),
                "risk_type_name": group.get("risk_type_name", ""),
                "target_function": group.get("target_function", ""),
                "allowed_code_scope": group.get("allowed_code_scope", ""),
                "row_count": group.get("row_count", 0),
                "mean_rule_minus_gold": group.get("mean_rule_minus_gold"),
                "direction_summary": group.get("direction_summary", []),
                "distribution": group.get("distribution", {}),
                "calibration_architecture": group.get("calibration_architecture", {}),
                "scorer_context": {
                    **(group.get("scorer_context", {}) or {}),
                    "function_preview": str((group.get("scorer_context", {}) or {}).get("function_preview", ""))[:1800],
                },
                "sample_cases": [
                    compact_score_sample_case(case)
                    for case in (group.get("sample_cases", []) or [])[:8]
                    if isinstance(case, dict)
                ],
            }
        )

    return {
        "locked": score_scope.get("locked", False),
        "total_score_diff_rows": score_scope.get("total_score_diff_rows", 0),
        "correctly_typed_score_diff_rows": score_scope.get("correctly_typed_score_diff_rows", 0),
        "target_groups": compact_groups,
    }


def trim_route_sample_cases(compact: dict, max_cases: int) -> None:
    mismatch = compact.get("type_mismatch_first", {}) or {}
    for pair in mismatch.get("top_confusion_pairs", []) or []:
        if isinstance(pair, dict):
            pair["sample_cases"] = (pair.get("sample_cases", []) or [])[:max_cases]

    score_scope = compact.get("score_calibration_scope", {}) or {}
    for group in score_scope.get("target_groups", []) or []:
        if isinstance(group, dict):
            group["sample_cases"] = (group.get("sample_cases", []) or [])[:max_cases]


def compact_version_history_for_route(version_history: object, route: str) -> dict:
    if not isinstance(version_history, dict):
        return {}

    if route == "score_calibration":
        allowed_metrics = {
            "version",
            "score_diff_top_rows",
            "score_diff_mean",
            "score_diff_rmse",
            "label_accuracy",
        }
        allowed_regression_metrics = {
            "score_diff_top_rows",
            "score_diff_mean",
            "score_diff_rmse",
            "label_accuracy",
        }
    else:
        allowed_metrics = {
            "version",
            "false_positive_rows",
            "false_negative_rows",
            "type_mismatch_rows",
            "primary_type_accuracy",
        }
        allowed_regression_metrics = {
            "false_positive_rows",
            "false_negative_rows",
            "type_mismatch_rows",
            "primary_type_accuracy",
        }

    metrics = []
    for item in version_history.get("metrics", []) or []:
        if isinstance(item, dict):
            metrics.append({key: value for key, value in item.items() if key in allowed_metrics})

    regression = version_history.get("regression_summary", {}) or {}
    if not isinstance(regression, dict):
        regression = {}
    filtered_regression = {}
    for direction in ("improved_metrics", "worsened_metrics"):
        filtered_regression[direction] = [
            item
            for item in regression.get(direction, []) or []
            if isinstance(item, dict) and item.get("metric") in allowed_regression_metrics
        ]
    return {
        "metrics": metrics,
        "regression_summary": filtered_regression,
    }


def compact_source_context_for_route(
    source_code_context: object,
    route: str,
    score_scope: dict,
) -> dict:
    if not isinstance(source_code_context, dict):
        return {}
    compact = compact_source_code_context_for_patcher(source_code_context)
    if route == "type_mismatch_repair":
        compact.pop("calibrators", None)
        return compact

    target_score_cols = {
        str(group.get("score_col", ""))
        for group in score_scope.get("target_groups", []) or []
        if isinstance(group, dict) and str(group.get("score_col", "")).strip()
    }
    target_score_cols.add("score_all_risks")
    scorer_summaries = compact.get("scorer_summaries", {}) or {}
    compact["scorer_summaries"] = {
        score_col: info
        for score_col, info in scorer_summaries.items()
        if score_col in target_score_cols
    }
    return compact


def compact_validation_feedback_for_route(feedback: object, route: str) -> dict:
    if not isinstance(feedback, dict):
        return {}
    if route == "score_calibration":
        allowed_metric_keys = {
            "score_mae",
            "score_rmse",
            "label_accuracy",
        }
        blocked_reason_terms = ("type_mismatch", "primary_type")
    else:
        allowed_metric_keys = {
            "label_accuracy",
            "primary_type_accuracy",
            "false_positive_rows",
            "false_negative_rows",
            "type_mismatch_rows",
        }
        blocked_reason_terms = ("MAE", "RMSE", "score calibration")

    compact = {
        "accepted": feedback.get("accepted"),
        "stage": feedback.get("stage", route),
        "reasons": [
            reason
            for reason in feedback.get("reasons", []) or []
            if isinstance(reason, str)
            and not any(term in reason for term in blocked_reason_terms)
        ],
    }
    for snapshot_name in ("baseline", "candidate"):
        snapshot = feedback.get(snapshot_name, {}) or {}
        if isinstance(snapshot, dict):
            compact[snapshot_name] = {
                key: value
                for key, value in snapshot.items()
                if key in allowed_metric_keys
            }

    rejected = feedback.get("rejected_patch_summary", {}) or {}
    if isinstance(rejected, dict):
        compact_rejected = {
            "summary": rejected.get("summary", []),
            "changes": rejected.get("changes", []),
            "patch_application": rejected.get("patch_application", {}),
            "instruction": rejected.get("instruction", ""),
        }
        if route == "score_calibration":
            compact_rejected["applied_score_calibration_fixes"] = rejected.get(
                "applied_score_calibration_fixes", []
            )
        else:
            compact_rejected["applied_type_mismatch_fixes"] = rejected.get(
                "applied_type_mismatch_fixes", []
            )
        compact["rejected_patch_summary"] = compact_rejected
    return compact


def compact_type_statistics_for_patcher(statistics: object) -> dict:
    if not isinstance(statistics, dict):
        return {}
    compact = dict(statistics)
    compact["mismatch_pair_score_summary"] = [
        {
            key: value
            for key, value in item.items()
            if key
            not in {
                "avg_rule_risk_score",
                "avg_gold_risk_score",
                "avg_abs_score_diff",
                "avg_rule_minus_gold",
            }
        }
        for item in statistics.get("mismatch_pair_score_summary", []) or []
        if isinstance(item, dict)
    ]
    return compact


def compact_diagnostic_strategy_for_route(strategy: object, route: str) -> dict:
    if not isinstance(strategy, dict):
        return {}
    compact = {
        "active_stage": strategy.get("active_stage", route),
        "prompt_route": strategy.get("prompt_route", route),
        "score_calibration_locked": strategy.get("score_calibration_locked"),
    }
    if route == "type_mismatch_repair":
        compact["type_mismatch_rows"] = strategy.get("type_mismatch_rows", 0)
        compact["type_mismatch_score_stage_threshold"] = strategy.get(
            "type_mismatch_score_stage_threshold", 80
        )
        compact["type_mismatch_definition"] = strategy.get(
            "type_mismatch_definition", ""
        )
    else:
        compact["calibration_rule"] = (
            "Use only correctly typed score-difference groups and preserve raw type evidence."
        )
    return compact


def compact_analysis_for_patcher(analysis_data: dict, limit: int = DEFAULT_ANALYSIS_LIMIT) -> dict:
    analysis_data_str = json.dumps(analysis_data, ensure_ascii=False)
    llm = analysis_data.get("llm_analysis", {}) if isinstance(analysis_data, dict) else {}
    statistics = analysis_data.get("statistics", {}) if isinstance(analysis_data, dict) else {}
    strategy = analysis_data.get("diagnostic_strategy", {}) or {}
    active_stage = str(strategy.get("active_stage", "type_mismatch_repair"))
    type_mismatch = compact_type_mismatch_first_for_patcher(
        analysis_data.get("type_mismatch_first", {}) or {}
    )
    score_scope = compact_score_calibration_scope_for_patcher(
        analysis_data.get("score_calibration_scope", {}) or {}
    )
    compact = {
        "diagnostic_strategy": compact_diagnostic_strategy_for_route(
            strategy,
            active_stage,
        ),
        "risk_taxonomy": analysis_data.get("risk_taxonomy", []),
        "version_history": compact_version_history_for_route(
            analysis_data.get("version_history", {}),
            active_stage,
        ),
        "source_code_context": compact_source_context_for_route(
            analysis_data.get("source_code_context", {}),
            active_stage,
            score_scope,
        ),
        "meta": {
            "compacted_for_patcher": True,
            "original_chars": len(analysis_data_str),
        },
    }

    if active_stage == "score_calibration":
        summary = analysis_data.get("summary", {}) or {}
        compact["summary"] = {
            "score_diff_top_rows": summary.get("score_diff_top_rows", 0),
        }
        compact["score_calibration_scope"] = score_scope
        compact["llm_analysis"] = {
            "route": llm.get("route", {}),
            "score_calibration_after_type_fix": llm.get(
                "score_calibration_after_type_fix", []
            ),
            "do_not_patch": llm.get("do_not_patch", []),
            "confidence": llm.get("confidence", ""),
        }
        compact["statistics"] = {
            "score_diff_analysis": statistics.get("score_diff_analysis", {}),
        }
        compact["meta"]["route_focused_section"] = "score_calibration_scope"
    else:
        summary = analysis_data.get("summary", {}) or {}
        compact["summary"] = {
            "false_positive_rows": summary.get("false_positive_rows", 0),
            "false_negative_rows": summary.get("false_negative_rows", 0),
            "type_mismatch_rows": summary.get("type_mismatch_rows", 0),
        }
        compact["type_mismatch_first"] = type_mismatch
        compact["llm_analysis"] = {
            "route": llm.get("route", {}),
            "type_mismatch_first_summary": llm.get("type_mismatch_first_summary", {}),
            "type_mismatch_pair_diagnosis": llm.get(
                "type_mismatch_pair_diagnosis", []
            ),
            "patch_plan_by_scorer": llm.get("patch_plan_by_scorer", []),
            "do_not_patch": llm.get("do_not_patch", []),
            "confidence": llm.get("confidence", ""),
        }
        compact["statistics"] = {
            "type_mismatch_analysis": compact_type_statistics_for_patcher(
                statistics.get("type_mismatch_analysis", {})
            ),
            "false_positive_analysis": statistics.get("false_positive_analysis", {}),
            "false_negative_analysis": statistics.get("false_negative_analysis", {}),
        }
        compact["meta"]["route_focused_section"] = "type_mismatch_first"

    compact_str = json.dumps(compact, ensure_ascii=False)
    if len(compact_str) <= limit:
        return compact

    trim_route_sample_cases(compact, max_cases=4)
    compact_str = json.dumps(compact, ensure_ascii=False)
    if len(compact_str) <= limit:
        compact["meta"]["reduced_route_sample_cases_due_to_size"] = 4
        return compact

    compact["source_code_context"]["scorer_summaries"] = {}
    compact_str = json.dumps(compact, ensure_ascii=False)
    if len(compact_str) <= limit:
        compact["meta"]["dropped_scorer_summaries_due_to_size"] = True
        return compact

    trim_route_sample_cases(compact, max_cases=2)
    compact["meta"]["reduced_route_sample_cases_due_to_size"] = 2
    return compact


def resolve_default_source_script(project_root: Path) -> Path | None:
    env_path = os.environ.get("SOURCE_SCRIPT")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    versions_dir = resolve_versions_dir(project_root)
    candidates = [
        versions_dir / "v1" / "scripts" / "risk_labeler_v1.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def resolve_default_analysis_json(project_root: Path) -> Path:
    env_path = os.environ.get("ANALYSIS_LLM_JSON")
    if env_path:
        return Path(env_path)
    return resolve_versions_dir(project_root) / "v1" / "reports" / "analysis" / "risk_labeler_v1_analysis_llm.json"


def compile_check(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<patched_script>", "exec")
        return True, ""
    except SyntaxError as e:
        msg = f"SyntaxError: {e.msg} at line {e.lineno}, offset {e.offset}"
        if e.text:
            msg += f"\n>>> {e.text.rstrip()}"
        return False, msg
    except Exception as e:
        return False, f"CompileError: {e}"


def strip_docstring(statements: list[ast.stmt]) -> list[ast.stmt]:
    if not statements:
        return statements
    first = statements[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return statements[1:]
    return statements


def clear_arg_annotations(args: ast.arguments) -> None:
    for arg in args.posonlyargs + args.args + args.kwonlyargs:
        arg.annotation = None
        arg.type_comment = None
    if args.vararg:
        args.vararg.annotation = None
        args.vararg.type_comment = None
    if args.kwarg:
        args.kwarg.annotation = None
        args.kwarg.type_comment = None


class RuntimeAstNormalizer(ast.NodeTransformer):
    def visit_Module(self, node: ast.Module) -> ast.AST:
        self.generic_visit(node)
        node.body = strip_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.generic_visit(node)
        node.body = strip_docstring(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        clear_arg_annotations(node.args)
        node.returns = None
        node.type_comment = None
        node.body = strip_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        clear_arg_annotations(node.args)
        node.returns = None
        node.type_comment = None
        node.body = strip_docstring(node.body)
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        self.generic_visit(node)
        if node.value is None:
            return ast.copy_location(ast.Pass(), node)
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)


def normalized_runtime_ast(code: str) -> str:
    tree = ast.parse(code)
    normalized_tree = RuntimeAstNormalizer().visit(tree)
    ast.fix_missing_locations(normalized_tree)
    return ast.dump(normalized_tree, include_attributes=False)


def has_executable_code_change(source_code: str, candidate_code: str) -> bool:
    if candidate_code == source_code:
        return False
    try:
        return normalized_runtime_ast(source_code) != normalized_runtime_ast(candidate_code)
    except SyntaxError:
        return True


def sanitize_code_block(code: str) -> str:
    code = (code or "").strip()
    fence = re.match(r"^```(?:python)?\s*(.*?)\s*```$", code, flags=re.S | re.I)
    if fence:
        return fence.group(1).strip() + "\n"
    return code + ("\n" if code and not code.endswith("\n") else "")


def sanitize_patch_text(code: str) -> str:
    code = (code or "").replace("\r\n", "\n")
    fence = re.match(r"^\s*```(?:python)?\s*\n(.*?)\n```\s*$", code, flags=re.S | re.I)
    if fence:
        code = fence.group(1)
    return code.strip("\n") + ("\n" if code.strip("\n") else "")


def apply_ordered_patches(
    source_code: str,
    operations: object,
) -> tuple[str, list[str], list[str], int]:
    if not isinstance(operations, list):
        return source_code, ["ordered_patches 必须是数组"], [], 0

    patched_code = source_code
    errors: list[str] = []
    skipped: list[str] = []
    applied_count = 0
    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            errors.append(f"patch {index}: 必须是对象")
            continue

        target = str(operation.get("target", f"patch_{index}"))
        old_code = sanitize_patch_text(str(operation.get("old_code", "")))
        new_code = sanitize_patch_text(str(operation.get("new_code", "")))
        if not old_code.strip():
            errors.append(f"patch {index} ({target}): old_code 为空")
            continue
        if not new_code.strip():
            errors.append(f"patch {index} ({target}): new_code 为空")
            continue
        if old_code == new_code:
            skipped.append(f"patch {index} ({target}): 新旧代码相同，已跳过")
            continue

        match_count = patched_code.count(old_code)
        if match_count != 1:
            errors.append(
                f"patch {index} ({target}): old_code 应唯一匹配，实际匹配 {match_count} 次"
            )
            continue
        patched_code = patched_code.replace(old_code, new_code, 1)
        applied_count += 1

    return patched_code, errors, skipped, applied_count


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    return call_chat_json(
        messages=build_messages(system_prompt, user_prompt),
        model=model,
        default_base_url=DEFAULT_BASE_URL,
        temperature=temperature,
        max_retries=max_retries,
        error_prefix="LLM 调用失败",
    )


def render_patch_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# 规则脚本 Patch 报告（LLM版）")
    lines.append("")

    patch_version = report.get("patch_version", "")
    if patch_version:
        lines.append(f"**Patch 版本**: {patch_version}")
        lines.append("")

    lines.append("## 摘要")
    lines.append("")
    for item in report.get("summary", []) or []:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 变更明细")
    lines.append("")
    for i, item in enumerate(report.get("changes", []) or [], start=1):
        lines.append(f"### {i}. {item.get('target', '未命名目标')}")
        if item.get("reason"):
            lines.append(f"- 原因: {item.get('reason')}" )
        if item.get("action"):
            lines.append(f"- 动作: {item.get('action')}" )
        if item.get("risk"):
            lines.append(f"- 风险: {item.get('risk')}" )
        if item.get("guardrail"):
            lines.append(f"- Guardrail: {item.get('guardrail')}" )
        if item.get("validation"):
            lines.append(f"- 验证: {item.get('validation')}" )
        lines.append("")

    notes = report.get("validation_notes", []) or []
    if notes:
        lines.append("## 注意事项")
        lines.append("")
        for item in notes:
            lines.append(f"- {item}")
        lines.append("")

    meta = report.get("meta", {}) or {}
    if meta:
        lines.append("## 元信息")
        lines.append("")
        for k, v in meta.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def attach_patch_meta(
    report: dict,
    model: str,
    syntax_ok: bool,
    source_script: Path,
    analysis_json: Path,
    compile_error: str,
) -> dict:
    report["meta"] = {
        "model": model,
        "syntax_ok": syntax_ok,
        "source_script": str(source_script),
        "analysis_json": str(analysis_json),
    }
    if compile_error:
        report["meta"]["compile_error"] = compile_error
    return report


def patch_script_with_llm(
    source_script: str | Path,
    analysis_json: str | Path,
    output_script: str | Path,
    patch_report_json: str | Path,
    patch_report_markdown: str | Path,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    show_progress: bool = True,
    candidate_validation_feedback: dict | None = None,
) -> PatchLLMResult:
    source_script = Path(source_script).resolve()
    analysis_json = Path(analysis_json).resolve()
    output_script = Path(output_script).resolve()
    patch_report_json = Path(patch_report_json).resolve()
    patch_report_markdown = Path(patch_report_markdown).resolve()

    progress = StageProgress(total=9, enabled=show_progress, desc="Patcher LLM")

    try:
        # 1) 检查输入文件
        if not source_script.exists():
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=f"source_script 不存在: {source_script}",
            )

        if not analysis_json.exists():
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=f"analysis_json 不存在: {analysis_json}",
            )
        progress.update("输入文件检查完成")

        # 2) 读取源脚本
        source_code = read_text_file(source_script)
        if len(source_code) > DEFAULT_SOURCE_CODE_LIMIT:
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=(
                    f"源脚本长度 {len(source_code)} 超过 PATCHER_SOURCE_CODE_LIMIT="
                    f"{DEFAULT_SOURCE_CODE_LIMIT}；拒绝截断完整脚本"
                ),
            )
        progress.update("源脚本读取完成")

        # 3) 读取 analysis_json
        analysis_data = json.loads(read_text_file(analysis_json))
        analysis_data = compact_analysis_for_patcher(analysis_data, DEFAULT_ANALYSIS_LIMIT)
        if candidate_validation_feedback:
            active_stage = str(
                (analysis_data.get("diagnostic_strategy", {}) or {}).get(
                    "active_stage",
                    "type_mismatch_repair",
                )
            )
            analysis_data["candidate_validation_feedback"] = (
                compact_validation_feedback_for_route(
                    candidate_validation_feedback,
                    active_stage,
                )
            )
        progress.update("analysis_json 读取完成")

        # 4) 构造 prompt
        system_prompt, user_prompt = build_patch_prompt(
            source_code=source_code,
            analysis_data=analysis_data,
        )
        progress.update("patch prompt 构建完成")

        # 5) 生成必须包含实际代码变化的 LLM patch
        llm_report: dict = {}
        ordered_patches: object = []
        full_code = source_code
        skipped_patches: list[str] = []
        applied_patch_count = 0
        used_ordered_patches = False
        generation_failures: list[str] = []
        generation_attempt_used = 0

        for generation_attempt in range(1, DEFAULT_PATCH_GENERATION_ATTEMPTS + 1):
            current_user_prompt = user_prompt
            if generation_failures:
                retry_feedback = generation_failures[-1]
                current_user_prompt += f"""

上一次 patch 输出没有产生可应用的实际代码变化，必须重新生成。
失败原因：{retry_feedback}

本次重试要求：
- 仍然只使用当前路由和原始 analysis_llm.json 中的证据。
- 从尚未被当前脚本实现的 P0/P1/P2 建议中选择修改。
- old_code 必须从当前脚本逐字复制且唯一匹配。
- new_code 必须与 old_code 实质不同。
- 不要重复上一次的 no-op 或无法匹配操作。
""".rstrip()

            llm_report = call_llm_json(
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                model=model,
                temperature=temperature,
            )
            ordered_patches = llm_report.get("ordered_patches", [])
            full_code, patch_errors, skipped_patches, applied_patch_count = (
                apply_ordered_patches(source_code, ordered_patches)
            )
            used_ordered_patches = applied_patch_count > 0
            fallback_code = sanitize_code_block(str(llm_report.get("full_code", "")))

            if patch_errors:
                if fallback_code.strip() and fallback_code != source_code:
                    full_code = fallback_code
                    used_ordered_patches = False
                else:
                    generation_failures.append(
                        "ordered_patches 应用失败: " + " | ".join(patch_errors)
                    )
                    continue
            elif not used_ordered_patches and fallback_code.strip():
                full_code = fallback_code

            if not full_code.strip():
                generation_failures.append(
                    "未返回可应用的 ordered_patches 或 full_code"
                )
                continue
            if full_code == source_code:
                detail = (
                    " | ".join(skipped_patches)
                    if skipped_patches
                    else "所有建议都已实现或没有实际变化"
                )
                generation_failures.append("没有生成实际代码改动: " + detail)
                continue
            if not has_executable_code_change(source_code, full_code):
                generation_failures.append(
                    "只生成了注释、格式或类型标注变化；必须修改风险类型/风险分数相关执行逻辑"
                )
                continue

            generation_attempt_used = generation_attempt
            break

        if generation_attempt_used == 0:
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=(
                    f"连续 {DEFAULT_PATCH_GENERATION_ATTEMPTS} 次未生成实际代码改动: "
                    + " || ".join(generation_failures)
                ),
            )
        progress.update(
            f"LLM patch 第 {generation_attempt_used} 次生成成功"
        )

        llm_report["patch_application"] = {
            "mode": (
                "ordered_patches" if used_ordered_patches else "full_code_fallback"
            ),
            "generation_attempt": generation_attempt_used,
            "max_generation_attempts": DEFAULT_PATCH_GENERATION_ATTEMPTS,
            "previous_generation_failures": generation_failures,
            "attempted_operation_count": (
                len(ordered_patches) if isinstance(ordered_patches, list) else 0
            ),
            "operation_count": applied_patch_count,
            "applied_operation_count": applied_patch_count,
            "skipped_operation_count": len(skipped_patches),
            "skipped_operations": skipped_patches,
            "no_code_change": False,
            "source_chars": len(source_code),
            "output_chars": len(full_code),
            "growth_chars": len(full_code) - len(source_code),
        }

        # 6) 语法检查 / 修复
        syntax_ok, compile_error = compile_check(full_code)
        if not syntax_ok:
            repair_system, repair_user = build_repair_prompt(full_code, compile_error)
            repaired = call_llm_json(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=model,
                temperature=0.0,
            )
            repaired_code = sanitize_code_block(str(repaired.get("full_code", "")))
            if repaired_code.strip():
                full_code = repaired_code
                syntax_ok, compile_error = compile_check(full_code)
                llm_report.setdefault("validation_notes", [])
                llm_report["validation_notes"] = list(llm_report.get("validation_notes", []) or []) + list(
                    repaired.get("repair_summary", []) or []
                )
            progress.update("语法修复完成")
        else:
            progress.update("语法检查通过")

        if syntax_ok and not has_executable_code_change(source_code, full_code):
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=(
                    "生成脚本只包含注释、格式或类型标注变化；没有可执行的优化逻辑变化"
                ),
            )

        # 7) 写 output_script
        llm_report = attach_patch_meta(
            report=llm_report,
            model=model,
            syntax_ok=syntax_ok,
            source_script=source_script,
            analysis_json=analysis_json,
            compile_error=compile_error,
        )

        ensure_parent_dir(output_script)
        output_script.write_text(full_code, encoding="utf-8")
        progress.update("output_script 写入完成")

        # 8) 写 patch_report_json
        write_json_file(patch_report_json, llm_report)
        progress.update("patch_report_json 写入完成")

        # 9) 写 patch_report_markdown
        ensure_parent_dir(patch_report_markdown)
        patch_report_markdown.write_text(
            render_patch_markdown(llm_report),
            encoding="utf-8",
        )
        progress.update("patch_report_markdown 写入完成")

        progress.close()

        return PatchLLMResult(
            success=syntax_ok,
            source_script=str(source_script),
            analysis_json=str(analysis_json),
            output_script=str(output_script),
            patch_report_json=str(patch_report_json),
            patch_report_markdown=str(patch_report_markdown),
            model=model,
            output_bytes=len(full_code.encode("utf-8")),
            syntax_ok=syntax_ok,
            error_message="" if syntax_ok else f"生成脚本仍未通过语法检查: {compile_error}",
        )
    except Exception:
        progress.close()
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch crypto risk rule script with LLM using analysis_llm.json")
    parser.add_argument("--source-script", type=str, default="")
    parser.add_argument("--analysis-json", type=str, default="")
    parser.add_argument("--output-script", type=str, default="")
    parser.add_argument("--patch-report-json", type=str, default="")
    parser.add_argument("--patch-report-markdown", type=str, default="")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    project_root = resolve_project_root()
    default_next_version_dir = resolve_versions_dir(project_root) / "v2"

    default_source = resolve_default_source_script(project_root)
    source_script = Path(args.source_script) if args.source_script else default_source
    analysis_json = Path(args.analysis_json) if args.analysis_json else resolve_default_analysis_json(project_root)
    output_script = Path(args.output_script) if args.output_script else default_next_version_dir / "scripts" / "risk_labeler_v2.py"
    patch_report_json = Path(args.patch_report_json) if args.patch_report_json else default_next_version_dir / "reports" / "patched" / "risk_labeler_v2_patch_report.json"
    patch_report_markdown = Path(args.patch_report_markdown) if args.patch_report_markdown else default_next_version_dir / "reports" / "patched" / "risk_labeler_v2_patch_report.md"

    if source_script is None:
        result = PatchLLMResult(
            success=False,
            source_script="",
            analysis_json=str(analysis_json),
            output_script=str(output_script),
            patch_report_json=str(patch_report_json),
            patch_report_markdown=str(patch_report_markdown),
            model=args.model,
            error_message="未找到默认 source_script，请通过 --source-script 显式传入。",
        )
    else:
        result = patch_script_with_llm(
            source_script=source_script,
            analysis_json=analysis_json,
            output_script=output_script,
            patch_report_json=patch_report_json,
            patch_report_markdown=patch_report_markdown,
            model=args.model,
            temperature=args.temperature,
            show_progress=True,
        )

    print("=" * 60)
    print("PATCH LLM RESULT")
    print("=" * 60)
    print(f"success               : {result.success}")
    print(f"model                 : {result.model}")
    print(f"source_script         : {result.source_script}")
    print(f"analysis_json         : {result.analysis_json}")
    print(f"output_script         : {result.output_script}")
    print(f"patch_report_json     : {result.patch_report_json}")
    print(f"patch_report_markdown : {result.patch_report_markdown}")
    print(f"output_bytes          : {result.output_bytes}")
    print(f"syntax_ok             : {result.syntax_ok}")
    if result.error_message:
        print(f"error_message         : {result.error_message}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
