from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

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
DEFAULT_SOURCE_CODE_LIMIT = int(os.environ.get("PATCHER_SOURCE_CODE_LIMIT", "80000"))
DEFAULT_ANALYSIS_LIMIT = int(os.environ.get("PATCHER_ANALYSIS_LIMIT", "50000"))

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


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n# [TRUNCATED]"


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


def compact_sample_case(case: dict) -> dict:
    return {
        "news_id": case.get("news_id", ""),
        "title": case.get("title", ""),
        "gold_risk_score": case.get("gold_risk_score"),
        "rule_risk_score": case.get("rule_risk_score"),
        "rule_minus_gold": case.get("rule_minus_gold"),
        "score_diff_direction": case.get("score_diff_direction", ""),
        "gold_primary_risk_type": case.get("gold_primary_risk_type", ""),
        "rule_primary_risk_type": case.get("rule_primary_risk_type", ""),
        "all_score_values": case.get("all_score_values", [])[:6],
        "primary_type_competition": case.get("primary_type_competition", {}),
        "scorer_trace": compact_scorer_trace(case.get("scorer_trace", []) or []),
    }


def compact_sample_cases_for_patcher(sample_cases: dict, max_cases: int = 4) -> dict:
    result = {}
    for key, value in sample_cases.items():
        if isinstance(value, list):
            result[key] = [compact_sample_case(item) for item in value[:max_cases] if isinstance(item, dict)]
        else:
            result[key] = value
    return result


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
        "trace_note": source_code_context.get("trace_note", ""),
    }


def compact_analysis_for_patcher(analysis_data: dict, limit: int = DEFAULT_ANALYSIS_LIMIT) -> dict:
    analysis_data_str = json.dumps(analysis_data, ensure_ascii=False)
    if len(analysis_data_str) <= limit:
        return analysis_data

    llm = analysis_data.get("llm_analysis", {}) if isinstance(analysis_data, dict) else {}
    statistics = analysis_data.get("statistics", {}) if isinstance(analysis_data, dict) else {}
    sample_cases = analysis_data.get("sample_cases", {}) if isinstance(analysis_data, dict) else {}

    compact = {
        "summary": analysis_data.get("summary", {}),
        "version_history": analysis_data.get("version_history", {}),
        "source_code_context": compact_source_code_context_for_patcher(
            analysis_data.get("source_code_context", {}) or {}
        ),
        "llm_analysis": {
            "executive_summary": llm.get("executive_summary", []),
            "metric_tradeoff_diagnosis": llm.get("metric_tradeoff_diagnosis", {}),
            "patterns": llm.get("patterns", []),
            "scorer_diagnosis": llm.get("scorer_diagnosis", []),
            "scorer_trace_diagnosis": llm.get("scorer_trace_diagnosis", []),
            "primary_type_diagnosis": llm.get("primary_type_diagnosis", []),
            "patch_plan": llm.get("patch_plan", []),
            "do_not_patch": llm.get("do_not_patch", []),
            "confidence": llm.get("confidence", ""),
        },
        "statistics": {
            "type_mismatch_analysis": statistics.get("type_mismatch_analysis", {}),
            "score_diff_analysis": statistics.get("score_diff_analysis", {}),
            "false_positive_analysis": statistics.get("false_positive_analysis", {}),
            "false_negative_analysis": statistics.get("false_negative_analysis", {}),
        },
        "sample_cases": compact_sample_cases_for_patcher(sample_cases),
        "meta": {
            "compacted_for_patcher": True,
            "original_chars": len(analysis_data_str),
        },
    }

    compact_str = json.dumps(compact, ensure_ascii=False)
    if len(compact_str) <= limit:
        return compact

    compact["sample_cases"] = compact_sample_cases_for_patcher(sample_cases, max_cases=2)
    compact_str = json.dumps(compact, ensure_ascii=False)
    if len(compact_str) <= limit:
        compact["meta"]["reduced_sample_cases_due_to_size"] = True
        return compact

    compact["source_code_context"]["scorer_summaries"] = {}
    compact_str = json.dumps(compact, ensure_ascii=False)
    if len(compact_str) <= limit:
        compact["meta"]["dropped_scorer_summaries_due_to_size"] = True
        return compact

    compact["sample_cases"] = {}
    compact["meta"]["dropped_sample_cases_due_to_size"] = True
    return compact


def resolve_default_source_script(project_root: Path) -> Optional[Path]:
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


def sanitize_code_block(code: str) -> str:
    code = (code or "").strip()
    fence = re.match(r"^```(?:python)?\s*(.*?)\s*```$", code, flags=re.S | re.I)
    if fence:
        return fence.group(1).strip() + "\n"
    return code + ("\n" if code and not code.endswith("\n") else "")


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
        source_code = truncate_text(read_text_file(source_script), DEFAULT_SOURCE_CODE_LIMIT)
        progress.update("源脚本读取完成")

        # 3) 读取 analysis_json
        analysis_data = json.loads(read_text_file(analysis_json))
        analysis_data = compact_analysis_for_patcher(analysis_data, DEFAULT_ANALYSIS_LIMIT)
        progress.update("analysis_json 读取完成")

        # 4) 构造 prompt
        system_prompt, user_prompt = build_patch_prompt(
            source_code=source_code,
            analysis_data=analysis_data,
        )
        progress.update("patch prompt 构建完成")

        # 5) 首轮 LLM patch
        llm_report = call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=temperature,
        )
        progress.update("LLM 首轮 patch 完成")

        full_code = sanitize_code_block(str(llm_report.get("full_code", "")))
        if not full_code.strip():
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message="LLM 未返回 full_code",
            )

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
