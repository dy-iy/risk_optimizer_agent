from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
VERSIONS_ROOT = EXPERIMENT_ROOT / "versions"
INPUT_CSV = PROJECT_ROOT / "data" / "input" / "raw_1000_news.csv"
GOLD_CSV = PROJECT_ROOT / "data" / "gold" / "crypto_news_risk_gold_1000.csv"

# 新实验的默认运行配置。直接修改这里即可，无须传命令行参数。
DEFAULT_START_VERSION = 11
DEFAULT_ITERATIONS = 5


def configure_project_imports() -> None:
    os.chdir(PROJECT_ROOT)
    project_text = str(PROJECT_ROOT)
    if project_text not in sys.path:
        sys.path.insert(0, project_text)


def normalize_version_name(version: str | int) -> str:
    text = str(version).strip().lower()
    if text.startswith("v"):
        text = text[1:]
    if not text.isdigit() or int(text) < 1:
        raise ValueError(f"非法版本号: {version}")
    return f"v{int(text)}"


def version_script(version_name: str) -> Path:
    return VERSIONS_ROOT / version_name / "scripts" / f"risk_labeler_{version_name}.py"


def ensure_version_dirs(version_name: str) -> None:
    version_dir = VERSIONS_ROOT / version_name
    for relative in (
        "scripts",
        "reports/predictions",
        "reports/merged",
        "reports/evals",
        "reports/errors",
        "reports/analysis",
        "reports/patched",
        "reports/comparisons",
        "reports/orchestrations",
    ):
        (version_dir / relative).mkdir(parents=True, exist_ok=True)


def experiment_build_paths(
    project_root: Path,
    current_version: str,
    next_version: str,
) -> dict[str, Path]:
    del project_root
    current_dir = VERSIONS_ROOT / current_version
    next_dir = VERSIONS_ROOT / next_version
    return {
        "prediction_csv": current_dir / "reports" / "predictions" / f"risk_labeler_{current_version}_output.csv",
        "runlog_json": current_dir / "reports" / "predictions" / f"risk_labeler_{current_version}_runlog.json",
        "merged_csv": current_dir / "reports" / "merged" / f"risk_labeler_{current_version}_merged.csv",
        "merge_log_json": current_dir / "reports" / "merged" / f"risk_labeler_{current_version}_merge_log.json",
        "eval_json": current_dir / "reports" / "evals" / f"risk_labeler_{current_version}_eval.json",
        "eval_details_json": current_dir / "reports" / "evals" / f"risk_labeler_{current_version}_eval_details.json",
        "false_positive_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_false_positive.csv",
        "false_negative_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_false_negative.csv",
        "type_mismatch_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_type_mismatch.csv",
        "score_diff_top_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_score_diff_top.csv",
        "slice_log_json": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_slice_log.json",
        "analysis_json_llm": current_dir / "reports" / "analysis" / f"risk_labeler_{current_version}_analysis_llm.json",
        "analysis_markdown_llm": current_dir / "reports" / "analysis" / f"risk_labeler_{current_version}_analysis_llm.md",
        "llm_payload_json": current_dir / "reports" / "analysis" / f"risk_labeler_{current_version}_analysis_llm_payload.json",
        "patched_script": next_dir / "scripts" / f"risk_labeler_{next_version}.py",
        "patch_report_json": next_dir / "reports" / "patched" / f"risk_labeler_{next_version}_patch_report.json",
        "patch_report_markdown": next_dir / "reports" / "patched" / f"risk_labeler_{next_version}_patch_report.md",
        "compare_json": next_dir / "reports" / "comparisons" / f"{current_version}_vs_{next_version}_compare.json",
        "compare_markdown": next_dir / "reports" / "comparisons" / f"{current_version}_vs_{next_version}_compare.md",
        "orchestrate_json": next_dir / "reports" / "orchestrations" / f"{current_version}_to_{next_version}_orchestrate.json",
    }


def experiment_collect_version_metric(project_root: Path, version_name: str) -> dict:
    del project_root
    version_dir = VERSIONS_ROOT / version_name
    eval_path = version_dir / "reports" / "evals" / f"risk_labeler_{version_name}_eval.json"
    slice_path = version_dir / "reports" / "errors" / f"risk_labeler_{version_name}_slice_log.json"

    eval_data = (
        json.loads(eval_path.read_text(encoding="utf-8-sig"))
        if eval_path.exists()
        else {}
    )
    slice_data = (
        json.loads(slice_path.read_text(encoding="utf-8-sig"))
        if slice_path.exists()
        else {}
    )
    score_metrics = eval_data.get("score_metrics", {}) or {}
    return {
        "version": version_name,
        "false_positive_rows": slice_data.get("false_positive_rows", 0),
        "false_negative_rows": slice_data.get("false_negative_rows", 0),
        "type_mismatch_rows": slice_data.get("type_mismatch_rows", 0),
        "score_diff_top_rows": slice_data.get("score_diff_top_rows", 0),
        "score_diff_mean": score_metrics.get("mae", 0),
        "score_diff_rmse": score_metrics.get("rmse", 0),
    }


