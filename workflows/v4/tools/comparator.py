"""Compare two versions and decide whether the later version is acceptable."""

import json
from pathlib import Path

from .common import write_json
from .paths import V4_DIR, build_paths


VERSION_1 = "v1"
VERSION_2 = "v2"

# Accuracy is excluded because 870/1000 rows are low risk.
WEIGHTS = {
    "类别_Micro-F1": 0.25,
    "类别_Macro-F1": 0.25,
    "等级_Macro-F1": 0.20,
    "高风险_Recall": 0.10,
    "高风险_F1": 0.20,
}
MAX_DROPS = {
    "类别_Micro-F1": 0.02,
    "类别_Macro-F1": 0.02,
    "等级_Macro-F1": 0.03,
    "高风险_Recall": 0.30,
    "高风险_F1": 0.03,
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summary(version: str) -> dict:
    paths = build_paths(version)
    category = read_json(paths["category_metrics_json"])
    level = read_json(paths["level_metrics_json"])
    return {
        "类别_Micro-F1": category["micro_f1"],
        "类别_Macro-F1": category["macro_f1"],
        "等级_Accuracy": level["accuracy"],
        "等级_Macro-F1": level["macro_f1"],
        "高风险_Recall": level["high_risk"]["recall"],
        "高风险_F1": level["high_risk"]["f1"],
    }


def quality_score(metrics: dict) -> float:
    return round(sum(metrics[name] * weight for name, weight in WEIGHTS.items()), 6)


def compare_versions(version_1: str = VERSION_1, version_2: str = VERSION_2) -> dict:
    metrics_1 = summary(version_1)
    metrics_2 = summary(version_2)
    changes = {
        name: round(metrics_2[name] - metrics_1[name], 6)
        for name in metrics_1
    }
    score_1 = quality_score(metrics_1)
    score_2 = quality_score(metrics_2)

    reasons = [
        f"{name} 下降 {abs(changes[name]):.4f}，超过允许值 {limit:.4f}"
        for name, limit in MAX_DROPS.items()
        if changes[name] < -limit
    ]
    if score_2 <= score_1:
        reasons.append(f"综合质量分未提升：{score_1:.4f} -> {score_2:.4f}")
    accepted = not reasons

    report = {
        "对比版本": [version_1, version_2],
        version_1: metrics_1,
        version_2: metrics_2,
        f"变化_{version_2}减{version_1}": changes,
        "综合质量分": {version_1: score_1, version_2: score_2},
        f"是否接受_{version_2}": accepted,
        "原因": reasons or [f"{version_2} 通过质量门槛"],
    }

    output = (
        V4_DIR
        / "versions"
        / version_2
        / "reports"
        / "comparison"
        / f"{version_1}_vs_{version_2}.json"
    )
    write_json(report, output)
    print(f"版本对比完成：{output}")
    return report


if __name__ == "__main__":
    compare_versions()
