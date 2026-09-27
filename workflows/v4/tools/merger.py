"""Merge gold labels and script predictions by 新闻id."""

from pathlib import Path

from .common import read_csv, write_csv


LABEL_COLUMNS = ["风险等级", "风险类别", "原因"]


def merge_results(gold_csv: Path, prediction_csv: Path, output_csv: Path) -> int:
    gold = read_csv(gold_csv)
    pred = read_csv(prediction_csv)

    required = {"新闻id", *LABEL_COLUMNS}
    if not required.issubset(gold.columns) or not required.issubset(pred.columns):
        raise ValueError("训练集或预测文件缺少标签列")
    if gold["新闻id"].duplicated().any() or pred["新闻id"].duplicated().any():
        raise ValueError("新闻id 必须唯一")

    gold = gold.rename(columns={column: f"gold_{column}" for column in LABEL_COLUMNS})
    pred = pred[["新闻id", *LABEL_COLUMNS]].rename(
        columns={column: f"pred_{column}" for column in LABEL_COLUMNS}
    )
    merged = gold.merge(pred, on="新闻id", how="left", validate="one_to_one")
    if merged["pred_风险等级"].isna().any():
        raise ValueError("部分训练数据没有预测结果")

    write_csv(merged, output_csv)
    return len(merged)

