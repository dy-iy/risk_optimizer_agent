"""错例切片工具：各输出字段取前 20 条错误，附加本任务诊断信息。"""

def _base_errors(task, records, predictions):
    indexed = {p["id"]: p for p in predictions}
    selected = []
    fields = [task.field, "level"] if task.kind == "joint" else [task.field]
    for field in fields:
        count = 0
        for record in records:
            result = indexed[record["id"]]
            gold = record["gold"][field]
            pred = result["prediction"][field]
            gold_set, pred_set = (set(gold), set(pred)) if isinstance(gold, list) else ({gold}, {pred})
            if gold_set == pred_set:
                continue
            if count >= 20 or len(selected) >= task.max_errors:
                break
            selected.append({"id": record["id"], "text": record["text"],
                "inputs": record["inputs"], "field": field,
                "missed_labels": sorted(gold_set - pred_set),
                "spurious_labels": sorted(pred_set - gold_set),
                "gold": gold, "prediction": pred, "votes": result["votes"]})
            count += 1
    return selected


def attach_facets(errors, function):
    return [{**error, "task_diagnostics": function(error)} for error in errors]


def sample_errors(task, records, predictions):
    return attach_facets(_base_errors(task, records, predictions), lambda e: {
        "neutral_boundary": "neutral" in (e["gold"], e["prediction"]),
        "negation_cue": any(w in e["text"].lower().split() for w in ("not", "no", "never", "without")),
        "comparison_cue": any(w in e["text"].lower() for w in ("compared", "versus", "from", "than"))})
