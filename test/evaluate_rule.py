from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "test"
DEFAULT_SCRIPT = TEST_DIR / "rule_v2.16.py"
DEFAULT_GOLD = TEST_DIR / "testnews.csv"
DEFAULT_OUT_DIR = TEST_DIR / "reports"

ID_COL = "\u65b0\u95fbid"

GOLD_LEVEL_TO_RULE = {
    "\u4f4e\u98ce\u9669": "low",
    "\u4e2d\u98ce\u9669": "medium",
    "\u9ad8\u98ce\u9669": "high",
    "low": "low",
    "medium": "medium",
    "high": "high",
}


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def value_counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.value_counts(dropna=False).items()}


def confusion_matrix(y_true: pd.Series, y_pred: pd.Series, labels: list[str]) -> dict[str, dict[str, int]]:
    return {
        true_label: {
            pred_label: int(((y_true == true_label) & (y_pred == pred_label)).sum())
            for pred_label in labels
        }
        for true_label in labels
    }


def script_stem(script_path: Path) -> str:
    return script_path.name.removesuffix(".py")


def copy_program(script_path: Path, program_dir: Path) -> Path:
    program_dir.mkdir(parents=True, exist_ok=True)
    target = program_dir / script_path.name
    shutil.copy2(script_path, target)
    return target


def run_rule_script(script_path: Path, gold_csv: Path, pred_csv: Path) -> subprocess.CompletedProcess[str]:
    pred_csv.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CSV_PATH"] = str(gold_csv)
    env["OUT_PATH"] = str(pred_csv)
    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        errors="replace",
        timeout=300,
    )


def evaluate(script_copy: Path, gold_csv: Path, pred_csv: Path, report_json: Path) -> dict[str, Any]:
    gold_df = read_csv(gold_csv)
    pred_df = read_csv(pred_csv)

    required_gold = [ID_COL, "gold_score", "gold_level", "gold_type"]
    required_pred = [ID_COL, "risk", "rule_label", "rule_primary_type"]
    missing_gold = [col for col in required_gold if col not in gold_df.columns]
    missing_pred = [col for col in required_pred if col not in pred_df.columns]
    if missing_gold or missing_pred:
        raise ValueError(
            f"Missing columns: gold={missing_gold or 'ok'}, pred={missing_pred or 'ok'}"
        )

    gold_df = gold_df.copy()
    pred_df = pred_df.copy()
    gold_df[ID_COL] = normalize_text(gold_df[ID_COL])
    pred_df[ID_COL] = normalize_text(pred_df[ID_COL])

    pred_keep = [
        ID_COL,
        "risk",
        "rule_label",
        "rule_types",
        "rule_primary_type",
        *[col for col in pred_df.columns if col.startswith("score_")],
    ]
    pred_keep = [col for col in pred_keep if col in pred_df.columns]

    merged = gold_df.merge(pred_df[pred_keep], on=ID_COL, how="inner")
    if merged.empty:
        raise ValueError("No matched rows between gold csv and prediction csv.")

    merged["gold_score"] = pd.to_numeric(merged["gold_score"], errors="coerce").fillna(0.0)
    merged["risk"] = pd.to_numeric(merged["risk"], errors="coerce").fillna(0.0)

    merged["gold_label_norm"] = normalize_text(merged["gold_level"]).map(GOLD_LEVEL_TO_RULE).fillna(
        normalize_text(merged["gold_level"]).str.lower()
    )
    merged["rule_label_norm"] = normalize_text(merged["rule_label"]).str.lower()
    merged["gold_type_norm"] = normalize_text(merged["gold_type"])
    merged["rule_primary_type_norm"] = normalize_text(merged["rule_primary_type"])

    score_error = merged["risk"] - merged["gold_score"]
    abs_error = score_error.abs()
    squared_error = np.square(score_error)
    label_match = merged["gold_label_norm"] == merged["rule_label_norm"]
    type_match = merged["gold_type_norm"] == merged["rule_primary_type_norm"]

    merged["score_error"] = score_error
    merged["abs_score_error"] = abs_error
    merged["label_match"] = label_match
    merged["primary_type_match"] = type_match

    labels = ["low", "medium", "high"]
    report = {
        "success": True,
        "program": str(script_copy.resolve()),
        "gold_csv": str(gold_csv.resolve()),
        "prediction_csv": str(pred_csv.resolve()),
        "rows": {
            "gold_rows": int(len(gold_df)),
            "prediction_rows": int(len(pred_df)),
            "matched_rows": int(len(merged)),
        },
        "score_metrics": {
            "mae": float(abs_error.mean()),
            "rmse": float(np.sqrt(squared_error.mean())),
            "mean_error_rule_minus_gold": float(score_error.mean()),
            "max_abs_error": float(abs_error.max()),
            "within_5_accuracy": float((abs_error <= 5).mean()),
            "within_10_accuracy": float((abs_error <= 10).mean()),
            "within_20_accuracy": float((abs_error <= 20).mean()),
        },
        "label_metrics": {
            "accuracy": float(label_match.mean()),
            "gold_distribution": value_counts(merged["gold_label_norm"]),
            "rule_distribution": value_counts(merged["rule_label_norm"]),
            "confusion_matrix": confusion_matrix(
                merged["gold_label_norm"],
                merged["rule_label_norm"],
                labels,
            ),
        },
        "primary_type_metrics": {
            "accuracy": float(type_match.mean()),
            "gold_distribution": value_counts(merged["gold_type_norm"]),
            "rule_distribution": value_counts(merged["rule_primary_type_norm"]),
            "top_mismatches": (
                merged.loc[~type_match]
                .groupby(["gold_type_norm", "rule_primary_type_norm"])
                .size()
                .reset_index(name="count")
                .sort_values("count", ascending=False)
                .head(20)
                .to_dict(orient="records")
            ),
        },
    }

    write_json(report_json, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a risk labeler script and evaluate it on testnews.csv.")
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    script_path = args.script.resolve()
    gold_csv = args.gold.resolve()
    out_dir = args.out_dir.resolve()
    name = script_stem(script_path)
    program_dir = out_dir / name

    script_copy = copy_program(script_path, program_dir)
    pred_csv = program_dir / "predictions.csv"
    report_json = program_dir / "eval_report.json"

    completed = run_rule_script(script_path, gold_csv, pred_csv)
    if completed.returncode != 0:
        print(f"script failed with return_code={completed.returncode}", file=sys.stderr)
        if completed.stderr:
            print(completed.stderr.strip(), file=sys.stderr)
        return completed.returncode

    report = evaluate(script_copy, gold_csv, pred_csv, report_json)
    print(f"prediction_csv: {pred_csv}")
    print(f"report_json   : {report_json}")
    print(f"mae           : {report['score_metrics']['mae']:.4f}")
    print(f"rmse          : {report['score_metrics']['rmse']:.4f}")
    print(f"label_acc     : {report['label_metrics']['accuracy']:.4f}")
    print(f"type_acc      : {report['primary_type_metrics']['accuracy']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
