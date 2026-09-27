from dataclasses import asdict, dataclass

from .common import safe_float
from .metrics import safe_get, summarize_eval, summarize_slice


@dataclass(frozen=True)
class CandidateGateDecision:
    accepted: bool
    stage: str
    reasons: list[str]
    baseline: dict[str, float]
    candidate: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _metric_snapshot(
    eval_data: dict[str, object],
    slice_data: dict[str, object],
) -> dict[str, float]:
    eval_summary = summarize_eval(eval_data)
    slice_summary = summarize_slice(slice_data)
    return {
        "score_mae": eval_summary["score_mae"],
        "score_rmse": eval_summary["score_rmse"],
        "label_accuracy": eval_summary["label_accuracy"],
        "primary_type_accuracy": eval_summary["primary_type_accuracy"],
        "false_positive_rows": float(slice_summary["false_positive_rows"] or 0),
        "false_negative_rows": float(slice_summary["false_negative_rows"] or 0),
        "type_mismatch_rows": float(slice_summary["type_mismatch_rows"] or 0),
    }


def evaluate_candidate_gate(
    analysis_data: dict[str, object],
    baseline_eval: dict[str, object],
    baseline_slice: dict[str, object],
    candidate_eval: dict[str, object],
    candidate_slice: dict[str, object],
) -> CandidateGateDecision:
    strategy = safe_get(analysis_data, "diagnostic_strategy", default={})
    if not isinstance(strategy, dict):
        strategy = {}
    stage = str(strategy.get("active_stage", "type_mismatch_repair"))
    threshold = safe_float(
        strategy.get("type_mismatch_score_stage_threshold", 80),
        80.0,
    )
    baseline = _metric_snapshot(baseline_eval, baseline_slice)
    candidate = _metric_snapshot(candidate_eval, candidate_slice)
    reasons: list[str] = []

    if stage == "score_calibration":
        if candidate["type_mismatch_rows"] > threshold:
            reasons.append(
                "score calibration re-locked type stage: "
                f"type_mismatch {candidate['type_mismatch_rows']:.0f} > {threshold:.0f}"
            )
        if candidate["type_mismatch_rows"] > baseline["type_mismatch_rows"]:
            reasons.append("score calibration increased type_mismatch")
        if candidate["primary_type_accuracy"] < baseline["primary_type_accuracy"]:
            reasons.append("score calibration reduced primary_type_accuracy")
        score_improved = (
            candidate["score_mae"] <= baseline["score_mae"] - 0.10
            or candidate["score_rmse"] <= baseline["score_rmse"] - 0.10
        )
        if not score_improved:
            reasons.append("score calibration did not materially improve MAE or RMSE")
        if candidate["score_mae"] > baseline["score_mae"] + 0.25:
            reasons.append("score calibration increased MAE by more than 0.25")
        if candidate["score_rmse"] > baseline["score_rmse"] + 0.25:
            reasons.append("score calibration increased RMSE by more than 0.25")
        if candidate["label_accuracy"] < baseline["label_accuracy"] - 0.01:
            reasons.append("score calibration reduced label_accuracy by more than 0.01")
    else:
        if candidate["type_mismatch_rows"] >= baseline["type_mismatch_rows"]:
            reasons.append("type repair did not reduce type_mismatch")
        if candidate["primary_type_accuracy"] < baseline["primary_type_accuracy"]:
            reasons.append("type repair reduced primary_type_accuracy")
        if candidate["label_accuracy"] < baseline["label_accuracy"] - 0.02:
            reasons.append("type repair reduced label_accuracy by more than 0.02")
        if candidate["score_mae"] > baseline["score_mae"] + 2.0:
            reasons.append("type repair increased score MAE by more than 2.0")

    if candidate["false_negative_rows"] > baseline["false_negative_rows"] + 1:
        reasons.append("candidate increased false_negative rows by more than 1")

    return CandidateGateDecision(
        accepted=not reasons,
        stage=stage,
        reasons=reasons,
        baseline=baseline,
        candidate=candidate,
    )


def render_candidate_gate_markdown(
    decision: CandidateGateDecision,
    attempt: int,
) -> str:
    lines = [
        "",
        "## Stage-aware quality gate",
        "",
        f"- Attempt: {attempt}",
        f"- Stage: {decision.stage}",
        f"- Accepted: {decision.accepted}",
    ]
    for reason in decision.reasons:
        lines.append(f"- Rejection reason: {reason}")
    return "\n".join(lines) + "\n"
