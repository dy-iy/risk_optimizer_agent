from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable

try:
    from .common import ensure_parent_dir, write_json_file
    from .metrics import VersionMetrics, collect_version_metrics
    from .paths import normalize_version_name, resolve_experiments_dir, resolve_project_root, resolve_versions_dir
except ImportError:
    from common import ensure_parent_dir, write_json_file
    from metrics import VersionMetrics, collect_version_metrics
    from paths import normalize_version_name, resolve_experiments_dir, resolve_project_root, resolve_versions_dir


REPORT_COLUMNS = [
    "version",
    "has_eval",
    "has_slice",
    "total_rows",
    "matched_rows",
    "score_mae",
    "score_rmse",
    "label_accuracy",
    "primary_type_accuracy",
    "false_positive_rows",
    "false_negative_rows",
    "type_mismatch_rows",
    "score_diff_top_rows",
]


def version_sort_key(version: str) -> tuple[int, str]:
    normalized = normalize_version_name(version)
    return int(normalized.lstrip("v")), normalized


def discover_versions(project_root: Path, workflow_name: str | None = None) -> list[str]:
    versions_dir = resolve_versions_dir(project_root, workflow_name)
    if not versions_dir.exists():
        return []
    versions = [
        path.name
        for path in versions_dir.iterdir()
        if path.is_dir() and path.name.lower().startswith("v") and path.name[1:].isdigit()
    ]
    return sorted(versions, key=version_sort_key)


def select_versions(project_root: Path, versions: Iterable[str] | None, workflow_name: str | None = None) -> list[str]:
    if versions:
        return [normalize_version_name(version) for version in versions]
    return discover_versions(project_root, workflow_name)


def metric_value(row: VersionMetrics, name: str):
    return getattr(row, name)


def write_csv_report(rows: list[VersionMetrics], csv_path: Path) -> None:
    ensure_parent_dir(csv_path)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            data = row.to_dict()
            writer.writerow({key: data.get(key) for key in REPORT_COLUMNS})


def format_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def best_version(rows: list[VersionMetrics]) -> str:
    candidates = [row for row in rows if row.has_eval]
    if not candidates:
        return ""

    metric_specs = [
        ("score_mae", False),
        ("score_rmse", False),
        ("label_accuracy", True),
        ("primary_type_accuracy", True),
        ("false_positive_rows", False),
        ("false_negative_rows", False),
        ("type_mismatch_rows", False),
    ]
    scores = {row.version: 0 for row in candidates}
    for metric_name, higher_is_better in metric_specs:
        values = [(row.version, metric_value(row, metric_name)) for row in candidates]
        values = [(version, value) for version, value in values if value is not None]
        if not values:
            continue
        target = max(value for _, value in values) if higher_is_better else min(value for _, value in values)
        for version, value in values:
            if value == target:
                scores[version] += 1

    ranked = sorted(
        candidates,
        key=lambda row: (
            -scores[row.version],
            row.score_mae,
            -row.label_accuracy,
            -row.primary_type_accuracy,
        ),
    )
    return ranked[0].version


def build_delta_rows(rows: list[VersionMetrics]) -> list[dict]:
    deltas: list[dict] = []
    previous: VersionMetrics | None = None
    for row in rows:
        if previous is None or not previous.has_eval or not row.has_eval:
            previous = row
            continue
        deltas.append(
            {
                "from_version": previous.version,
                "to_version": row.version,
                "score_mae_delta": round(row.score_mae - previous.score_mae, 6),
                "score_rmse_delta": round(row.score_rmse - previous.score_rmse, 6),
                "label_accuracy_delta": round(row.label_accuracy - previous.label_accuracy, 6),
                "primary_type_accuracy_delta": round(row.primary_type_accuracy - previous.primary_type_accuracy, 6),
                "false_positive_delta": (
                    None
                    if row.false_positive_rows is None or previous.false_positive_rows is None
                    else row.false_positive_rows - previous.false_positive_rows
                ),
                "false_negative_delta": (
                    None
                    if row.false_negative_rows is None or previous.false_negative_rows is None
                    else row.false_negative_rows - previous.false_negative_rows
                ),
                "type_mismatch_delta": (
                    None
                    if row.type_mismatch_rows is None or previous.type_mismatch_rows is None
                    else row.type_mismatch_rows - previous.type_mismatch_rows
                ),
            }
        )
        previous = row
    return deltas


