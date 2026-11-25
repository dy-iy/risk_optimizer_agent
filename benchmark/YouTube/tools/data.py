"""导入并校验 WRENCH 官方 YouTube 固定划分；训练集不保存真实标签。"""

import csv
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


WRENCH_REVISION = "d1185f6cfba8d5ace2a39229799cb3f1c136bf66"
WRENCH_BASE_URL = (
    "https://huggingface.co/datasets/jieyuz2/WRENCH/resolve/"
    f"{WRENCH_REVISION}/classification/youtube"
)
SPLIT_COUNTS = {"train": 1686, "valid": 120, "test": 250}
SOURCE_SHA256 = {
    "train": "8c487acf4e3e16b340e28f3633d5613f83c4db2d3b287e4341c86ff8f2192705",
    "valid": "01d28d71c48e34bdde0566a9eead1d9694baa4eae176cea6d979bb59c4516df3",
    "test": "ef04412aee047657e41d7ad1411f11d05fce13b642bf1bec8897d138343ee12d",
}


def read_csv(path, expected=None):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if expected is not None and reader.fieldnames != expected:
            raise ValueError(f"{path.name} 字段必须是 {expected}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path.name} 存在列数不一致的记录")
    return rows


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def create_split(data_dir):
    """按固定提交导入官方正文、顺序和划分，不重新抽样或合并重复正文。"""
    data_dir = Path(data_dir)
    splits, sources = {}, {}
    for name, count in SPLIT_COUNTS.items():
        url = f"{WRENCH_BASE_URL}/{name}.json"
        with urlopen(url, timeout=30) as response:
            raw = response.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != SOURCE_SHA256[name]:
            raise ValueError(f"WRENCH {name}.json 校验失败，未写入数据划分")
        records = json.loads(raw)
        if not isinstance(records, dict) or len(records) != count:
            raise ValueError(f"WRENCH {name}.json 必须有 {count} 条数据")
        rows = []
        for source_id, record in records.items():
            text, label = record["data"]["text"], record["label"]
            if not isinstance(text, str) or label not in (0, 1):
                raise ValueError(f"WRENCH {name}.json 中有无效正文或标签")
            row = {"id": f"youtube_{name}_{source_id}", "text": text}
            if name != "train":
                row["label"] = {0: "ham", 1: "spam"}[label]
            rows.append(row)
        splits[name] = rows
        sources[name] = {"url": url, "sha256": digest}

    # 所有下载和源文件校验成功后再写入；不落盘官方训练金标或预定义弱标签。
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_hashes = {}
    for name, rows in splits.items():
        path = data_dir / f"{name}.csv"
        columns = ["id", "text"] if name == "train" else ["id", "text", "label"]
        write_csv(path, rows, columns)
        csv_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = {
        "dataset": "WRENCH/YouTube",
        "revision": WRENCH_REVISION,
        "sources": sources,
        "n_samples": sum(SPLIT_COUNTS.values()),
        "counts": SPLIT_COUNTS,
        "csv_sha256": csv_hashes,
        "class_counts": {
            name: {label: sum(row["label"] == label for row in splits[name])
                   for label in ("ham", "spam")}
            for name in ("valid", "test")
        },
        "roles": {"train": "仅用 LF 投票拟合弱监督模型，不保存金标",
                  "valid": "向 agent 提供指标、错例和 LF 质量反馈",
                  "test": "仅评价，不进入 agent 反馈或模型拟合"},
        "sampling": "直接使用官方 train/valid/test 的正文、顺序和样本归属；不抽样、不去重。",
        "original_csv_used": False,
    }
    (data_dir / "split.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_data(folder):
    """返回 train / valid / test；已有文件离线校验，训练行始终只有 id、text。"""
    data_dir = Path(folder) / "data"
    paths = {name: data_dir / f"{name}.csv" for name in SPLIT_COUNTS}
    metadata_path = data_dir / "split.json"
    if any((data_dir / f"{name}.csv").exists() for name in ("labeled", "unlabeled")):
        raise ValueError("data 中仍有旧随机划分，请先归档旧 data，再导入 WRENCH train/valid/test")
    existing = [path.exists() for path in [*paths.values(), metadata_path]]
    if not any(existing):
        create_split(data_dir)
    elif not all(existing):
        raise ValueError("固定划分不完整，请检查 data/train.csv、valid.csv、test.csv 和 split.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (metadata.get("dataset") != "WRENCH/YouTube"
            or metadata.get("revision") != WRENCH_REVISION
            or metadata.get("counts") != SPLIT_COUNTS
            or metadata.get("n_samples") != sum(SPLIT_COUNTS.values())):
        raise ValueError("已有数据不是当前 WRENCH 官方固定划分，请重新导入")

    splits, all_ids = {}, set()
    for name, path in paths.items():
        source = metadata.get("sources", {}).get(name, {})
        if (source.get("sha256") != SOURCE_SHA256[name]
                or source.get("url") != f"{WRENCH_BASE_URL}/{name}.json"):
            raise ValueError(f"{name} 的 WRENCH 来源记录不一致")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("csv_sha256", {}).get(name):
            raise ValueError(f"{name}.csv 已变化，不能重用固定划分")
        samples = read_csv(path, ["id", "text"] if name == "train" else ["id", "text", "label"])
        expected = {"id", "text"} if name == "train" else {"id", "text", "label"}
        if len(samples) != SPLIT_COUNTS[name] or any(set(row) != expected for row in samples):
            raise ValueError(f"{name}.csv 条数或字段不符合约定")
        ids = {row["id"] for row in samples}
        if (len(ids) != len(samples) or ids & all_ids
                or any(not row["id"].startswith(f"youtube_{name}_") for row in samples)):
            raise ValueError("数据划分中有重复编号或无效的 WRENCH 编号")
        if name != "train":
            if any(row["label"] not in ("ham", "spam") for row in samples):
                raise ValueError(f"{name}.csv 存在无效标签")
            counts = {label: sum(row["label"] == label for row in samples) for label in ("ham", "spam")}
            if counts != metadata.get("class_counts", {}).get(name):
                raise ValueError(f"{name}.csv 类别条数与固定划分不一致")
        # 官方按样本划分，允许相同正文跨集合；不可在这里重新分组或去重。
        all_ids.update(ids)
        splits[name] = samples
    return splits


def split_identity(metadata):
    return {key: metadata[key] for key in ("dataset", "revision", "counts", "csv_sha256")}


if __name__ == "__main__":
    print("YouTube：" + " / ".join(f"{name}={len(rows)}" for name, rows in load_data(Path(__file__).resolve().parents[1]).items()) + "，固定划分校验通过。")
