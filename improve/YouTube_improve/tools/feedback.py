"""为 Analyzer 构造并保存训练/验证反馈；不读取测试信息。"""

import json
from pathlib import Path
import random

import numpy as np


if __package__:
    from .task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, label_id
else:
    from task import CARDINALITY, CLASS_NAMES, CLASS_TITLES, label_id


def _json_value(value):
    """把 numpy 数值转成可直接保存、发送的 JSON 值。"""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"无法序列化 {type(value).__name__}")


def build_feedback(result, current_code, version_name, max_examples=10, seed=42, *, action):
    """按已选操作构造样例：add 两组各 2n，modify 三组各 n；不读取测试信息。"""
    if type(max_examples) is not int or max_examples < 0:
        raise ValueError("反馈样例基数 n 必须是非负整数")
    if action not in {"add", "modify"}:
        raise ValueError("反馈操作必须为 add 或 modify")
    result = result["aggregators"]["simple_vote"]
    names = result["lf_names"]
    valid = result["valid"]
    train = result["train"]
    valid_L = np.asarray(valid["L"])
    train_L = np.asarray(train["L"])
    predictions = np.asarray(valid["predictions"])
    gold = np.asarray([
        label_id(row["label"])
        for row in valid["rows"]
    ], dtype=int)
    # Random 对字符串种子的处理稳定，不依赖每次进程随机化的 hash()。
    rng = random.Random(f"{seed}:{version_name}:{action}")

    def examples(rows, matrix, mask, limit, with_gold=False):
        indices = np.flatnonzero(mask).tolist()
        chosen = rng.sample(indices, min(limit, len(indices)))
        selected = []
        for index in chosen:
            row = rows[index]
            item = {
                "id": row["id"],
                "text": row["text"],
                "votes": {name: vote for name, vote in zip(names, matrix[index].tolist()) if vote != -1},
            }
            if with_gold:
                item["gold"] = int(gold[index])
                item["prediction"] = int(predictions[index])
            selected.append(item)
        return selected

    if action == "add":
        specs = {
            "valid_abstained": (valid["rows"], valid_L, predictions == -1, 2 * max_examples, True),
            "train_uncovered": (train["rows"], train_L, np.all(train_L == -1, axis=1), 2 * max_examples, False),
        }
    else:
        conflict = sum((train_L == label).any(axis=1).astype(int) for label in range(CARDINALITY)) > 1
        specs = {
            "valid_wrong": (valid["rows"], valid_L, (predictions != -1) & (predictions != gold), max_examples, True),
            "valid_correct": (valid["rows"], valid_L, predictions == gold, max_examples, True),
            "train_conflict": (train["rows"], train_L, conflict, max_examples, False),
        }
    groups = {name: examples(*spec) for name, spec in specs.items()}
    # 只白名单提取开发反馈；不把整个 metrics 或 result 发给模型。
    feedback = {
        "version": version_name,
        "aggregator": "simple_vote",
        "label_values": {"-1": "abstain", **{str(i): title for i, title in enumerate(CLASS_TITLES)}},
        "lf_code": current_code,
        "lf_names": names,
        "valid_metrics": result["metrics"]["valid"],
        "valid_class_coverage": {
            label: {
                "n_samples": int((gold == value).sum()),
                "n_covered": int(((gold == value) & (valid_L != -1).any(axis=1)).sum()),
                "n_uncovered": int(((gold == value) & (valid_L == -1).all(axis=1)).sum()),
            }
            for value, label in enumerate(CLASS_NAMES)
        },
        "train_statistics": result["metrics"]["train"],
        "lf_quality": result["lf_quality"],
        "example_sampling": {
            "action": action,
            "base_n": max_examples,
            "limits": {name: spec[3] for name, spec in specs.items()},
            "available": {name: int(spec[2].sum()) for name, spec in specs.items()},
        },
        "examples": groups,
    }
    return json.loads(json.dumps(feedback, ensure_ascii=False, default=_json_value))


def save_feedback(output, feedback):
    """保存本轮反馈快照；重跑同一版本时覆盖。"""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "feedback.json"
    path.write_text(
        json.dumps(feedback, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path
