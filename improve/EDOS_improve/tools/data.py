"""离线核验并读取现有冻结划分；不读取 raw、不下载或重分数据。"""

import csv
import hashlib
import io
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

if __package__:
    from .task import CLASS_TITLES, DATASET, LABEL_IDS, SPLIT_COUNTS
else:
    from task import CLASS_TITLES, DATASET, LABEL_IDS, SPLIT_COUNTS


CSV_FILENAMES = {split: f"{split}.csv" for split in SPLIT_COUNTS}


class DataSplitError(ValueError):
    """数据快照缺失、改变或不符合已确认任务。"""


def split_identity(metadata):
    """指标和绘图共用同一数据身份，禁止混入其他划分的结果。"""
    return {key: metadata[key] for key in ("dataset", "scheme", "counts", "csv_sha256", "label_mapping")}


def load_data(folder):
    data_dir = Path(folder) / "data"
    try:
        metadata = json.loads((data_dir / "split.json").read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise DataSplitError("split.json 顶层必须是对象")
        for field in ("csv_sha256", "csv_schema", "class_counts"):
            if not isinstance(metadata.get(field), dict):
                raise DataSplitError(f"split.json 的 {field} 必须是对象")
        if (metadata.get("dataset") != DATASET
                or metadata.get("scheme") != "preserve_local_source_split_v1"
                or metadata.get("counts") != SPLIT_COUNTS
                or metadata.get("n_samples") != sum(SPLIT_COUNTS.values())
                or metadata.get("label_mapping") != {str(i): title for i, title in enumerate(CLASS_TITLES)}):
            raise DataSplitError("split.json 与已确认任务、类别或划分不一致")
        splits, all_ids = {}, set()
        for split, count in SPLIT_COUNTS.items():
            payload = (data_dir / CSV_FILENAMES[split]).read_bytes()
            if hashlib.sha256(payload).hexdigest() != metadata.get("csv_sha256", {}).get(split):
                raise DataSplitError(f"{split}.csv 已变化，不能重用固定划分")
            columns = ["id", "text"] + ([] if split == "train" else ["label"])
            reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
            if reader.fieldnames != columns or metadata.get("csv_schema", {}).get(split) != columns:
                raise DataSplitError(f"{split}.csv 字段必须为 {columns}")
            rows = list(reader)
            if len(rows) != count:
                raise DataSplitError(f"{split}.csv 必须有 {count} 条")
            for row in rows:
                if (set(row) != set(columns) or not row["id"] or row["id"] in all_ids
                        or not isinstance(row["text"], str)):
                    raise DataSplitError(f"{split}.csv 存在重复编号或无效字段")
                all_ids.add(row["id"])
            if split != "train":
                if any(row["label"] not in LABEL_IDS for row in rows):
                    raise DataSplitError(f"{split}.csv 存在无效标签")
                counts = {title: sum(row["label"] == title for row in rows) for title in CLASS_TITLES}
                if counts != metadata.get("class_counts", {}).get(split):
                    raise DataSplitError(f"{split}.csv 类别条数与冻结清单不一致")
                for row in rows:
                    row["label"] = LABEL_IDS[row["label"]]
            splits[split] = rows
        return splits
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as error:
        raise DataSplitError(f"冻结数据读取失败（{type(error).__name__}），请检查 data 下的三个 CSV 和 split.json") from error


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, columns):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    splits = load_data(Path(__file__).resolve().parents[1])
    print(DATASET + "：" + " / ".join(f"{name}={len(rows)}" for name, rows in splits.items()) + "，冻结数据校验通过。")


if __name__ == "__main__":
    main()
