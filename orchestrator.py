from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from tools.common import StageProgress, write_json_file
from tools.paths import (
    build_paths,
    ensure_pipeline_dirs,
    normalize_version_name,
    resolve_default_gold_csv,
    resolve_default_input_csv,
    resolve_project_root,
    resolve_version_script,
)


# =========================================================
# 固定运行参数
# 说明：
# 1. 版本号仍然在运行时手动输入
# 2. 模块不再手动选择，固定跑：
#    runner -> merger -> evaluator -> slicer -> analyzer_llm -> patcher
# 3. patcher 生成下一版后，运行下一版并用 comparator 对比当前版
# 4. 不再保留 nollm analyzer
# =========================================================
INPUT_CSV: Optional[str] = None
GOLD_CSV: Optional[str] = None

# 如果想固定解释器，可以改成：
# PYTHON_EXECUTABLE = r"D:\Miniconda\python.exe"
PYTHON_EXECUTABLE: Optional[str] = None

TIMEOUT = 300

MODEL = "deepseek-v4-pro"
SAMPLE_ROWS = 12
TEXT_LIMIT = 220

ENABLE_PATCHER = True
PATCH_MODEL: Optional[str] = None
PATCH_TEMPERATURE = 0.2

# 一般不用填。默认会使用 analyzer_llm 刚生成的 analysis_json。
# 如果你想让 patcher 使用某个指定 analysis 文件，可以填绝对路径。
PATCH_SOURCE_ANALYSIS_JSON: Optional[str] = None


# =========================
# 结果结构
# =========================
@dataclass
class OrchestrateResult:
    success: bool
    current_version: str
    next_version: str

    script_path: str
    input_csv: str
    gold_csv: str

    prediction_csv: str
    merged_csv: str
    eval_json: str
    eval_details_json: str

    false_positive_csv: str
    false_negative_csv: str
    type_mismatch_csv: str
    score_diff_top_csv: str
    slice_log_json: str

    analysis_json: str = ""
    analysis_markdown: str = ""
    llm_payload_json: str = ""

    patched_script: str = ""
    patch_report_json: str = ""
    patch_report_markdown: str = ""
    patch_syntax_ok: bool = False

    candidate_prediction_csv: str = ""
    candidate_merged_csv: str = ""
    candidate_eval_json: str = ""
    candidate_eval_details_json: str = ""
    candidate_false_positive_csv: str = ""
    candidate_false_negative_csv: str = ""
    candidate_type_mismatch_csv: str = ""
    candidate_score_diff_top_csv: str = ""
    candidate_slice_log_json: str = ""

    compare_json: str = ""
    compare_markdown: str = ""
    compare_winner: str = ""

    orchestrate_json: str = ""
    steps_completed: int = 0
    error_message: str = ""


def write_json(path: Path, data: dict) -> None:
    write_json_file(path, data)


# =========================
# 动态导入
# =========================
def import_runner():
    from tools.runner import run_rule_script, save_run_result
    return run_rule_script, save_run_result


def import_merger():
    from tools.merger import merge_gold_and_predictions
    return merge_gold_and_predictions


def import_evaluator():
    from tools.evaluator import evaluate_merged_file
    return evaluate_merged_file


def import_slicer():
    from tools.slicer import slice_errors
    return slice_errors


def import_analyzer_llm():
    from analyzer_llm import analyze_error_files_with_llm
    return analyze_error_files_with_llm


def import_patcher():
    from patcher_llm_v2 import patch_script_with_llm
    return patch_script_with_llm


def import_comparator():
    from tools.comparator import compare_versions
    return compare_versions


