"""使用选择集真实投票判定候选；分数和样本不进入 Analyzer/Patcher。"""

from dataclasses import dataclass
import math

from .impact import compare_split


@dataclass(frozen=True)
class AcceptanceConfig:
    enabled: bool
    min_net_correct_gain: int
    max_newly_wrong: int
    max_regressed: int
    min_macro_f1_delta: float
    max_covered_accuracy_drop: float
    min_add_train_output_gain: int

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("验收开关必须是布尔值")
        for key in ("min_net_correct_gain", "max_newly_wrong", "max_regressed", "min_add_train_output_gain"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError(f"{key} 必须为非负整数")
        if not math.isfinite(self.min_macro_f1_delta) or not -1 <= self.min_macro_f1_delta <= 1:
            raise ValueError("Macro-F1 变化门槛须在 [-1,1] 内")
        if not math.isfinite(self.max_covered_accuracy_drop) or not 0 <= self.max_covered_accuracy_drop <= 1:
            raise ValueError("已覆盖准确率允许下降值须在 [0,1] 内")


def assess_candidate(old_result, new_result, action, config):
    before, after = (r["aggregators"]["simple_vote"] for r in (old_result, new_result))
    if before["metrics"]["development_roles"] != after["metrics"]["development_roles"]:
        raise ValueError("候选验收必须使用相同的开发角色清单")
    summary, _ = compare_split(before["selection"], after["selection"], before["lf_names"], after["lf_names"],
                               after["selection"]["rows"], with_gold=True)
    quality = summary["validation_quality"]
    old_metrics, new_metrics = (r["metrics"]["selection"] for r in (before, after))
    macro_delta = new_metrics["all_samples"]["macro_f1"] - old_metrics["all_samples"]["macro_f1"]
    old_q, new_q = (m["covered_only"]["accuracy"] for m in (old_metrics, new_metrics))
    train_gain = after["metrics"]["train"]["n_labeled"] - before["metrics"]["train"]["n_labeled"]
    reasons = []
    if quality["net_correct_gain"] < config.min_net_correct_gain:
        reasons.append("net_correct_gain_below_minimum")
    if quality["newly_wrong_samples"] > config.max_newly_wrong:
        reasons.append("too_many_new_wrong_outputs")
    if quality["regressed_samples"] > config.max_regressed:
        reasons.append("too_many_previous_correct_regressions")
    if macro_delta + 1e-12 < config.min_macro_f1_delta:
        reasons.append("macro_f1_delta_below_minimum")
    if old_q is not None and (new_q is None or new_q + config.max_covered_accuracy_drop + 1e-12 < old_q):
        reasons.append("covered_accuracy_drop_exceeded")
    if action == "add":
        if quality["new_correct_from_all_abstain"] < 1:
            reasons.append("no_correct_output_from_all_abstain")
        if train_gain < config.min_add_train_output_gain:
            reasons.append("train_output_gain_below_minimum")
    return {"eligible": not reasons if config.enabled else True, "gating_enabled": config.enabled,
            "reasons": reasons, "selection_quality": quality, "macro_f1_delta": macro_delta,
            "covered_accuracy_before": old_q, "covered_accuracy_after": new_q,
            "train_output_gain": train_gain,
            "rank": [quality["net_correct_gain"], macro_delta, -quality["newly_wrong_samples"], train_gain]}
