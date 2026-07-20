from __future__ import annotations

import json
import hashlib
import shutil
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
    resolve_versions_root,
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
    focus_metric: str = ""
    candidate_accepted: bool = False
    promotion_decision: str = ""
    changed_rows_json: str = ""
    changed_rows_csv: str = ""
    patch_attempts: int = 0
    attempt_history_json: str = ""
    promotion_mode: str = "strict"
    relaxed_selected_attempt: int = 0
    promotion_warning: str = ""

    orchestrate_json: str = ""
    steps_completed: int = 0
    error_message: str = ""


def write_json(path: Path, data: dict) -> None:
    write_json_file(path, data)


def read_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_version_metric(
    project_root: Path,
    version_name: str,
    dataset_sha256: str = "",
) -> dict:
    version_dir = resolve_versions_root(project_root) / version_name
    eval_path = version_dir / "reports" / "evals" / f"risk_labeler_{version_name}_eval.json"
    slice_path = version_dir / "reports" / "errors" / f"risk_labeler_{version_name}_slice_log.json"

    eval_data = read_json_if_exists(eval_path)
    slice_data = read_json_if_exists(slice_path)
    score_metrics = eval_data.get("score_metrics", {}) or {}
    evaluation_context = eval_data.get("evaluation_context", {}) or {}
    report_dataset_sha256 = str(evaluation_context.get("gold_sha256", ""))
    if dataset_sha256 and report_dataset_sha256 != dataset_sha256:
        return {}

    return {
        "version": version_name,
        "false_positive_rows": slice_data.get("false_positive_rows", 0),
        "false_negative_rows": slice_data.get("false_negative_rows", 0),
        "type_mismatch_rows": slice_data.get("type_mismatch_rows", 0),
        "score_diff_top_rows": slice_data.get("score_diff_top_rows", 0),
        "score_diff_mean": score_metrics.get("mae", 0),
        "score_diff_rmse": score_metrics.get("rmse", 0),
        "gold_sha256": report_dataset_sha256,
    }


def build_recent_version_metrics(
    project_root: Path,
    current_version_name: str,
    dataset_sha256: str = "",
    history_window: int = 6,
) -> list[dict]:
    current_num = int(current_version_name.lstrip("v"))
    metrics: list[dict] = []
    start = max(1, current_num - max(1, history_window) + 1)
    for version_num in range(start, current_num + 1):
        metric = collect_version_metric(
            project_root,
            f"v{version_num}",
            dataset_sha256=dataset_sha256,
        )
        if metric:
            metrics.append(metric)
    return metrics


def choose_focus_metric(metrics: list[dict]) -> str:
    """Choose one coordinate for the next patch, prioritizing stalled metrics."""
    target_metrics = ["type_mismatch_rows", "false_positive_rows", "false_negative_rows"]
    if len(metrics) >= 3:
        for metric in target_metrics:
            values = [item.get(metric) for item in metrics[-4:]]
            values = [float(value) for value in values if isinstance(value, (int, float))]
            if len(values) >= 3 and values[-1] >= min(values[:-1]):
                return metric

    if not metrics:
        return ""
    current = metrics[-1]
    return max(target_metrics, key=lambda metric: float(current.get(metric, 0) or 0))


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