# =========================
# 结果组装
# =========================
def build_result(
    *,
    current_version: str,
    next_version: str,
    script_path: Path,
    input_csv: Path,
    gold_csv: Path,
    paths: dict[str, Path],
    analysis_json: str = "",
    analysis_markdown: str = "",
    llm_payload_json: str = "",
    patched_script: str = "",
    patch_report_json: str = "",
    patch_report_markdown: str = "",
    patch_syntax_ok: bool = False,
    candidate_paths: dict[str, Path] | None = None,
    compare_winner: str = "",
    steps_completed: int = 0,
    success: bool = False,
    error_message: str = "",
) -> OrchestrateResult:
    candidate_paths = candidate_paths or {}

    return OrchestrateResult(
        success=success,
        current_version=current_version,
        next_version=next_version,

        script_path=str(script_path),
        input_csv=str(input_csv),
        gold_csv=str(gold_csv),

        prediction_csv=str(paths["prediction_csv"]),
        merged_csv=str(paths["merged_csv"]),
        eval_json=str(paths["eval_json"]),
        eval_details_json=str(paths["eval_details_json"]),

        false_positive_csv=str(paths["false_positive_csv"]),
        false_negative_csv=str(paths["false_negative_csv"]),
        type_mismatch_csv=str(paths["type_mismatch_csv"]),
        score_diff_top_csv=str(paths["score_diff_top_csv"]),
        slice_log_json=str(paths["slice_log_json"]),

        analysis_json=analysis_json,
        analysis_markdown=analysis_markdown,
        llm_payload_json=llm_payload_json,

        patched_script=patched_script,
        patch_report_json=patch_report_json,
        patch_report_markdown=patch_report_markdown,
        patch_syntax_ok=patch_syntax_ok,

        candidate_prediction_csv=str(candidate_paths.get("prediction_csv", "")),
        candidate_merged_csv=str(candidate_paths.get("merged_csv", "")),
        candidate_eval_json=str(candidate_paths.get("eval_json", "")),
        candidate_eval_details_json=str(candidate_paths.get("eval_details_json", "")),
        candidate_false_positive_csv=str(candidate_paths.get("false_positive_csv", "")),
        candidate_false_negative_csv=str(candidate_paths.get("false_negative_csv", "")),
        candidate_type_mismatch_csv=str(candidate_paths.get("type_mismatch_csv", "")),
        candidate_score_diff_top_csv=str(candidate_paths.get("score_diff_top_csv", "")),
        candidate_slice_log_json=str(candidate_paths.get("slice_log_json", "")),

        compare_json=str(paths.get("compare_json", "")),
        compare_markdown=str(paths.get("compare_markdown", "")),
        compare_winner=compare_winner,

        orchestrate_json=str(paths["orchestrate_json"]),
        steps_completed=steps_completed,
        error_message=error_message,
    )


def persist_and_return(result: OrchestrateResult, save_path: Path) -> OrchestrateResult:
    write_json(save_path, asdict(result))
    return result


