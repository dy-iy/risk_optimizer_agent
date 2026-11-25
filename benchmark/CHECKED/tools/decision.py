"""验证集上的类别均衡净标注收益。"""

from fractions import Fraction
import json

if __package__:
    from .task import CLASS_NAMES
else:
    from task import CLASS_NAMES


def weak_label_quality(confusion):
    """正确记 +1，误标记 -1，弃权记 0；真实类别等权。"""
    contributions = []
    for actual in CLASS_NAMES:
        row = confusion[actual]
        correct = row[actual]
        wrong = sum(row[predicted] for predicted in CLASS_NAMES if predicted != actual)
        support = correct + wrong + row["abstain"]
        if support <= 0:
            raise ValueError(f"验证集缺少真实类别 {actual}，无法计算类别均衡收益")
        contributions.append(Fraction(correct - wrong, support))
    return sum(contributions) / len(contributions)


def was_rejected(version_dir):
    path = version_dir / "results" / "decision.json"
    return path.is_file() and json.loads(path.read_text(encoding="utf-8"))["accepted"] is False
