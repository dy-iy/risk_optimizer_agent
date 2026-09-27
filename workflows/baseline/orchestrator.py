from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .tools.common import StageProgress, write_json_file
from .tools.metrics import collect_recent_version_metrics
from .tools.paths import (
    build_paths,
    ensure_pipeline_dirs,
    normalize_version_name,
    resolve_default_gold_csv,
    resolve_default_input_csv,
    resolve_project_root,
    resolve_version_script,
)
from .tools.pipeline_stages import run_quality_stages
from .tools.workflow import workflow_step_count


INPUT_CSV: Optional[str] = None
GOLD_CSV: Optional[str] = None
WORKFLOW_NAME = "baseline"

PYTHON_EXECUTABLE: Optional[str] = None
TIMEOUT = 300

MODEL = "deepseek-v4-pro"
SAMPLE_ROWS = 12
TEXT_LIMIT = 220

ENABLE_PATCHER = True
PATCH_MODEL: Optional[str] = None
PATCH_TEMPERATURE = 0.2
PATCH_SOURCE_ANALYSIS_JSON: Optional[str] = None


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


def import_analyzer_llm():
    from .analyzer_llm import analyze_error_files_with_llm

    return analyze_error_files_with_llm


def import_patcher():
    from .patcher_llm_v2 import patch_script_with_llm

    return patch_script_with_llm


def import_comparator():
    from .tools.comparator import compare_versions

    return compare_versions


def _resolve_optional_path(path: str | Path | None, default: Path) -> Path:
    return default if path is None else Path(path).resolve()


def _persist(result: OrchestrateResult, save_path: Path) -> OrchestrateResult:
    write_json_file(save_path, asdict(result))
    return result


def _stringify_candidate_paths(candidate_paths: dict[str, Path] | None) -> dict[str, str]:
    candidate_paths = candidate_paths or {}
    return {
        "candidate_prediction_csv": str(candidate_paths.get("prediction_csv", "")),
        "candidate_merged_csv": str(candidate_paths.get("merged_csv", "")),
        "candidate_eval_json": str(candidate_paths.get("eval_json", "")),
        "candidate_eval_details_json": str(candidate_paths.get("eval_details_json", "")),
        "candidate_false_positive_csv": str(candidate_paths.get("false_positive_csv", "")),
        "candidate_false_negative_csv": str(candidate_paths.get("false_negative_csv", "")),
        "candidate_type_mismatch_csv": str(candidate_paths.get("type_mismatch_csv", "")),
        "candidate_score_diff_top_csv": str(candidate_paths.get("score_diff_top_csv", "")),
        "candidate_slice_log_json": str(candidate_paths.get("slice_log_json", "")),
    }


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
        compare_json=str(paths.get("compare_json", "")),
        compare_markdown=str(paths.get("compare_markdown", "")),
        compare_winner=compare_winner,
        orchestrate_json=str(paths["orchestrate_json"]),
        steps_completed=steps_completed,
        error_message=error_message,
        **_stringify_candidate_paths(candidate_paths),
    )