def configure_original_workflow():
    configure_project_imports()
    import orchestrator

    # Only redirect version storage. All runner/analyzer/patcher/comparator logic
    # continues to come from the restored original workflow.
    orchestrator.resolve_version_script = lambda project_root, version_name: version_script(version_name)
    orchestrator.ensure_pipeline_dirs = (
        lambda project_root, current_version, next_version: (
            ensure_version_dirs(current_version),
            ensure_version_dirs(next_version),
        )
    )
    orchestrator.build_paths = experiment_build_paths
    orchestrator.collect_version_metric = experiment_collect_version_metric
    return orchestrator


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是大于等于 1 的整数")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用新 Gold 数据运行原始迭代 workflow")
    parser.add_argument(
        "--start-version",
        type=positive_int,
        default=DEFAULT_START_VERSION,
        help=f"起始版本号（默认: {DEFAULT_START_VERSION}）",
    )
    parser.add_argument(
        "--iterations",
        type=positive_int,
        default=DEFAULT_ITERATIONS,
        help=f"迭代轮数（默认: {DEFAULT_ITERATIONS}）",
    )
    parser.add_argument(
        "--overwrite-next",
        action="store_true",
        help="允许覆盖已存在的下一版本脚本",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查路径和配置，不调用 workflow",
    )
    return parser.parse_args()


def validate_inputs(start_version: int) -> None:
    missing = [path for path in (INPUT_CSV, GOLD_CSV, version_script(f"v{start_version}")) if not path.exists()]
    if missing:
        raise FileNotFoundError("缺少运行文件:\n" + "\n".join(str(path) for path in missing))


def save_batch_report(report: dict) -> Path:
    output_dir = EXPERIMENT_ROOT / "batch_runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    validate_inputs(args.start_version)

    print(f"项目目录 : {PROJECT_ROOT}")
    print(f"实验目录 : {EXPERIMENT_ROOT}")
    print(f"输入数据 : {INPUT_CSV}")
    print(f"Gold数据 : {GOLD_CSV}")
    print(f"起始脚本 : {version_script(f'v{args.start_version}')}")
    print(f"迭代轮数 : {args.iterations}")
    if args.dry_run:
        print("dry-run 检查通过，未执行 workflow。")
        return

    orchestrator = configure_original_workflow()
    report = {
        "workflow": "original_git_workflow",
        "experiment_root": str(EXPERIMENT_ROOT),
        "versions_root": str(VERSIONS_ROOT),
        "input_csv": str(INPUT_CSV),
        "gold_csv": str(GOLD_CSV),
        "start_version": args.start_version,
        "requested_iterations": args.iterations,
        "results": [],
    }

    for current_number in range(args.start_version, args.start_version + args.iterations):
        next_number = current_number + 1
        next_script = version_script(f"v{next_number}")
        if next_script.exists() and not args.overwrite_next:
            raise FileExistsError(
                f"下一版本已存在，拒绝覆盖: {next_script}\n"
                "如确认覆盖，请添加 --overwrite-next"
            )

        print(f"\n{'=' * 72}\nv{current_number} -> v{next_number}\n{'=' * 72}")
        result = orchestrator.orchestrate_pipeline(
            current_version=current_number,
            next_version=next_number,
            input_csv=INPUT_CSV,
            gold_csv=GOLD_CSV,
            python_executable=sys.executable,
            timeout=orchestrator.TIMEOUT,
            model=orchestrator.MODEL,
            sample_rows=orchestrator.SAMPLE_ROWS,
            text_limit=orchestrator.TEXT_LIMIT,
            enable_patcher=orchestrator.ENABLE_PATCHER,
            patch_model=orchestrator.PATCH_MODEL,
            patch_temperature=orchestrator.PATCH_TEMPERATURE,
            patch_source_analysis_json=orchestrator.PATCH_SOURCE_ANALYSIS_JSON,
            show_progress=True,
        )
        report["results"].append(asdict(result))
        print(f"success        : {result.success}")
        print(f"compare_winner : {result.compare_winner}")
        print(f"next_script    : {result.patched_script}")
        if not result.success:
            print(f"error_message  : {result.error_message}")
            break

    report_path = save_batch_report(report)
    print(f"\n批次报告: {report_path}")


if __name__ == "__main__":
    main()