# =========================
# 主流程
# =========================
def orchestrate_pipeline(
    current_version: str | int,
    next_version: str | int,
    input_csv: str | Path | None = None,
    gold_csv: str | Path | None = None,
    python_executable: Optional[str] = None,
    timeout: int = 300,
    model: Optional[str] = None,
    sample_rows: int = 12,
    text_limit: int = 220,
    enable_patcher: bool = True,
    patch_model: Optional[str] = None,
    patch_temperature: float = 0.2,
    patch_source_analysis_json: Optional[str | Path] = None,
    show_progress: bool = True,
) -> OrchestrateResult:
    project_root = resolve_project_root()

    current_version_name = normalize_version_name(current_version)
    next_version_name = normalize_version_name(next_version)

    if current_version_name == next_version_name:
        raise ValueError("当前版本号和下一版本号不能相同")

    script_path = resolve_version_script(project_root, current_version_name)

    if input_csv is None:
        input_csv = resolve_default_input_csv(project_root)
    else:
        input_csv = Path(input_csv).resolve()

    if gold_csv is None:
        gold_csv = resolve_default_gold_csv(project_root)
    else:
        gold_csv = Path(gold_csv).resolve()

    ensure_pipeline_dirs(project_root, current_version_name, next_version_name)
    paths = build_paths(project_root, current_version_name, next_version_name)
    candidate_paths = build_paths(project_root, next_version_name, next_version_name)

    total_steps = 5 + (6 if enable_patcher else 0)
    progress = StageProgress(
        total=total_steps,
        enabled=show_progress,
        desc=f"Pipeline {current_version_name}->{next_version_name}",
    )

    steps_completed = 0

    analysis_json = ""
    analysis_markdown = ""
    llm_payload_json = ""

    patched_script = ""
    patch_report_json = ""
    patch_report_markdown = ""
    patch_syntax_ok = False
    compare_winner = ""

    try:
        # 0) 先检查当前脚本是否存在
        if not script_path.exists():
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"当前版本脚本不存在: {script_path}",
                ),
                paths["orchestrate_json"],
            )

        # 1) runner
        run_rule_script, save_run_result = import_runner()
        run_result = run_rule_script(
            script_path=script_path,
            input_csv=input_csv,
            output_csv=paths["prediction_csv"],
            python_executable=python_executable,
            timeout=timeout,
        )
        save_run_result(run_result, paths["runlog_json"])

        if not run_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"runner 失败: {run_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("runner 完成")

        # 2) merger
        merge_gold_and_predictions = import_merger()
        merge_result = merge_gold_and_predictions(
            gold_csv=gold_csv,
            pred_csv=paths["prediction_csv"],
            merged_csv=paths["merged_csv"],
            log_json=paths["merge_log_json"],
        )

        if not merge_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"merger 失败: {merge_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("merger 完成")

        # 3) evaluator
        evaluate_merged_file = import_evaluator()
        eval_result = evaluate_merged_file(
            merged_csv=paths["merged_csv"],
            eval_json=paths["eval_json"],
            details_json=paths["eval_details_json"],
        )

        if not eval_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"evaluator 失败: {eval_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("evaluator 完成")

        # 4) slicer
        slice_errors = import_slicer()
        slice_result = slice_errors(
            merged_csv=paths["merged_csv"],
            false_positive_csv=paths["false_positive_csv"],
            false_negative_csv=paths["false_negative_csv"],
            type_mismatch_csv=paths["type_mismatch_csv"],
            score_diff_top_csv=paths["score_diff_top_csv"],
            log_json=paths["slice_log_json"],
            topn_score_diff=200,
        )

        if not slice_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"slicer 失败: {slice_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("slicer 完成")

        # 5) analyzer_llm：固定运行
        analyze_error_files_with_llm = import_analyzer_llm()
        ana_result = analyze_error_files_with_llm(
            false_positive_csv=paths["false_positive_csv"],
            false_negative_csv=paths["false_negative_csv"],
            type_mismatch_csv=paths["type_mismatch_csv"],
            score_diff_top_csv=paths["score_diff_top_csv"],
            analysis_json=paths["analysis_json_llm"],
            analysis_markdown=paths["analysis_markdown_llm"],
            llm_payload_json=paths["llm_payload_json"],
            model=model or "deepseek-chat",
            sample_rows=sample_rows,
            text_limit=text_limit,
            show_progress=True,
        )

        if not ana_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"analyzer_llm 失败: {ana_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        analysis_json = str(paths["analysis_json_llm"])
        analysis_markdown = str(paths["analysis_markdown_llm"])
        llm_payload_json = str(paths["llm_payload_json"])

        steps_completed += 1
        progress.update("analyzer_llm 完成")

        # 6) patcher：默认固定运行
        if enable_patcher:
            chosen_analysis_json = None

            if patch_source_analysis_json:
                chosen_analysis_json = Path(patch_source_analysis_json).resolve()
            elif analysis_json:
                chosen_analysis_json = Path(analysis_json).resolve()

            if chosen_analysis_json is None or not chosen_analysis_json.exists():
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        steps_completed=steps_completed,
                        success=False,
                        error_message="patcher 失败: 未找到可用的 analysis_json。",
                    ),
                    paths["orchestrate_json"],
                )

            patch_script_with_llm = import_patcher()
            patch_result = patch_script_with_llm(
                source_script=script_path,
                analysis_json=chosen_analysis_json,
                output_script=paths["patched_script"],
                patch_report_json=paths["patch_report_json"],
                patch_report_markdown=paths["patch_report_markdown"],
                model=patch_model or model or "deepseek-chat",
                temperature=patch_temperature,
                show_progress=True,
            )

            if not patch_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=str(paths["patched_script"]),
                        patch_report_json=str(paths["patch_report_json"]),
                        patch_report_markdown=str(paths["patch_report_markdown"]),
                        patch_syntax_ok=patch_result.syntax_ok,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"patcher 失败: {patch_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            patched_script = str(paths["patched_script"])
            patch_report_json = str(paths["patch_report_json"])
            patch_report_markdown = str(paths["patch_report_markdown"])
            patch_syntax_ok = patch_result.syntax_ok

            steps_completed += 1
            progress.update("patcher 完成")

            # 7) runner candidate
            candidate_run_result = run_rule_script(
                script_path=paths["patched_script"],
                input_csv=input_csv,
                output_csv=candidate_paths["prediction_csv"],
                python_executable=python_executable,
                timeout=timeout,
            )
            save_run_result(candidate_run_result, candidate_paths["runlog_json"])

            if not candidate_run_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=patched_script,
                        patch_report_json=patch_report_json,
                        patch_report_markdown=patch_report_markdown,
                        patch_syntax_ok=patch_syntax_ok,
                        candidate_paths=candidate_paths,
                        compare_winner=compare_winner,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"candidate runner 失败: {candidate_run_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            steps_completed += 1
            progress.update("candidate runner 完成")

            # 8) merger candidate
            candidate_merge_result = merge_gold_and_predictions(
                gold_csv=gold_csv,
                pred_csv=candidate_paths["prediction_csv"],
                merged_csv=candidate_paths["merged_csv"],
                log_json=candidate_paths["merge_log_json"],
            )

            if not candidate_merge_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=patched_script,
                        patch_report_json=patch_report_json,
                        patch_report_markdown=patch_report_markdown,
                        patch_syntax_ok=patch_syntax_ok,
                        candidate_paths=candidate_paths,
                        compare_winner=compare_winner,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"candidate merger 失败: {candidate_merge_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            steps_completed += 1
            progress.update("candidate merger 完成")

            # 9) evaluator candidate
            candidate_eval_result = evaluate_merged_file(
                merged_csv=candidate_paths["merged_csv"],
                eval_json=candidate_paths["eval_json"],
                details_json=candidate_paths["eval_details_json"],
            )

            if not candidate_eval_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=patched_script,
                        patch_report_json=patch_report_json,
                        patch_report_markdown=patch_report_markdown,
                        patch_syntax_ok=patch_syntax_ok,
                        candidate_paths=candidate_paths,
                        compare_winner=compare_winner,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"candidate evaluator 失败: {candidate_eval_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            steps_completed += 1
            progress.update("candidate evaluator 完成")

            # 10) slicer candidate
            candidate_slice_result = slice_errors(
                merged_csv=candidate_paths["merged_csv"],
                false_positive_csv=candidate_paths["false_positive_csv"],
                false_negative_csv=candidate_paths["false_negative_csv"],
                type_mismatch_csv=candidate_paths["type_mismatch_csv"],
                score_diff_top_csv=candidate_paths["score_diff_top_csv"],
                log_json=candidate_paths["slice_log_json"],
                topn_score_diff=200,
            )

            if not candidate_slice_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=patched_script,
                        patch_report_json=patch_report_json,
                        patch_report_markdown=patch_report_markdown,
                        patch_syntax_ok=patch_syntax_ok,
                        candidate_paths=candidate_paths,
                        compare_winner=compare_winner,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"candidate slicer 失败: {candidate_slice_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            steps_completed += 1
            progress.update("candidate slicer 完成")

            # 11) comparator
            compare_versions = import_comparator()
            compare_result = compare_versions(
                baseline_eval_json=paths["eval_json"],
                candidate_eval_json=candidate_paths["eval_json"],
                baseline_slice_log_json=paths["slice_log_json"],
                candidate_slice_log_json=candidate_paths["slice_log_json"],
                compare_json=paths["compare_json"],
                compare_markdown=paths["compare_markdown"],
                baseline_name=current_version_name,
                candidate_name=next_version_name,
            )

            compare_winner = compare_result.winner

            if not compare_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=patched_script,
                        patch_report_json=patch_report_json,
                        patch_report_markdown=patch_report_markdown,
                        patch_syntax_ok=patch_syntax_ok,
                        candidate_paths=candidate_paths,
                        compare_winner=compare_winner,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"comparator 失败: {compare_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            steps_completed += 1
            progress.update("comparator 完成")

        progress.close()

        return persist_and_return(
            build_result(
                current_version=current_version_name,
                next_version=next_version_name,
                script_path=script_path,
                input_csv=input_csv,
                gold_csv=gold_csv,
                paths=paths,
                analysis_json=analysis_json,
                analysis_markdown=analysis_markdown,
                llm_payload_json=llm_payload_json,
                patched_script=patched_script,
                patch_report_json=patch_report_json,
                patch_report_markdown=patch_report_markdown,
                patch_syntax_ok=patch_syntax_ok,
                candidate_paths=candidate_paths if enable_patcher else None,
                compare_winner=compare_winner,
                steps_completed=steps_completed,
                success=True,
                error_message="",
            ),
            paths["orchestrate_json"],
        )

    except Exception as e:
        progress.close()
        return persist_and_return(
            build_result(
                current_version=current_version_name,
                next_version=next_version_name,
                script_path=script_path,
                input_csv=input_csv,
                gold_csv=gold_csv,
                paths=paths,
                analysis_json=analysis_json,
                analysis_markdown=analysis_markdown,
                llm_payload_json=llm_payload_json,
                patched_script=patched_script,
                patch_report_json=patch_report_json,
                patch_report_markdown=patch_report_markdown,
                patch_syntax_ok=patch_syntax_ok,
                candidate_paths=candidate_paths if enable_patcher else None,
                compare_winner=compare_winner,
                steps_completed=steps_completed,
                success=False,
                error_message=f"orchestrator 异常: {e}",
            ),
            paths["orchestrate_json"],
        )