def import_impact_analyzer():
    from tools.impact_analyzer import analyze_candidate_impact
    return analyze_candidate_impact


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
    focus_metric: str = "",
    candidate_accepted: bool = False,
    promotion_decision: str = "",
    patch_attempts: int = 0,
    promotion_mode: str = "strict",
    relaxed_selected_attempt: int = 0,
    promotion_warning: str = "",
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
        focus_metric=focus_metric,
        candidate_accepted=candidate_accepted,
        promotion_decision=promotion_decision,
        changed_rows_json=str(paths.get("impact_json", "")),
        changed_rows_csv=str(paths.get("changed_rows_csv", "")),
        patch_attempts=patch_attempts,
        attempt_history_json=str(paths.get("attempt_history_json", "")),
        promotion_mode=promotion_mode,
        relaxed_selected_attempt=relaxed_selected_attempt,
        promotion_warning=promotion_warning,

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
def _orchestrate_pipeline_once(
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
    patch_revision_feedback: Optional[dict] = None,
    reuse_analysis: bool = False,
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
    gold_sha256 = sha256_file(gold_csv)
    evaluation_context = {
        "gold_csv": str(gold_csv),
        "gold_sha256": gold_sha256,
    }

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
    focus_metric = ""
    candidate_accepted = False
    promotion_decision = ""

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

        if enable_patcher and paths["patched_script"].exists():
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
                    error_message=(
                        f"目标版本脚本已存在，拒绝覆盖: {paths['patched_script']}。"
                        "请使用新的 next_version，或先人工处理已有版本。"
                    ),
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
            evaluation_context=evaluation_context,
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
        version_metrics = build_recent_version_metrics(
            project_root,
            current_version_name,
            dataset_sha256=gold_sha256,
        )
        focus_metric = choose_focus_metric(version_metrics)
        version_metrics_json = paths["analysis_json_llm"].with_name(
            f"risk_labeler_{current_version_name}_version_metrics.json"
        )
        if version_metrics:
            write_json(version_metrics_json, version_metrics)
        can_reuse_analysis = (
            reuse_analysis
            and paths["analysis_json_llm"].exists()
            and paths["analysis_markdown_llm"].exists()
            and paths["llm_payload_json"].exists()
        )
        if not can_reuse_analysis:
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
                version_metrics_json=version_metrics_json if version_metrics else None,
                source_script=script_path,
                slice_log_json=paths["slice_log_json"],
                focus_metric=focus_metric,
                merged_csv=paths["merged_csv"],
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
                output_script=paths["candidate_script"],
                patch_report_json=paths["patch_report_json"],
                patch_report_markdown=paths["patch_report_markdown"],
                model=patch_model or model or "deepseek-chat",
                temperature=patch_temperature,
                show_progress=True,
                revision_feedback=patch_revision_feedback,
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
                        patched_script=str(paths["candidate_script"]),
                        patch_report_json=str(paths["patch_report_json"]),
                        patch_report_markdown=str(paths["patch_report_markdown"]),
                        patch_syntax_ok=patch_result.syntax_ok,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"patcher 失败: {patch_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            patched_script = str(paths["candidate_script"])
            patch_report_json = str(paths["patch_report_json"])
            patch_report_markdown = str(paths["patch_report_markdown"])
            patch_syntax_ok = patch_result.syntax_ok

            steps_completed += 1
            progress.update("patcher 完成")

            # 7) runner candidate
            candidate_run_result = run_rule_script(
                script_path=paths["candidate_script"],
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

            # changed-row preflight: detect error conversion before full evaluation.
            analyze_candidate_impact = import_impact_analyzer()
            impact_result = analyze_candidate_impact(
                baseline_merged_csv=paths["merged_csv"],
                candidate_merged_csv=candidate_paths["merged_csv"],
                report_json=paths["impact_json"],
                changed_rows_csv=paths["changed_rows_csv"],
                analysis_json=chosen_analysis_json,
            )
            if not impact_result.success:
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
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"changed-row preflight 失败: {impact_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            if not impact_result.preflight_passed:
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
                        compare_winner=current_version_name,
                        focus_metric=focus_metric,
                        candidate_accepted=False,
                        promotion_decision="preflight_rejected",
                        steps_completed=steps_completed,
                        success=True,
                    ),
                    paths["orchestrate_json"],
                )

            # 9) evaluator candidate
            candidate_eval_result = evaluate_merged_file(
                merged_csv=candidate_paths["merged_csv"],
                eval_json=candidate_paths["eval_json"],
                details_json=candidate_paths["eval_details_json"],
                evaluation_context=evaluation_context,
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
                focus_metric=focus_metric,
            )

            compare_winner = compare_result.winner
            candidate_accepted = compare_result.candidate_accepted
            promotion_decision = compare_result.decision

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

            if candidate_accepted:
                paths["patched_script"].parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(paths["candidate_script"], paths["patched_script"])
                patched_script = str(paths["patched_script"])

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
                focus_metric=focus_metric,
                candidate_accepted=candidate_accepted,
                promotion_decision=promotion_decision,
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
                focus_metric=focus_metric,
                candidate_accepted=candidate_accepted,
                promotion_decision=promotion_decision,
                steps_completed=steps_completed,
                success=False,
                error_message=f"orchestrator 异常: {e}",
            ),
            paths["orchestrate_json"],
        )


