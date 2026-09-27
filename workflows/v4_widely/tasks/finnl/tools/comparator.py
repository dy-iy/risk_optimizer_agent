"""版本比较工具：按质量分和指标最大下降幅度判断是否接受候选。"""

def compare(task, previous, candidate):
    old = round(sum(previous[k] * w for k, w in task.weights.items()), 6)
    new = round(sum(candidate[k] * w for k, w in task.weights.items()), 6)
    reasons = []
    if new <= old:
        reasons.append("quality score did not strictly improve")
    for key, limit in task.max_drops.items():
        if round(candidate[key] - previous[key], 6) < -limit:
            reasons.append(f"{key} dropped by more than {limit}")
    return {"accepted": not reasons, "previous_score": old, "candidate_score": new,
            "reasons": reasons, "previous_metrics": previous, "candidate_metrics": candidate}