# =========================
# 主程序入口
# =========================
if __name__ == "__main__":
    current_version = input("请输入当前版本号：").strip()
    next_version = input("请输入下一版本号：").strip()

    result = orchestrate_pipeline(
        current_version=current_version,
        next_version=next_version,
        input_csv=INPUT_CSV,
        gold_csv=GOLD_CSV,
        python_executable=PYTHON_EXECUTABLE,
        timeout=TIMEOUT,
        model=MODEL,
        sample_rows=SAMPLE_ROWS,
        text_limit=TEXT_LIMIT,
        enable_patcher=ENABLE_PATCHER,
        patch_model=PATCH_MODEL,
        patch_temperature=PATCH_TEMPERATURE,
        patch_source_analysis_json=PATCH_SOURCE_ANALYSIS_JSON,
        show_progress=True,
    )

    print("=" * 60)
    print("ORCHESTRATE RESULT")
    print("=" * 60)
    print(f"success              : {result.success}")
    print(f"current_version      : {result.current_version}")
    print(f"next_version         : {result.next_version}")
    print(f"script_path          : {result.script_path}")
    print(f"input_csv            : {result.input_csv}")
    print(f"gold_csv             : {result.gold_csv}")

    print(f"prediction_csv       : {result.prediction_csv}")
    print(f"merged_csv           : {result.merged_csv}")
    print(f"eval_json            : {result.eval_json}")
    print(f"eval_details_json    : {result.eval_details_json}")

    print(f"false_positive_csv   : {result.false_positive_csv}")
    print(f"false_negative_csv   : {result.false_negative_csv}")
    print(f"type_mismatch_csv    : {result.type_mismatch_csv}")
    print(f"score_diff_top_csv   : {result.score_diff_top_csv}")
    print(f"slice_log_json       : {result.slice_log_json}")

    print(f"analysis_json        : {result.analysis_json}")
    print(f"analysis_markdown    : {result.analysis_markdown}")
    print(f"llm_payload_json     : {result.llm_payload_json}")

    print(f"patched_script       : {result.patched_script}")
    print(f"patch_report_json    : {result.patch_report_json}")
    print(f"patch_report_markdown: {result.patch_report_markdown}")
    print(f"patch_syntax_ok      : {result.patch_syntax_ok}")

    print(f"candidate_prediction_csv: {result.candidate_prediction_csv}")
    print(f"candidate_merged_csv    : {result.candidate_merged_csv}")
    print(f"candidate_eval_json     : {result.candidate_eval_json}")
    print(f"candidate_eval_details  : {result.candidate_eval_details_json}")
    print(f"candidate_slice_log     : {result.candidate_slice_log_json}")

    print(f"compare_json         : {result.compare_json}")
    print(f"compare_markdown     : {result.compare_markdown}")
    print(f"compare_winner       : {result.compare_winner}")

    print(f"orchestrate_json     : {result.orchestrate_json}")
    print(f"steps_completed      : {result.steps_completed}")

    if result.error_message:
        print(f"error_message        : {result.error_message}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
