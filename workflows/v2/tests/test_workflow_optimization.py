from __future__ import annotations

import unittest
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from analyzer_llm import (
    build_analysis_payload,
    build_patch_contract,
    build_regression_summary,
    build_transition_risk_analysis,
)
from orchestrator import choose_focus_metric
from tools.comparator import evaluate_acceptance_policy
from tools.paths import resolve_versions_root
from tools.impact_analyzer import analyze_candidate_impact


class ComparatorPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = {
            "false_positive_rows": 46,
            "false_negative_rows": 14,
            "type_mismatch_rows": 162,
        }

    def test_rejects_any_target_regression(self) -> None:
        candidate = {
            "false_positive_rows": 45,
            "false_negative_rows": 14,
            "type_mismatch_rows": 163,
        }
        decision = evaluate_acceptance_policy(
            self.baseline,
            candidate,
            focus_metric="type_mismatch_rows",
        )
        self.assertFalse(decision["candidate_accepted"])
        self.assertIn("type_mismatch_rows", decision["regressions"])

    def test_accepts_focus_improvement_without_regression(self) -> None:
        candidate = {
            "false_positive_rows": 46,
            "false_negative_rows": 14,
            "type_mismatch_rows": 80,
        }
        decision = evaluate_acceptance_policy(
            self.baseline,
            candidate,
            focus_metric="type_mismatch_rows",
        )
        self.assertTrue(decision["candidate_accepted"])
        self.assertEqual(decision["improvements"], ["type_mismatch_rows"])

    def test_rejects_non_focus_improvement_during_targeted_iteration(self) -> None:
        candidate = {
            "false_positive_rows": 45,
            "false_negative_rows": 14,
            "type_mismatch_rows": 162,
        }
        decision = evaluate_acceptance_policy(
            self.baseline,
            candidate,
            focus_metric="type_mismatch_rows",
        )
        self.assertFalse(decision["candidate_accepted"])
        self.assertFalse(decision["focus_satisfied"])

    def test_rejects_metric_gaming_that_hurts_label_accuracy(self) -> None:
        candidate = {
            "false_positive_rows": 40,
            "false_negative_rows": 14,
            "type_mismatch_rows": 160,
        }
        decision = evaluate_acceptance_policy(
            self.baseline,
            candidate,
            baseline_quality={
                "label_accuracy": 0.868,
                "primary_type_accuracy": 0.821,
                "score_mae": 11.9,
                "score_rmse": 17.8,
                "matched_rows": 1000,
            },
            candidate_quality={
                "label_accuracy": 0.860,
                "primary_type_accuracy": 0.821,
                "score_mae": 11.8,
                "score_rmse": 17.7,
                "matched_rows": 1000,
            },
            focus_metric="false_positive_rows",
        )
        self.assertFalse(decision["candidate_accepted"])
        self.assertIn("label_accuracy", decision["quality_regressions"])


