from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

import build_final_gold_news as final_builder
import consistency_checker_csv as checker


PROCESS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROCESS_DIR / "output"
CHECK_DIR = OUTPUT_DIR / "consistency_check"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def comparable(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def assert_equal(actual: Any, expected: Any, context: str) -> None:
    if comparable(actual) != comparable(expected):
        raise AssertionError(f"{context}: actual={actual!r}, expected={expected!r}")


def validate_consistency_replay() -> dict[str, Any]:
    label_a = checker.read_csv_auto(checker.CSV_A_PATH)
    label_b = checker.read_csv_auto(checker.resolve_csv_b_path())
    report = checker.read_csv_auto(CHECK_DIR / "consistency_report.csv").set_index("新闻id")
    candidate = checker.read_csv_auto(CHECK_DIR / "candidate_gold_agreement.csv").set_index("新闻id")

    checker.validate_input(label_a, "A")
    checker.validate_input(label_b, "B")
    a_by_id = label_a.set_index("新闻id", drop=False)
    b_by_id = label_b.set_index("新闻id", drop=False)
    if set(a_by_id.index) != set(b_by_id.index):
        raise AssertionError("A/B 新闻id 集合不一致")

    route_counts = {
        "A_high_agreement": 0,
        "B_minor_conflict": 0,
        "C_severe_conflict": 0,
    }
    for record_id in sorted(a_by_id.index):
        row_a = a_by_id.loc[record_id]
        row_b = b_by_id.loc[record_id]
        ann_a = checker.normalize_annotation(row_a)
        ann_b = checker.normalize_annotation(row_b)
        metrics = checker.compare_annotations(ann_a, ann_b)
        historical = report.loc[record_id]

        for field in [
            "score_a",
            "score_b",
            "score_diff",
            "label_a",
            "label_b",
            "primary_a",
            "primary_b",
            "consistency_level",
            "suggested_action",
        ]:
            assert_equal(metrics[field], historical[field], f"新闻id={record_id}, field={field}")

        route_counts[metrics["consistency_level"]] += 1
        if metrics["consistency_level"] == "A_high_agreement":
            replayed = checker.merge_candidate_gold(
                record_id,
                str(row_a.get("时间", "")) or str(row_b.get("时间", "")),
                str(row_a.get("内容", "")) or str(row_b.get("内容", "")),
                ann_a,
                ann_b,
            )
            stored = candidate.loc[record_id]
            for field in [
                "risk_score",
                "risk_label",
                "risk_types",
                "primary_risk_type",
                "reason",
                "confidence",
                "summary",
                "gold_source",
            ]:
                assert_equal(replayed[field], stored[field], f"candidate 新闻id={record_id}, field={field}")

    return {"rows": len(label_a), "route_counts": route_counts}


def validate_final_outputs() -> dict[str, Any]:
    final_path = OUTPUT_DIR / "final_gold_news_1000.csv"
    source_path = OUTPUT_DIR / "final_gold_news_1000_with_source.csv"
    manifest_path = OUTPUT_DIR / "final_gold_news_1000_manifest.json"
    review_path = final_builder.active_review_path()
    if review_path is None:
        raise AssertionError("找不到复核文件")

    final = pd.read_csv(final_path, encoding="utf-8-sig")
    with_source = pd.read_csv(source_path, encoding="utf-8-sig")
    sample = pd.read_csv(final_builder.SAMPLE_PATH, encoding="utf-8-sig")
    review = pd.read_csv(review_path, encoding="utf-8-sig", keep_default_na=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    final_builder.validate(final, sample)
    if not final.equals(with_source[final_builder.TARGET_COLUMNS]):
        raise AssertionError("主输出与带来源输出的 10 个标准字段不一致")

    dataset_hash = manifest["outputs"]["dataset"]["sha256"]
    source_hash = manifest["outputs"]["dataset_with_source"]["sha256"]
    assert_equal(dataset_hash, sha256_file(final_path), "final dataset sha256")
    assert_equal(source_hash, sha256_file(source_path), "source dataset sha256")

    pending_review = int((review["human_review_status"] == "pending").sum())
    pending_source = int(
        (
            with_source["gold_source"]
            == "severe_conflict_llm_adjudicated_pending_human"
        ).sum()
    )
    assert_equal(pending_review, pending_source, "pending human review rows")
    assert_equal(manifest["pending_human_review_rows"], pending_source, "manifest pending rows")

    return {
        "rows": len(final),
        "columns": len(final.columns),
        "source_counts": with_source["gold_source"].value_counts().to_dict(),
        "risk_label_counts": final["risk_label"].value_counts().to_dict(),
        "pending_human_review_rows": pending_review,
        "manifest_hashes_valid": True,
    }


def main() -> None:
    result = {
        "consistency_replay": validate_consistency_replay(),
        "final_outputs": validate_final_outputs(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
