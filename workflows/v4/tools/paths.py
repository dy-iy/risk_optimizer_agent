"""Paths for one V4 version."""

import re
from pathlib import Path


V4_DIR = Path(__file__).resolve().parents[1]


def build_paths(version: str) -> dict[str, Path]:
    if not re.fullmatch(r"v\d+", version):
        raise ValueError("版本名必须是 v1、v2 等格式")

    version_dir = V4_DIR / "versions" / version
    reports = version_dir / "reports"
    paths = {
        "input_csv": V4_DIR / "data" / "crypto_news_train.csv",
        "script": version_dir / f"script_{version}.py",
        "prediction_csv": reports / "predictions" / "predictions.csv",
        "merged_csv": reports / "merged" / "merged.csv",
        "category_metrics_json": reports / "evals" / "category_metrics.json",
        "level_metrics_json": reports / "evals" / "level_metrics.json",
        "type_mismatch_csv": reports / "errors" / "type_mismatch.csv",
        "level_mismatch_csv": reports / "errors" / "level_mismatch.csv",
        "analysis_json": reports / "analysis" / "analysis.json",
        "patch_report_json": reports / "patch" / "patch_report.json",
    }
    for path in paths.values():
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
    return paths