class DiagnosticTests(unittest.TestCase):
    def test_detects_type_mismatch_plateau(self) -> None:
        metrics = [
            {"version": "v18", "false_positive_rows": 42, "false_negative_rows": 7, "type_mismatch_rows": 259},
            {"version": "v19", "false_positive_rows": 31, "false_negative_rows": 10, "type_mismatch_rows": 258},
            {"version": "v20", "false_positive_rows": 26, "false_negative_rows": 13, "type_mismatch_rows": 258},
        ]
        summary = build_regression_summary(metrics)
        plateau_names = {item["metric"] for item in summary["plateau_metrics"]}
        self.assertIn("type_mismatch_rows", plateau_names)
        self.assertEqual(choose_focus_metric(metrics), "type_mismatch_rows")

    def test_type_mismatch_sampling_covers_pairs(self) -> None:
        rows = []
        for pair_index in range(6):
            for row_index in range(3):
                rows.append(
                    {
                        "新闻id": f"{pair_index}-{row_index}",
                        "gold_risk_score": 10,
                        "rule_risk_score": 15,
                        "score_diff": 5 - row_index,
                        "gold_risk_label": "low",
                        "rule_risk_label": "low",
                        "gold_primary_risk_type": "无明显风险",
                        "rule_primary_risk_type": f"类别{pair_index}",
                        "type_mismatch_kind": "overassigned_weak_type",
                        "type_mismatch_pair": f"无明显风险 -> 类别{pair_index}",
                    }
                )
        tm_df = pd.DataFrame(rows)
        empty = pd.DataFrame()
        payload = build_analysis_payload(
            empty,
            empty,
            tm_df,
            empty,
            sample_rows=12,
            focus_metric="type_mismatch_rows",
        )
        sampled_pairs = {
            (item["gold_primary_risk_type"], item["rule_primary_risk_type"])
            for item in payload["sample_cases"]["type_mismatch"]
        }
        self.assertEqual(len(sampled_pairs), 6)
        self.assertEqual(
            payload["optimization_contract"]["focus_metric"],
            "type_mismatch_rows",
        )

    def test_transition_analysis_exposes_fp_to_type_mismatch_controls(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "新闻id": "21",
                    "内容": "conditional liquidation estimate",
                    "gold_risk_score": 20,
                    "rule_risk_score": 85,
                    "gold_risk_label": "low",
                    "rule_risk_label": "high",
                    "gold_risk_types": '["liquidation"]',
                    "rule_risk_types": "liquidation",
                    "gold_primary_risk_type": "liquidation",
                    "rule_primary_risk_type": "liquidation",
                    "score_liquidation": 0.85,
                },
                {
                    "新闻id": "22",
                    "内容": "low severity typed control",
                    "gold_risk_score": 20,
                    "rule_risk_score": 20,
                    "gold_risk_label": "low",
                    "rule_risk_label": "low",
                    "gold_risk_types": '["liquidation"]',
                    "rule_risk_types": "liquidation",
                    "gold_primary_risk_type": "liquidation",
                    "rule_primary_risk_type": "liquidation",
                    "score_liquidation": 0.2,
                },
            ]
        )
        result = build_transition_risk_analysis(merged, sample_rows=4)
        self.assertEqual(
            result["vulnerable_error_conversions"][
                "false_positive_to_type_mismatch_if_label_is_lowered"
            ],
            1,
        )
        self.assertEqual(
            result["must_preserve_sets"]["correct_low_with_typed_primary_rows"],
            1,
        )

    def test_patch_contract_adds_fail_closed_transition_guards(self) -> None:
        payload = {
            "optimization_contract": {"focus_metric": "false_positive_rows"},
            "sample_cases": {"false_positive": [{"news_id": "21"}]},
            "transition_risk_analysis": {
                "vulnerable_error_conversions": {"cases": [{"news_id": "21"}]},
                "must_preserve_sets": {
                    "correct_low_with_typed_primary_cases": [{"news_id": "22"}]
                },
            },
        }
        contract = build_patch_contract(
            payload,
            {
                "patch_plan": [{"target": "score_liquidation", "action": "narrow guard"}],
                "confidence": "medium",
            },
        )
        self.assertIn("false_positive->type_mismatch", contract["forbidden_transitions"])
        self.assertEqual(contract["must_preserve_news_ids"], ["21", "22"])


