"""离线核验并读取现有冻结划分；不读取 raw、不下载或重分数据。"""

from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

if __package__:
    from .task import CLASS_NAMES, CLASS_TITLES, DATASET, SPLIT_COUNTS
else:
    from task import CLASS_NAMES, CLASS_TITLES, DATASET, SPLIT_COUNTS


CSV_FILENAMES = {"train": "train.csv", "valid": "validation.csv", "test": "test.csv"}
CSV_COLUMNS = ["id", "youtube_code", "text", "label"]


class DataSplitError(ValueError):
    """数据快照缺失、改变或不符合已确认任务。"""


def split_identity(metadata):
    """指标和绘图共用同一数据身份，禁止混入其他划分的结果。"""
    return {key: metadata[key] for key in ("dataset", "scheme", "counts", "csv_sha256", "label_mapping")}


def load_data(folder, *, include_test=True):
    """核验原始 CSV，再将数据投影为 LF 流程使用的字段。"""
    data_dir = Path(folder) / "data"
    try:
        metadata = json.loads((data_dir / "split.json").read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise DataSplitError("split.json 顶层必须是对象")
        for field in ("csv_sha256", "class_counts", "video_counts"):
            if not isinstance(metadata.get(field), dict):
                raise DataSplitError(f"split.json 的 {field} 必须是对象")
        if (metadata.get("dataset") != DATASET
                or metadata.get("scheme") != "preserve_local_source_split_v1"
                or metadata.get("counts") != SPLIT_COUNTS
                or metadata.get("n_samples") != sum(SPLIT_COUNTS.values())
                or metadata.get("label_mapping") != dict(zip(CLASS_NAMES, CLASS_TITLES))
                or metadata.get("csv_filenames") != CSV_FILENAMES
                or metadata.get("csv_schema") != CSV_COLUMNS):
            raise DataSplitError("split.json 与已确认任务、类别或划分不一致")
        splits, all_ids, all_videos = {}, set(), set()
        for split, count in SPLIT_COUNTS.items():
            if split == "test" and not include_test:
                continue
            filename = CSV_FILENAMES[split]
            payload = (data_dir / filename).read_bytes()
            if hashlib.sha256(payload).hexdigest() != metadata["csv_sha256"].get(split):
                raise DataSplitError(f"{filename} 已变化，不能重用固定划分")
            reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
            if reader.fieldnames != CSV_COLUMNS:
                raise DataSplitError(f"{filename} 字段必须为 {CSV_COLUMNS}")
            rows, videos, labels = [], set(), Counter()
            for row in reader:
                if (None in row or not all(row.values()) or not row["text"].strip()
                        or row["id"] in all_ids or row["label"] not in CLASS_NAMES):
                    raise DataSplitError(f"{filename} 存在重复编号或无效字段")
                all_ids.add(row["id"])
                videos.add(row["youtube_code"])
                labels[row["label"]] += 1
                item = {"id": row["id"], "text": row["text"]}
                if split != "train":
                    item["label"] = int(row["label"])
                rows.append(item)
            if len(rows) != count:
                raise DataSplitError(f"{filename} 必须有 {count} 条")
            if labels != metadata["class_counts"].get(split):
                raise DataSplitError(f"{filename} 类别条数与冻结清单不一致")
            if videos & all_videos or len(videos) != metadata["video_counts"].get(split):
                raise DataSplitError(f"{filename} 视频跨集合重叠或视频数与冻结清单不一致")
            all_videos.update(videos)
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