def _copy_attempt_artifacts(result: OrchestrateResult, archive_dir: Path) -> list[str]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    artifact_fields = [
        "patched_script",
        "patch_report_json",
        "patch_report_markdown",
        "candidate_prediction_csv",
        "candidate_merged_csv",
        "candidate_eval_json",
        "candidate_eval_details_json",
        "candidate_false_positive_csv",
        "candidate_false_negative_csv",
        "candidate_type_mismatch_csv",
        "candidate_slice_log_json",
        "compare_json",
        "compare_markdown",
        "changed_rows_json",
        "changed_rows_csv",
    ]
    for field_name in artifact_fields:
        raw_path = getattr(result, field_name, "")
        if not raw_path:
            continue
        source = Path(raw_path)
        if not source.exists() or not source.is_file():
            continue
        destination = archive_dir / f"{field_name}{source.suffix}"
        shutil.copy2(source, destination)
        copied.append(str(destination))
    return copied


def _archive_preexisting_candidate(paths: dict[str, Path], candidate_paths: dict[str, Path]) -> dict:
    if paths["patched_script"].exists() or not paths["candidate_script"].exists():
        return {}
    attempts_root = paths["attempt_history_json"].parent / "attempts"
    archive_dir = attempts_root / "preexisting"
    suffix = 2
    while archive_dir.exists():
        archive_dir = attempts_root / f"preexisting_{suffix}"
        suffix += 1
    archive_dir.mkdir(parents=True, exist_ok=True)
    sources = {
        "candidate_script": paths["candidate_script"],
        "patch_report_json": paths["patch_report_json"],
        "patch_report_markdown": paths["patch_report_markdown"],
        "compare_json": paths["compare_json"],
        "compare_markdown": paths["compare_markdown"],
        "changed_rows_json": paths["impact_json"],
        "changed_rows_csv": paths["changed_rows_csv"],
        "candidate_prediction_csv": candidate_paths["prediction_csv"],
        "candidate_merged_csv": candidate_paths["merged_csv"],
        "candidate_eval_json": candidate_paths["eval_json"],
        "candidate_eval_details_json": candidate_paths["eval_details_json"],
        "candidate_false_positive_csv": candidate_paths["false_positive_csv"],
        "candidate_false_negative_csv": candidate_paths["false_negative_csv"],
        "candidate_type_mismatch_csv": candidate_paths["type_mismatch_csv"],
        "candidate_slice_log_json": candidate_paths["slice_log_json"],
    }
    copied = []
    for name, source in sources.items():
        if not source.exists() or not source.is_file():
            continue
        destination = archive_dir / f"{name}{source.suffix}"
        shutil.copy2(source, destination)
        copied.append(str(destination))
    return {
        "attempt": 0,
        "kind": "preexisting_rejected_candidate_backup",
        "archive_dir": str(archive_dir),
        "artifacts": copied,
    }


def _clear_candidate_scratch(paths: dict[str, Path], candidate_paths: dict[str, Path]) -> None:
    """Remove only known generated scratch files so retries cannot reuse stale reports."""
    generated = [
        paths["candidate_script"],
        paths["patch_report_json"],
        paths["patch_report_markdown"],
        paths["compare_json"],
        paths["compare_markdown"],
        paths["impact_json"],
        paths["changed_rows_csv"],
        candidate_paths["prediction_csv"],
        candidate_paths["runlog_json"],
        candidate_paths["merged_csv"],
        candidate_paths["merge_log_json"],
        candidate_paths["eval_json"],
        candidate_paths["eval_details_json"],
        candidate_paths["false_positive_csv"],
        candidate_paths["false_negative_csv"],
        candidate_paths["type_mismatch_csv"],
        candidate_paths["score_diff_top_csv"],
        candidate_paths["slice_log_json"],
    ]
    for path in generated:
        if path.exists() and path.is_file():
            path.unlink()


