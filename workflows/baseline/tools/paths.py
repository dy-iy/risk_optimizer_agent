from __future__ import annotations

import os
from pathlib import Path

try:
    from .schema import VERSION_SUBDIRS
except ImportError:
    from schema import VERSION_SUBDIRS


def resolve_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data").is_dir() and (parent / "workflows").is_dir():
            return parent
        if (parent / "tools").is_dir() and (parent / "data").is_dir():
            return parent
    return current.parents[3]


def resolve_workflow_name(workflow_name: str | None = None) -> str:
    name = (workflow_name or os.environ.get("RISK_WORKFLOW") or "baseline").strip()
    return name or "baseline"


def resolve_workflow_dir(project_root: Path, workflow_name: str | None = None) -> Path:
    return project_root / "workflows" / resolve_workflow_name(workflow_name)


def resolve_versions_dir(project_root: Path, workflow_name: str | None = None) -> Path:
    workflow_versions_dir = resolve_workflow_dir(project_root, workflow_name) / "versions"
    if workflow_versions_dir.exists() or resolve_workflow_name(workflow_name) != "baseline":
        return workflow_versions_dir
    return project_root / "versions"


def resolve_experiments_dir(project_root: Path, workflow_name: str | None = None) -> Path:
    workflow_experiments_dir = resolve_workflow_dir(project_root, workflow_name) / "experiments"
    if workflow_experiments_dir.exists() or resolve_workflow_name(workflow_name) != "baseline":
        return workflow_experiments_dir
    return project_root / "experiments"


def normalize_version_name(version: str | int) -> str:
    version_text = str(version).strip().lower()
    if version_text.startswith("v"):
        version_text = version_text[1:]
    if not version_text.isdigit():
        raise ValueError(f"版本号不合法: {version}")
    return f"v{version_text}"


def resolve_version_script(project_root: Path, version_name: str, workflow_name: str | None = None) -> Path:
    versions_dir = resolve_versions_dir(project_root, workflow_name)
    candidates = [
        versions_dir / version_name / "scripts" / f"risk_labeler_{version_name}.py",
        project_root / f"risk_labeler_{version_name}.py",
        project_root / "src" / f"risk_labeler_{version_name}.py",
        project_root / "app" / f"risk_labeler_{version_name}.py",
    ]
    for path in candidates:
        if path.exists():
            return path

    return versions_dir / version_name / "scripts" / f"risk_labeler_{version_name}.py"


def resolve_default_input_csv(project_root: Path) -> Path:
    return project_root / "data" / "input" / "raw_1000_news.csv"


def resolve_default_gold_csv(project_root: Path) -> Path:
    return project_root / "data" / "gold" / "crypto_news_risk_gold_1000.csv"


def ensure_version_dirs(project_root: Path, version_name: str, workflow_name: str | None = None) -> None:
    version_dir = resolve_versions_dir(project_root, workflow_name) / version_name
    for relative_dir in VERSION_SUBDIRS:
        (version_dir / relative_dir).mkdir(parents=True, exist_ok=True)


def ensure_pipeline_dirs(
    project_root: Path,
    current_version: str,
    next_version: str,
    workflow_name: str | None = None,
) -> None:
    ensure_version_dirs(project_root, current_version, workflow_name)
    ensure_version_dirs(project_root, next_version, workflow_name)


def build_paths(
    project_root: Path,
    current_version: str,
    next_version: str,
    workflow_name: str | None = None,
) -> dict[str, Path]:
    versions_dir = resolve_versions_dir(project_root, workflow_name)
    current_dir = versions_dir / current_version
    next_dir = versions_dir / next_version
    current_report_dir = current_dir / "reports"
    next_report_dir = next_dir / "reports"

    return {
        "prediction_csv": current_report_dir / "predictions" / f"risk_labeler_{current_version}_output.csv",
        "runlog_json": current_report_dir / "predictions" / f"risk_labeler_{current_version}_runlog.json",
        "merged_csv": current_report_dir / "merged" / f"risk_labeler_{current_version}_merged.csv",
        "merge_log_json": current_report_dir / "merged" / f"risk_labeler_{current_version}_merge_log.json",
        "eval_json": current_report_dir / "evals" / f"risk_labeler_{current_version}_eval.json",
        "eval_details_json": current_report_dir / "evals" / f"risk_labeler_{current_version}_eval_details.json",
        "false_positive_csv": current_report_dir / "errors" / f"risk_labeler_{current_version}_false_positive.csv",
        "false_negative_csv": current_report_dir / "errors" / f"risk_labeler_{current_version}_false_negative.csv",
        "type_mismatch_csv": current_report_dir / "errors" / f"risk_labeler_{current_version}_type_mismatch.csv",
        "score_diff_top_csv": current_report_dir / "errors" / f"risk_labeler_{current_version}_score_diff_top.csv",
        "slice_log_json": current_report_dir / "errors" / f"risk_labeler_{current_version}_slice_log.json",
        "analysis_json_llm": current_report_dir / "analysis" / f"risk_labeler_{current_version}_analysis_llm.json",
        "analysis_markdown_llm": current_report_dir / "analysis" / f"risk_labeler_{current_version}_analysis_llm.md",
        "llm_payload_json": current_report_dir / "analysis" / f"risk_labeler_{current_version}_analysis_llm_payload.json",
        "patched_script": next_dir / "scripts" / f"risk_labeler_{next_version}.py",
        "patch_report_json": next_report_dir / "patched" / f"risk_labeler_{next_version}_patch_report.json",
        "patch_report_markdown": next_report_dir / "patched" / f"risk_labeler_{next_version}_patch_report.md",
        "compare_json": next_report_dir / "comparisons" / f"{current_version}_vs_{next_version}_compare.json",
        "compare_markdown": next_report_dir / "comparisons" / f"{current_version}_vs_{next_version}_compare.md",
        "orchestrate_json": next_report_dir / "orchestrations" / f"{current_version}_to_{next_version}_orchestrate.json",
    }
