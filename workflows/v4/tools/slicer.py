"""Write category and level mismatch rows."""

import json
from pathlib import Path

from .common import parse_categories, read_csv, write_csv


def slice_errors(merged_csv: Path, paths: dict[str, Path]) -> dict:
    data = read_csv(merged_csv)
    gold_types = data["gold_风险类别"].apply(parse_categories)
    pred_types = data["pred_风险类别"].apply(parse_categories)

    type_mismatch = data[gold_types != pred_types].copy()
    mismatch_indexes = type_mismatch.index
    type_mismatch["漏报类别"] = [
        json.dumps(sorted(gold_types[index] - pred_types[index]), ensure_ascii=False)
        for index in mismatch_indexes
    ]
    type_mismatch["误报类别"] = [
        json.dumps(sorted(pred_types[index] - gold_types[index]), ensure_ascii=False)
        for index in mismatch_indexes
    ]

    level_mismatch = data[data["gold_风险等级"] != data["pred_风险等级"]].copy()

    write_csv(type_mismatch, paths["type_mismatch_csv"])
    write_csv(level_mismatch, paths["level_mismatch_csv"])
    return {
        "type_mismatch_rows": len(type_mismatch),
        "level_mismatch_rows": len(level_mismatch),
    }