def render_markdown(rows: list[VersionMetrics], deltas: list[dict], winner: str) -> str:
    lines: list[str] = []
    lines.append("# Experiment Metrics Report")
    lines.append("")
    if winner:
        lines.append(f"- Best version by balanced metric voting: **{winner}**")
    else:
        lines.append("- Best version by balanced metric voting: unavailable")
    lines.append(f"- Versions compared: {len(rows)}")
    lines.append("- Compare versions only when their eval artifacts use the same gold dataset.")
    lines.append("")
    lines.append("## Version Metrics")
    lines.append("")
    lines.append("| Version | MAE | RMSE | Label Acc | Type Acc | FP | FN | Type Mismatch |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.version,
                    format_cell(row.score_mae),
                    format_cell(row.score_rmse),
                    format_cell(row.label_accuracy),
                    format_cell(row.primary_type_accuracy),
                    format_cell(row.false_positive_rows),
                    format_cell(row.false_negative_rows),
                    format_cell(row.type_mismatch_rows),
                ]
            )
            + " |"
        )
    lines.append("")

    if deltas:
        lines.append("## Version Deltas")
        lines.append("")
        lines.append("| From | To | MAE Delta | Label Acc Delta | Type Acc Delta | FP Delta | FN Delta | Type Mismatch Delta |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for row in deltas:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["from_version"],
                        row["to_version"],
                        format_cell(row["score_mae_delta"]),
                        format_cell(row["label_accuracy_delta"]),
                        format_cell(row["primary_type_accuracy_delta"]),
                        format_cell(row["false_positive_delta"]),
                        format_cell(row["false_negative_delta"]),
                        format_cell(row["type_mismatch_delta"]),
                    ]
                )
                + " |"
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_experiment_report(
    project_root: Path,
    versions: list[str],
    output_dir: Path,
    workflow_name: str | None = None,
) -> dict[str, str]:
    rows = collect_version_metrics(project_root, versions, workflow_name)
    rows = sorted(rows, key=lambda row: version_sort_key(row.version))
    deltas = build_delta_rows(rows)
    winner = best_version(rows)

    json_path = output_dir / "version_metrics.json"
    csv_path = output_dir / "version_metrics.csv"
    markdown_path = output_dir / "version_metrics.md"

    write_json_file(
        json_path,
        {
            "best_version": winner,
            "versions": [row.to_dict() for row in rows],
            "deltas": deltas,
        },
    )
    write_csv_report(rows, csv_path)
    ensure_parent_dir(markdown_path)
    markdown_path.write_text(render_markdown(rows, deltas, winner), encoding="utf-8")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
        "best_version": winner,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a multi-version experiment metrics report.")
    parser.add_argument("--workflow", type=str, default="baseline", help="Workflow name under workflows/, e.g. baseline or v3.")
    parser.add_argument("--versions", nargs="*", default=None, help="Versions to compare, e.g. v20 v21 v22 v23.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for version_metrics.{json,csv,md}. Defaults to workflows/<workflow>/experiments/reports/latest.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = resolve_project_root()
    versions = select_versions(project_root, args.versions, args.workflow)
    if not versions:
        print("No versions found.")
        return 1

    output_dir = args.output_dir
    if output_dir is None:
        output_dir = resolve_experiments_dir(project_root, args.workflow) / "reports" / "latest"
    elif not output_dir.is_absolute():
        output_dir = resolve_experiments_dir(project_root, args.workflow) / output_dir

    result = write_experiment_report(project_root, versions, output_dir, args.workflow)
    print(f"workflow    : {args.workflow}")
    print(f"best_version: {result['best_version'] or '(none)'}")
    print(f"json        : {result['json']}")
    print(f"csv         : {result['csv']}")
    print(f"markdown    : {result['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
