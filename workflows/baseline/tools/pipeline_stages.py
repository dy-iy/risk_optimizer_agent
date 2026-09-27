from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .common import StageProgress
from .evaluator import evaluate_merged_file
from .merger import merge_gold_and_predictions
from .runner import run_rule_script, save_run_result
from .slicer import slice_errors


@dataclass(frozen=True)
class QualityStageResult:
    success: bool
    steps_completed: int
    error_message: str = ""


def _stage_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}".strip()


def _update_progress(progress: StageProgress | None, prefix: str, name: str) -> None:
    if progress is not None:
        progress.update(f"{_stage_name(prefix, name)} completed")


def run_quality_stages(
    *,
    script_path: str | Path,
    input_csv: str | Path,
    gold_csv: str | Path,
    paths: dict[str, Path],
    python_executable: Optional[str] = None,
    timeout: int = 300,
    progress: StageProgress | None = None,
    stage_prefix: str = "",
    topn_score_diff: int = 200,
) -> QualityStageResult:
    """Run the deterministic quality stages for one rule-script version."""
    steps_completed = 0

    run_result = run_rule_script(
        script_path=script_path,
        input_csv=input_csv,
        output_csv=paths["prediction_csv"],
        python_executable=python_executable,
        timeout=timeout,
    )
    save_run_result(run_result, paths["runlog_json"])
    if not run_result.success:
        stage = _stage_name(stage_prefix, "runner")
        return QualityStageResult(False, steps_completed, f"{stage} failed: {run_result.error_message}")

    steps_completed += 1
    _update_progress(progress, stage_prefix, "runner")

    merge_result = merge_gold_and_predictions(
        gold_csv=gold_csv,
        pred_csv=paths["prediction_csv"],
        merged_csv=paths["merged_csv"],
        log_json=paths["merge_log_json"],
    )
    if not merge_result.success:
        stage = _stage_name(stage_prefix, "merger")
        return QualityStageResult(False, steps_completed, f"{stage} failed: {merge_result.error_message}")

    steps_completed += 1
    _update_progress(progress, stage_prefix, "merger")

    eval_result = evaluate_merged_file(
        merged_csv=paths["merged_csv"],
        eval_json=paths["eval_json"],
        details_json=paths["eval_details_json"],
    )
    if not eval_result.success:
        stage = _stage_name(stage_prefix, "evaluator")
        return QualityStageResult(False, steps_completed, f"{stage} failed: {eval_result.error_message}")

    steps_completed += 1
    _update_progress(progress, stage_prefix, "evaluator")

    slice_result = slice_errors(
        merged_csv=paths["merged_csv"],
        false_positive_csv=paths["false_positive_csv"],
        false_negative_csv=paths["false_negative_csv"],
        type_mismatch_csv=paths["type_mismatch_csv"],
        score_diff_top_csv=paths["score_diff_top_csv"],
        log_json=paths["slice_log_json"],
        topn_score_diff=topn_score_diff,
    )
    if not slice_result.success:
        stage = _stage_name(stage_prefix, "slicer")
        return QualityStageResult(False, steps_completed, f"{stage} failed: {slice_result.error_message}")

    steps_completed += 1
    _update_progress(progress, stage_prefix, "slicer")

    return QualityStageResult(True, steps_completed)
