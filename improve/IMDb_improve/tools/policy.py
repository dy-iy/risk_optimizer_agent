"""按总控传入的覆盖率、准确率阈值确定本轮优化策略。"""

import math
from numbers import Real


def _rate(value, name):
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError(f"{name} 必须是 0 到 1 之间的有限数值")
    return float(value)


def validate_thresholds(low_coverage_threshold, high_covered_accuracy_threshold):
    """阈值只由总控提供，本模块不设置默认判定参数。"""
    _rate(low_coverage_threshold, "低覆盖率阈值")
    _rate(high_covered_accuracy_threshold, "已覆盖高准确率阈值")


def build_strategy(feedback, max_changes, low_coverage_threshold, high_covered_accuracy_threshold):
    validate_thresholds(low_coverage_threshold, high_covered_accuracy_threshold)
    if feedback["aggregator"] != "simple_vote":
        raise ValueError("修改策略必须使用简单投票反馈")
    if type(max_changes) is not int or max_changes < 1:
        raise ValueError("LF 改动上限必须是正整数")
    train = feedback["train_statistics"]
    valid = feedback["valid_metrics"]
    train_coverage = _rate(train["coverage"], "训练输出覆盖率")
    valid_coverage = _rate(valid["coverage"], "验证输出覆盖率")
    accuracy = valid["covered_only"]["accuracy"]
    if accuracy is None:
        if valid_coverage != 0:
            raise ValueError("验证输出覆盖率非零时，已覆盖准确率不能为空")
    else:
        accuracy = _rate(accuracy, "验证已覆盖准确率")
        if valid_coverage == 0:
            raise ValueError("验证集无输出时，已覆盖准确率应为空")
    effective_coverage = min(train_coverage, valid_coverage)
    is_low_coverage = effective_coverage < low_coverage_threshold
    is_high_accuracy = None if accuracy is None else accuracy >= high_covered_accuracy_threshold

    if accuracy is None:
        state = "unknown_accuracy"
        goal = "准确率证据不足，保守建立可靠覆盖。"
        allowed = ["add", "modify"]
    elif is_low_coverage and is_high_accuracy:
        state = "low_coverage_high_accuracy"
        goal = "从未覆盖样例中发现可靠模式，新增 LF 扩大正确覆盖。"
        allowed = ["add"]
    elif is_low_coverage:
        state = "low_coverage_low_accuracy"
        goal = "先修改造成高影响误标的 LF，再考虑可靠覆盖。"
        allowed = ["modify"]
    elif not is_high_accuracy:
        state = "high_coverage_low_accuracy"
        goal = "修改造成误标的 LF，保留正确覆盖。"
        allowed = ["modify"]
    else:
        state = "high_coverage_high_accuracy"
        goal = "针对剩余覆盖缺口或误标，选择新增或修改 LF。"
        allowed = ["add", "modify"]

    # 必须先确定操作再抽样，保证 Analyzer 只看到该操作需要的样例。
    # 双操作状态仍有覆盖缺口时优先新增，完全覆盖后转为修改。
    selected_action = allowed[0] if len(allowed) == 1 else ("add" if effective_coverage < 1 else "modify")

    return {
        "aggregator": feedback["aggregator"],
        "coverage_basis": "simple_vote_output",
        "train_output_coverage": train_coverage,
        "valid_output_coverage": valid_coverage,
        "train_lf_coverage": _rate(train["lf_coverage"], "训练 LF 覆盖率"),
        "valid_lf_coverage": _rate(valid["lf_coverage"], "验证 LF 覆盖率"),
        "effective_coverage": effective_coverage,
        "valid_covered_accuracy": accuracy,
        "low_coverage_threshold": float(low_coverage_threshold),
        "high_covered_accuracy_threshold": float(high_covered_accuracy_threshold),
        "is_low_coverage": is_low_coverage,
        "is_high_accuracy": is_high_accuracy,
        "state": state,
        "optimization_goal": goal,
        "allowed_actions": allowed,
        "selected_action": selected_action,
        "max_changes": max_changes,
    }