class ChangedRowPreflightTests(unittest.TestCase):
    def test_rejects_false_positive_converted_to_type_mismatch(self) -> None:
        baseline = pd.DataFrame(
            [
                {
                    "新闻id": "21",
                    "gold_risk_label": "low",
                    "rule_risk_label": "high",
                    "gold_primary_risk_type": "liquidation",
                    "rule_primary_risk_type": "liquidation",
                    "rule_risk_score": 85,
                    "score_liquidation": 0.85,
                }
            ]
        )
        candidate = baseline.copy()
        candidate["rule_risk_label"] = "low"
        candidate["rule_primary_risk_type"] = "no risk"
        candidate["rule_risk_score"] = 34
        candidate["score_liquidation"] = 0.34

        root = Path.cwd() / "tests"
        baseline_path = root / "_impact_baseline.csv"
        candidate_path = root / "_impact_candidate.csv"
        analysis_path = root / "_impact_analysis.json"
        report_path = root / "_impact_report.json"
        rows_path = root / "_impact_changed.csv"
        temporary_files = [
            baseline_path,
            candidate_path,
            analysis_path,
            report_path,
            rows_path,
        ]
        try:
            baseline.to_csv(baseline_path, index=False, encoding="utf-8-sig")
            candidate.to_csv(candidate_path, index=False, encoding="utf-8-sig")
            analysis_path.write_text(
                json.dumps(
                    {
                        "patch_contract": {
                            "forbidden_transitions": ["false_positive->type_mismatch"],
                            "max_target_regression": {
                                "false_positive": 0,
                                "false_negative": 0,
                                "type_mismatch": 0,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = analyze_candidate_impact(
                baseline_path,
                candidate_path,
                report_path,
                rows_path,
                analysis_path,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
        finally:
            for path in temporary_files:
                path.unlink(missing_ok=True)

        self.assertTrue(result.success)
        self.assertFalse(result.preflight_passed)
        self.assertEqual(report["target_count_deltas"]["type_mismatch"], 1)
        self.assertTrue(
            any(
                item.get("transition") == "false_positive->type_mismatch"
                for item in report["violations"]
            )
        )


class ExperimentIsolationTests(unittest.TestCase):
    def test_versions_root_defaults_to_project_versions(self) -> None:
        project = Path("D:/example_project")
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("RISK_VERSIONS_DIR", None)
            self.assertEqual(resolve_versions_root(project), project / "versions")

    def test_versions_root_can_be_isolated_with_relative_path(self) -> None:
        project = Path.cwd().resolve()
        with patch.dict(
            "os.environ",
            {"RISK_VERSIONS_DIR": "experiments/test_run/versions"},
        ):
            self.assertEqual(
                resolve_versions_root(project),
                (project / "experiments/test_run/versions").resolve(),
            )

    def test_batch_runner_stops_after_rejected_candidate(self) -> None:
        launcher_path = (
            Path.cwd() / "experiments/restart_20260715/run.py"
        ).resolve()
        spec = importlib.util.spec_from_file_location("restart_batch_launcher", launcher_path)
        assert spec and spec.loader
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        launcher.configure_environment()

        @dataclass
        class FakeResult:
            success: bool = True
            candidate_accepted: bool = False
            promotion_decision: str = "rejected"
            compare_winner: str = "v2"
            error_message: str = ""

        import orchestrator

        with patch.object(
            orchestrator,
            "orchestrate_pipeline",
            return_value=FakeResult(),
        ) as pipeline:
            report = launcher.run_iterations(start_version=2, iterations=5)

        self.assertEqual(pipeline.call_count, 1)
        self.assertEqual(report["attempted_iterations"], 1)
        self.assertEqual(report["accepted_iterations"], 0)
        self.assertEqual(report["stop_reason"], "candidate_rejected_at_v2_to_v3")

    def test_orchestrator_revises_rejected_patch_before_returning(self) -> None:
        import orchestrator

        common = {
            "success": True,
            "focus_metric": "false_positive_rows",
            "error_message": "",
            "changed_rows_json": "",
            "compare_json": "",
            "patch_report_json": "",
            "patch_attempts": 0,
            "attempt_history_json": "",
        }
        rejected = SimpleNamespace(
            **common,
            candidate_accepted=False,
            promotion_decision="preflight_rejected",
        )
        accepted = SimpleNamespace(
            **common,
            candidate_accepted=True,
            promotion_decision="accepted",
        )

        with (
            patch.object(
                orchestrator,
                "_orchestrate_pipeline_once",
                side_effect=[rejected, accepted],
            ) as pipeline,
            patch.object(orchestrator, "_archive_preexisting_candidate", return_value={}),
            patch.object(orchestrator, "_copy_attempt_artifacts", return_value=[]),
            patch.object(orchestrator, "write_json"),
            patch.object(orchestrator, "persist_and_return"),
        ):
            result = orchestrator.orchestrate_pipeline(
                current_version=2,
                next_version=3,
                patch_revision_retries=2,
            )

        self.assertTrue(result.candidate_accepted)
        self.assertEqual(result.patch_attempts, 2)
        self.assertEqual(pipeline.call_count, 2)
        second_call = pipeline.call_args_list[1].kwargs
        self.assertTrue(second_call["reuse_analysis"])
        self.assertEqual(
            second_call["patch_revision_feedback"]["rejected_attempt"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
