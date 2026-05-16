from __future__ import annotations

from pathlib import Path


def resolve_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "tools").is_dir() and (parent / "data").is_dir():
            return parent
    return current.parents[2]


def normalize_version_name(version: str | int) -> str:
    version_text = str(version).strip().lower()
    if version_text.startswith("v"):
        version_text = version_text[1:]
    if not version_text.isdigit():
        raise ValueError(f"版本号不合法: {version}")
    return f"v{version_text}"


def resolve_version_script(project_root: Path, version_name: str) -> Path:
    candidates = [
        project_root / "versions" / version_name / "scripts" / f"risk_labeler_{version_name}.py",
        project_root / f"risk_labeler_{version_name}.py",
        project_root / "src" / f"risk_labeler_{version_name}.py",
        project_root / "app" / f"risk_labeler_{version_name}.py",
    ]
    for path in candidates:
        if path.exists():
            return path

    return project_root / "versions" / version_name / "scripts" / f"risk_labeler_{version_name}.py"


def resolve_default_input_csv(project_root: Path) -> Path:
    return project_root / "data" / "input" / "raw_1000_news.csv"


def resolve_default_gold_csv(project_root: Path) -> Path:
    return project_root / "data" / "gold" / "cleared_news_v2_deepseek_1000_labeled.csv"


def ensure_version_dirs(project_root: Path, version_name: str) -> None:
    version_dir = project_root / "versions" / version_name
    for relative_dir in [
        "scripts",
        "reports/predictions",
        "reports/merged",
        "reports/evals",
        "reports/errors",
        "reports/analysis",
        "reports/patched",
        "reports/comparisons",
        "reports/orchestrations",
    ]:
        (version_dir / relative_dir).mkdir(parents=True, exist_ok=True)


def ensure_pipeline_dirs(project_root: Path, current_version: str, next_version: str) -> None:
    ensure_version_dirs(project_root, current_version)
    ensure_version_dirs(project_root, next_version)


def build_paths(project_root: Path, current_version: str, next_version: str) -> dict[str, Path]:
    versions_dir = project_root / "versions"
    current_dir = versions_dir / current_version
    next_dir = versions_dir / next_version

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
