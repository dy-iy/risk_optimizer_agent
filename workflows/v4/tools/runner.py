"""Run one V4 script and produce predictions, metrics and error slices."""

import importlib.util
import json
import sys
from pathlib import Path

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflows.v4.tools.evaluator import evaluate
from workflows.v4.tools.merger import merge_results
from workflows.v4.tools.paths import build_paths
from workflows.v4.tools.slicer import slice_errors


VERSION = "v1"
RISK_TYPES = json.loads(
    (build_paths(VERSION)["input_csv"].parents[1] / "artifacts" / "vocabulary.json").read_text(
        encoding="utf-8"
    )
)


def load_labeler(script_path, version: str):
    spec = importlib.util.spec_from_file_location(f"labeler_{version}", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载脚本：{script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "label_csv"):
        raise AttributeError("版本脚本缺少 label_csv")
    return module


def print_metrics(category_metrics: dict, level_metrics: dict, slices: dict) -> None:
    print("\n风险类别指标")
    print(
        "Micro-F1 / Macro-F1: "
        f"{category_metrics['micro_f1']:.4f} / "
        f"{category_metrics['macro_f1']:.4f}"
    )
    print("per-category precision / recall / F1:")
    for risk_type, values in category_metrics["per_category"].items():
        print(
            f"- {risk_type}: {values['precision']:.4f} / "
            f"{values['recall']:.4f} / {values['f1']:.4f}"
        )

    print("\n风险等级指标")
    print(
        "Accuracy / Macro-F1: "
        f"{level_metrics['accuracy']:.4f} / {level_metrics['macro_f1']:.4f}"
    )
    print(
        "高风险 Recall / F1: "
        f"{level_metrics['high_risk']['recall']:.4f} / "
        f"{level_metrics['high_risk']['f1']:.4f}"
    )
    print("\nper-level precision / recall / F1:")
    for level, values in level_metrics["per_level"].items():
        print(
            f"- {level}: {values['precision']:.4f} / "
            f"{values['recall']:.4f} / {values['f1']:.4f}"
        )
    print(f"error slices: {slices}")


def run_version(
    version: str,
    show_metrics: bool = True,
    show_progress: bool = True,
) -> tuple[dict, dict, dict]:
    paths = build_paths(version)
    progress = tqdm(total=4, desc=f"运行 {version}", unit="步", disable=not show_progress)

    labeler = load_labeler(paths["script"], version)
    labeler.label_csv(paths["input_csv"], paths["prediction_csv"])
    progress.update(1)

    merge_results(paths["input_csv"], paths["prediction_csv"], paths["merged_csv"])
    progress.update(1)

    category_metrics, level_metrics = evaluate(
        paths["merged_csv"],
        paths["category_metrics_json"],
        paths["level_metrics_json"],
        RISK_TYPES,
    )
    progress.update(1)

    slices = slice_errors(paths["merged_csv"], paths)
    progress.update(1)
    progress.close()
    if show_metrics:
        print_metrics(category_metrics, level_metrics, slices)
    return category_metrics, level_metrics, slices


def main() -> None:
    run_version(VERSION)


if __name__ == "__main__":
    main()