def _build_revision_feedback(result: OrchestrateResult, attempt: int) -> dict:
    impact = read_json_if_exists(Path(result.changed_rows_json)) if result.changed_rows_json else {}
    comparison = read_json_if_exists(Path(result.compare_json)) if result.compare_json else {}
    patch_report = read_json_if_exists(Path(result.patch_report_json)) if result.patch_report_json else {}
    rejected_mechanisms = []
    for change in patch_report.get("changes", []) or []:
        if not isinstance(change, dict):
            continue
        rejected_mechanisms.append(
            {
                "target": change.get("target", ""),
                "action": change.get("action", ""),
            }
        )
    return {
        "feedback_version": "patch_revision_feedback_v1",
        "rejected_attempt": attempt,
        "decision": result.promotion_decision,
        "focus_metric": result.focus_metric,
        "rejected_mechanisms": rejected_mechanisms,
        "changed_row_preflight": {
            "target_count_deltas": impact.get("target_count_deltas", {}),
            "transition_matrix": impact.get("transition_matrix", []),
            "violations": impact.get("violations", []),
            "top_changed_rows": (impact.get("top_changed_rows", []) or [])[:25],
        },
        "comparison": {
            "acceptance": comparison.get("acceptance", {}),
            "metric_results": comparison.get("metric_results", []),
            "slice_metric_results": comparison.get("slice_metric_results", []),
        },
        "revision_instruction": (
            "Do not repeat the rejected mechanism unchanged. Keep the same single "
            "optimization focus, directly eliminate every reported regression and "
            "forbidden transition, and preserve all patch_contract control rows."
        ),
    }


def _score_relaxed_candidate(record: dict, focus_metric: str = "") -> Optional[dict]:
    attempt = int(record.get("attempt", 0) or 0)
    archive_dir = Path(str(record.get("archive_dir", "")))
    script_path = archive_dir / "patched_script.py"
    if attempt <= 0 or not script_path.exists():
        return None

    impact = read_json_if_exists(archive_dir / "changed_rows_json.json")
    comparison = read_json_if_exists(archive_dir / "compare_json.json")
    deltas = impact.get("target_count_deltas", {}) or {}
    if not deltas:
        for item in comparison.get("acceptance", {}).get("metric_decisions", []) or []:
            metric = str(item.get("metric", "")).removesuffix("_rows")
            if metric:
                deltas[metric] = float(item.get("delta_for_candidate", 0) or 0)

    regression_total = sum(max(0.0, float(value or 0)) for value in deltas.values())
    improvement_total = sum(max(0.0, -float(value or 0)) for value in deltas.values())
    focus_key = focus_metric.removesuffix("_rows")
    focus_improvement = max(0.0, -float(deltas.get(focus_key, 0) or 0))

    acceptance = comparison.get("acceptance", {}) or {}
    quality_regressions = len(acceptance.get("quality_regressions", []) or [])
    baseline_quality = comparison.get("baseline_summary", {}) or {}
    candidate_quality = comparison.get("candidate_summary", {}) or {}
    quality_known = bool(baseline_quality and candidate_quality) and impact.get(
        "preflight_passed", True
    ) is not False
    quality_gain = 0.0
    if quality_known:
        quality_gain += float(candidate_quality.get("label_accuracy", 0) or 0) - float(
            baseline_quality.get("label_accuracy", 0) or 0
        )
        quality_gain += float(candidate_quality.get("primary_type_accuracy", 0) or 0) - float(
            baseline_quality.get("primary_type_accuracy", 0) or 0
        )
        quality_gain += (
            float(baseline_quality.get("score_mae", 0) or 0)
            - float(candidate_quality.get("score_mae", 0) or 0)
        ) / 100.0
        quality_gain += (
            float(baseline_quality.get("score_rmse", 0) or 0)
            - float(candidate_quality.get("score_rmse", 0) or 0)
        ) / 100.0

    # Lexicographic policy: first avoid target regressions, then avoid measured
    # quality regressions, then prefer evaluated candidates and useful gains.
    rank = [
        round(regression_total, 8),
        quality_regressions,
        0 if quality_known else 1,
        round(-focus_improvement, 8),
        round(-improvement_total, 8),
        round(-quality_gain, 8),
        attempt,
    ]
    return {
        "attempt": attempt,
        "archive_dir": str(archive_dir),
        "script_path": str(script_path),
        "target_count_deltas": deltas,
        "target_regression_total": regression_total,
        "target_improvement_total": improvement_total,
        "focus_improvement": focus_improvement,
        "quality_regressions": quality_regressions,
        "quality_known": quality_known,
        "quality_gain": round(quality_gain, 8),
        "rank": rank,
    }


