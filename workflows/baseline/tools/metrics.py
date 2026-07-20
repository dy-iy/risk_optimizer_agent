from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from .common import read_json_file
    from .paths import normalize_version_name, resolve_versions_dir
except ImportError:
    from common import read_json_file
    from paths import normalize_version_name, resolve_versions_dir


@dataclass
class VersionMetrics:
    version: str
    eval_json: str
    slice_log_json: str
    has_eval: bool
    has_slice: bool
    total_rows: int = 0
    matched_rows: int = 0
    score_mae: float = 0.0
    score_rmse: float = 0.0
    label_accuracy: float = 0.0
    primary_type_accuracy: float = 0.0
    false_positive_rows: Optional[int] = None
    false_negative_rows: Optional[int] = None
    type_mismatch_rows: Optional[int] = None
    score_diff_top_rows: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_data(self) -> bool:
        core_values = [
            self.total_rows,
            self.matched_rows,
            self.score_mae,
            self.score_rmse,
            self.label_accuracy,
            self.primary_type_accuracy,
            self.false_positive_rows or 0,
            self.false_negative_rows or 0,
            self.type_mismatch_rows or 0,
            self.score_diff_top_rows or 0,
        ]
        return any(core_values)


def safe_get(data: dict, *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load_optional_json(path: Optional[str | Path]) -> Optional[dict]:
    if not path:
        return None
    json_path = Path(path).resolve()
    if not json_path.exists():
        return None
    return read_json_file(json_path)


def summarize_eval(eval_data: dict) -> dict[str, Any]:
    return {
        "score_mae": to_float(safe_get(eval_data, "score_metrics", "mae", default=0.0)),
        "score_rmse": to_float(safe_get(eval_data, "score_metrics", "rmse", default=0.0)),
        "label_accuracy": to_float(safe_get(eval_data, "label_metrics", "accuracy", default=0.0)),
        "primary_type_accuracy": to_float(safe_get(eval_data, "primary_type_metrics", "accuracy", default=0.0)),
        "matched_rows": int(to_float(safe_get(eval_data, "matched_rows", default=0))),
        "total_rows": int(to_float(safe_get(eval_data, "total_rows", default=0))),
    }


def summarize_slice(slice_data: Optional[dict]) -> dict[str, Any]:
    if not slice_data:
        return {
            "false_positive_rows": None,
            "false_negative_rows": None,
            "type_mismatch_rows": None,
            "score_diff_top_rows": None,
        }
    return {
        "false_positive_rows": safe_get(slice_data, "false_positive_rows", default=None),
        "false_negative_rows": safe_get(slice_data, "false_negative_rows", default=None),
        "type_mismatch_rows": safe_get(slice_data, "type_mismatch_rows", default=None),
        "score_diff_top_rows": safe_get(slice_data, "score_diff_top_rows", default=None),
    }


def version_eval_path(project_root: Path, version: str, workflow_name: str | None = None) -> Path:
    version_name = normalize_version_name(version)
    return resolve_versions_dir(project_root, workflow_name) / version_name / "reports" / "evals" / f"risk_labeler_{version_name}_eval.json"


def version_slice_log_path(project_root: Path, version: str, workflow_name: str | None = None) -> Path:
    version_name = normalize_version_name(version)
    return resolve_versions_dir(project_root, workflow_name) / version_name / "reports" / "errors" / f"risk_labeler_{version_name}_slice_log.json"


def load_version_metrics(project_root: Path, version: str, workflow_name: str | None = None) -> VersionMetrics:
    version_name = normalize_version_name(version)
    eval_json = version_eval_path(project_root, version_name, workflow_name)
    slice_log_json = version_slice_log_path(project_root, version_name, workflow_name)

    eval_data = load_optional_json(eval_json) or {}
    slice_data = load_optional_json(slice_log_json)
    eval_summary = summarize_eval(eval_data)
    slice_summary = summarize_slice(slice_data)

    return VersionMetrics(
        version=version_name,
        eval_json=str(eval_json),
        slice_log_json=str(slice_log_json),
        has_eval=bool(eval_data),
        has_slice=bool(slice_data),
        total_rows=eval_summary["total_rows"],
        matched_rows=eval_summary["matched_rows"],
        score_mae=eval_summary["score_mae"],
        score_rmse=eval_summary["score_rmse"],
        label_accuracy=eval_summary["label_accuracy"],
        primary_type_accuracy=eval_summary["primary_type_accuracy"],
        false_positive_rows=slice_summary["false_positive_rows"],
        false_negative_rows=slice_summary["false_negative_rows"],
        type_mismatch_rows=slice_summary["type_mismatch_rows"],
        score_diff_top_rows=slice_summary["score_diff_top_rows"],
    )


def collect_version_metrics(
    project_root: Path,
    versions: list[str],
    workflow_name: str | None = None,
) -> list[VersionMetrics]:
    return [load_version_metrics(project_root, version, workflow_name) for version in versions]


def collect_recent_version_metrics(
    project_root: Path,
    current_version: str,
    workflow_name: str | None = None,
) -> list[dict[str, Any]]:
    current_version_name = normalize_version_name(current_version)
    current_num = int(current_version_name.lstrip("v"))
    versions = []
    if current_num > 1:
        versions.append(f"v{current_num - 1}")
    versions.append(current_version_name)

    rows: list[dict[str, Any]] = []
    for item in collect_version_metrics(project_root, versions, workflow_name):
        if item.has_data():
            rows.append(
                {
                    "version": item.version,
                    "false_positive_rows": item.false_positive_rows or 0,
                    "false_negative_rows": item.false_negative_rows or 0,
                    "type_mismatch_rows": item.type_mismatch_rows or 0,
                    "score_diff_top_rows": item.score_diff_top_rows or 0,
                    "score_diff_mean": item.score_mae,
                    "score_diff_rmse": item.score_rmse,
                }
            )
    return rows