def _fail(
    *,
    paths: dict[str, Path],
    current_version: str,
    next_version: str,
    script_path: Path,
    input_csv: Path,
    gold_csv: Path,
    steps_completed: int,
    error_message: str,
    analysis_json: str = "",
    analysis_markdown: str = "",
    llm_payload_json: str = "",
    patched_script: str = "",
    patch_report_json: str = "",
    patch_report_markdown: str = "",
    patch_syntax_ok: bool = False,
    candidate_paths: dict[str, Path] | None = None,
    compare_winner: str = "",
) -> OrchestrateResult:
    return _persist(
        build_result(
            current_version=current_version,
            next_version=next_version,
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
            error_message=error_message,
        ),
        paths["orchestrate_json"],
    )


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
    workflow_name: str = WORKFLOW_NAME,
) -> OrchestrateResult:
    project_root = resolve_project_root()
    current_version_name = normalize_version_name(current_version)
    next_version_name = normalize_version_name(next_version)

    if current_version_name == next_version_name:
        raise ValueError("current_version and next_version must be different")

    script_path = resolve_version_script(project_root, current_version_name, workflow_name)
    input_path = _resolve_optional_path(input_csv, resolve_default_input_csv(project_root))
    gold_path = _resolve_optional_path(gold_csv, resolve_default_gold_csv(project_root))

    ensure_pipeline_dirs(project_root, current_version_name, next_version_name, workflow_name)
    paths = build_paths(project_root, current_version_name, next_version_name, workflow_name)
    candidate_paths = build_paths(project_root, next_version_name, next_version_name, workflow_name)

    progress = StageProgress(
        total=workflow_step_count(enable_patcher),
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
        fail_context = {
            "paths": paths,
            "current_version": current_version_name,
            "next_version": next_version_name,
            "script_path": script_path,
            "input_csv": input_path,
            "gold_csv": gold_path,
        }

        if not script_path.exists():
            progress.close()
            return _fail(
                **fail_context,
                steps_completed=steps_completed,
                error_message=f"current version script not found: {script_path}",
            )

        quality_result = run_quality_stages(
            script_path=script_path,
            input_csv=input_path,
            gold_csv=gold_path,
            paths=paths,
            python_executable=python_executable,
            timeout=timeout,
            progress=progress,
        )
        steps_completed += quality_result.steps_completed
        if not quality_result.success:
            progress.close()
            return _fail(
                **fail_context,
                steps_completed=steps_completed,
                error_message=quality_result.error_message,
            )

        analyze_error_files_with_llm = import_analyzer_llm()
        version_metrics = collect_recent_version_metrics(project_root, current_version_name, workflow_name)
        version_metrics_json = paths["analysis_json_llm"].with_name(
            f"risk_labeler_{current_version_name}_version_metrics.json"
        )
        if version_metrics:
            write_json_file(version_metrics_json, version_metrics)

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
        )
        if not ana_result.success:
            progress.close()
            return _fail(
                **fail_context,
                steps_completed=steps_completed,
                error_message=f"analyzer_llm failed: {ana_result.error_message}",
            )

        analysis_json = str(paths["analysis_json_llm"])
        analysis_markdown = str(paths["analysis_markdown_llm"])
        llm_payload_json = str(paths["llm_payload_json"])

        steps_completed += 1
        progress.update("analyzer_llm completed")

        if enable_patcher:
            chosen_analysis_json = (
                Path(patch_source_analysis_json).resolve()
                if patch_source_analysis_json
                else Path(analysis_json).resolve()
            )

            if not chosen_analysis_json.exists():
                progress.close()
                return _fail(
                    **fail_context,
                    analysis_json=analysis_json,
                    analysis_markdown=analysis_markdown,
                    llm_payload_json=llm_payload_json,
                    steps_completed=steps_completed,
                    error_message="patcher failed: analysis_json is unavailable",
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

            patched_script = str(paths["patched_script"])
            patch_report_json = str(paths["patch_report_json"])
            patch_report_markdown = str(paths["patch_report_markdown"])
            patch_syntax_ok = patch_result.syntax_ok

            if not patch_result.success:
                progress.close()
                return _fail(
                    **fail_context,
                    analysis_json=analysis_json,
                    analysis_markdown=analysis_markdown,
                    llm_payload_json=llm_payload_json,
                    patched_script=patched_script,
                    patch_report_json=patch_report_json,
                    patch_report_markdown=patch_report_markdown,
                    patch_syntax_ok=patch_syntax_ok,
                    steps_completed=steps_completed,
                    error_message=f"patcher failed: {patch_result.error_message}",
                )

            steps_completed += 1
            progress.update("patcher completed")

            candidate_quality = run_quality_stages(
                script_path=paths["patched_script"],
                input_csv=input_path,
                gold_csv=gold_path,
                paths=candidate_paths,
                python_executable=python_executable,
                timeout=timeout,
                progress=progress,
                stage_prefix="candidate ",
            )
            steps_completed += candidate_quality.steps_completed
            if not candidate_quality.success:
                progress.close()
                return _fail(
                    **fail_context,
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
                    error_message=candidate_quality.error_message,
                )

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
                return _fail(
                    **fail_context,
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
                    error_message=f"comparator failed: {compare_result.error_message}",
                )

            steps_completed += 1
            progress.update("comparator completed")

        progress.close()
        return _persist(
            build_result(
                current_version=current_version_name,
                next_version=next_version_name,
                script_path=script_path,
                input_csv=input_path,
                gold_csv=gold_path,
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
            ),
            paths["orchestrate_json"],
        )

    except Exception as exc:
        progress.close()
        return _fail(
            paths=paths,
            current_version=current_version_name,
            next_version=next_version_name,
            script_path=script_path,
            input_csv=input_path,
            gold_csv=gold_path,
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
            error_message=f"orchestrator exception: {exc}",
        )


def _print_result(result: OrchestrateResult) -> None:
    print("=" * 60)
    print("ORCHESTRATE RESULT")
    print("=" * 60)
    for key, value in asdict(result).items():
        print(f"{key:28}: {value}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    current_version = input("current version: ").strip()
    next_version = input("next version: ").strip()

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
        workflow_name=WORKFLOW_NAME,
    )
    _print_result(result)