def promote_best_rejected_candidate(
    paths: dict[str, Path],
    attempt_history: list[dict],
    focus_metric: str = "",
    reason: str = "user_requested_relaxed_promotion",
) -> dict:
    scored = [
        candidate
        for record in attempt_history
        if (candidate := _score_relaxed_candidate(record, focus_metric)) is not None
    ]
    if not scored:
        return {"success": False, "error": "no archived candidate script is available"}
    selected = min(scored, key=lambda item: tuple(item["rank"]))
    source_script = Path(selected["script_path"])
    paths["patched_script"].parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_script, paths["patched_script"])
    report = {
        "success": True,
        "policy": "relaxed_best_candidate_v1",
        "reason": reason,
        "warning": (
            "This candidate did not pass the strict constrained-Pareto gate. "
            "It was promoted by an explicit relaxed-policy override."
        ),
        "selected_attempt": selected["attempt"],
        "selected": selected,
        "all_candidates": sorted(scored, key=lambda item: tuple(item["rank"])),
        "promoted_script": str(paths["patched_script"]),
    }
    write_json(paths["relaxed_promotion_json"], report)
    return report


def promote_existing_rejected_version(
    current_version: str | int,
    next_version: str | int,
    reason: str = "user_requested_relaxed_promotion",
) -> dict:
    """Promote the safest archived rejected attempt without rerunning any LLM."""
    project_root = resolve_project_root()
    current_name = normalize_version_name(current_version)
    next_name = normalize_version_name(next_version)
    paths = build_paths(project_root, current_name, next_name)
    history = read_json_if_exists(paths["attempt_history_json"])
    orchestrate_report = read_json_if_exists(paths["orchestrate_json"])
    attempts = history.get("attempts", []) if isinstance(history, dict) else []
    report = promote_best_rejected_candidate(
        paths=paths,
        attempt_history=attempts,
        focus_metric=str(
            history.get("focus_metric", "")
            or orchestrate_report.get("focus_metric", "")
        ),
        reason=reason,
    )
    if not report.get("success"):
        return report

    history["candidate_accepted"] = True
    history["promotion_mode"] = "relaxed"
    history["relaxed_promotion"] = report
    write_json(paths["attempt_history_json"], history)

    orchestrate_report.update(
        {
            "candidate_accepted": True,
            "promotion_decision": "relaxed_override",
            "compare_winner": next_name,
            "promotion_mode": "relaxed",
            "relaxed_selected_attempt": report.get("selected_attempt", 0),
            "promotion_warning": report.get("warning", ""),
            "patched_script": str(paths["patched_script"]),
            "attempt_history_json": str(paths["attempt_history_json"]),
        }
    )
    write_json(paths["orchestrate_json"], orchestrate_report)
    return report


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
    patch_revision_retries: int = 2,
    relaxed_promotion: bool = False,
) -> OrchestrateResult:
    """Run one version step and revise a rejected patch up to two times by default."""
    project_root = resolve_project_root()
    current_name = normalize_version_name(current_version)
    next_name = normalize_version_name(next_version)
    paths = build_paths(project_root, current_name, next_name)
    candidate_paths = build_paths(project_root, next_name, next_name)
    max_attempts = 1 + max(0, int(patch_revision_retries)) if enable_patcher else 1
    attempt_history: list[dict] = []
    preexisting = _archive_preexisting_candidate(paths, candidate_paths) if enable_patcher else {}
    if preexisting:
        attempt_history.append(preexisting)
    feedback: Optional[dict] = None
    result: Optional[OrchestrateResult] = None

    for attempt in range(1, max_attempts + 1):
        if not paths["patched_script"].exists():
            _clear_candidate_scratch(paths, candidate_paths)
        result = _orchestrate_pipeline_once(
            current_version=current_version,
            next_version=next_version,
            input_csv=input_csv,
            gold_csv=gold_csv,
            python_executable=python_executable,
            timeout=timeout,
            model=model,
            sample_rows=sample_rows,
            text_limit=text_limit,
            enable_patcher=enable_patcher,
            patch_model=patch_model,
            patch_temperature=patch_temperature,
            patch_source_analysis_json=patch_source_analysis_json,
            show_progress=show_progress,
            patch_revision_feedback=feedback,
            reuse_analysis=attempt > 1,
        )
        result.patch_attempts = attempt
        archive_dir = (
            resolve_versions_root(project_root)
            / next_name
            / "reports"
            / "orchestrations"
            / "attempts"
            / f"attempt_{attempt}"
        )
        copied = _copy_attempt_artifacts(result, archive_dir)
        attempt_record = {
            "attempt": attempt,
            "success": result.success,
            "candidate_accepted": result.candidate_accepted,
            "promotion_decision": result.promotion_decision,
            "focus_metric": result.focus_metric,
            "error_message": result.error_message,
            "archive_dir": str(archive_dir),
            "artifacts": copied,
        }
        attempt_history.append(attempt_record)

        if not result.success or not enable_patcher or result.candidate_accepted:
            break
        if attempt >= max_attempts:
            break

        feedback = _build_revision_feedback(result, attempt)
        write_json(
            archive_dir / "revision_feedback.json",
            feedback,
        )

    assert result is not None
    relaxed_report: dict = {}
    result.promotion_mode = "relaxed" if relaxed_promotion else "strict"
    if (
        relaxed_promotion
        and enable_patcher
        and result.success
        and not result.candidate_accepted
    ):
        relaxed_report = promote_best_rejected_candidate(
            paths=paths,
            attempt_history=attempt_history,
            focus_metric=result.focus_metric,
        )
        if relaxed_report.get("success"):
            selected_attempt = int(relaxed_report.get("selected_attempt", 0) or 0)
            selected_dir = Path(relaxed_report["selected"]["archive_dir"])
            result.candidate_accepted = True
            result.promotion_decision = "relaxed_override"
            result.compare_winner = next_name
            result.relaxed_selected_attempt = selected_attempt
            result.promotion_warning = str(relaxed_report.get("warning", ""))
            result.patched_script = str(paths["patched_script"])
            archived_fields = {
                "patch_report_json": "patch_report_json.json",
                "patch_report_markdown": "patch_report_markdown.md",
                "candidate_prediction_csv": "candidate_prediction_csv.csv",
                "candidate_merged_csv": "candidate_merged_csv.csv",
                "candidate_eval_json": "candidate_eval_json.json",
                "candidate_eval_details_json": "candidate_eval_details_json.json",
                "candidate_false_positive_csv": "candidate_false_positive_csv.csv",
                "candidate_false_negative_csv": "candidate_false_negative_csv.csv",
                "candidate_type_mismatch_csv": "candidate_type_mismatch_csv.csv",
                "candidate_slice_log_json": "candidate_slice_log_json.json",
                "compare_json": "compare_json.json",
                "compare_markdown": "compare_markdown.md",
                "changed_rows_json": "changed_rows_json.json",
                "changed_rows_csv": "changed_rows_csv.csv",
            }
            for field_name, filename in archived_fields.items():
                archived_path = selected_dir / filename
                if archived_path.exists():
                    setattr(result, field_name, str(archived_path))

    history_report = {
        "policy": "patch_revision_loop_v1",
        "max_attempts": max_attempts,
        "attempts_used": result.patch_attempts,
        "candidate_accepted": result.candidate_accepted,
        "focus_metric": result.focus_metric,
        "promotion_mode": result.promotion_mode,
        "relaxed_promotion": relaxed_report,
        "attempts": attempt_history,
    }
    write_json(paths["attempt_history_json"], history_report)
    result.attempt_history_json = str(paths["attempt_history_json"])
    persist_and_return(result, paths["orchestrate_json"])
    return result


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
    print(f"focus_metric         : {result.focus_metric}")
    print(f"candidate_accepted   : {result.candidate_accepted}")
    print(f"promotion_decision   : {result.promotion_decision}")

    print(f"orchestrate_json     : {result.orchestrate_json}")
    print(f"steps_completed      : {result.steps_completed}")

    if result.error_message:
        print(f"error_message        : {result.error_message}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
