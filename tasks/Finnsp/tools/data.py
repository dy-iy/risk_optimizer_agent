"""官方 dev 500 条作测试；官方 train 剔除表头后固定分层划分为 3499/500。"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from sklearn.model_selection import train_test_split


OFFICIAL_REPOSITORY = "https://github.com/supersymmetry-technologies/BBT-FinCUGE-Applications"
OFFICIAL_REVISION = "3b686ca79e2184b36498ba15fd1e9609d9eda3cb"
OFFICIAL_BASE_URL = (
    "https://raw.githubusercontent.com/supersymmetry-technologies/BBT-FinCUGE-Applications/"
    f"{OFFICIAL_REVISION}/FinCUGE_Publish/finnsp"
)
SPLIT_COUNTS = {"train": 3499, "valid": 500, "test": 500}
SPLIT_SEED = 42
SPLIT_SCHEME = "official_train_stratified_holdout_official_dev_as_test_v1"
SOURCE_FILES = {"train": "train_list.json", "dev": "eval_list.json"}
SOURCE_COUNTS = {"train": 4000, "dev": 500}
SOURCE_SHA256 = {
    "train": "8e8e84ae7ced4ba5b636a86f9332747d3c93df61c18c1c7f71ddb1d28547460d",
    "dev": "722b4b942fcd5ff2b24a1ab8e0b1e8c1c8905e2dc13a07179293757f4cce92b7",
}
LABELS = ("0", "1")
SOURCE_HEADER = ["id", "title", "text", "entity", "negative", "key_entity"]


class DataSplitError(ValueError):
    """数据来源、划分或标签不符合实验约定，不属于 LF 技术错误。"""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path, columns=None):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if columns is not None and reader.fieldnames != columns:
            raise DataSplitError(f"{Path(path).name} 字段不符合约定")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise DataSplitError(f"{Path(path).name} 存在列数不一致的记录")
    return rows


def write_csv(path, rows, columns):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def parse_inputs(value):
    inputs = json.loads(value) if isinstance(value, str) else value
    if not isinstance(inputs, dict) or set(inputs) != {"entities"}:
        raise DataSplitError("inputs 只能包含 entities 候选实体列表")
    entities = inputs["entities"]
    if not isinstance(entities, list) or any(not isinstance(item, str) or not item.strip() for item in entities):
        raise DataSplitError("候选实体必须是字符串列表；源文件候选实体为空时使用空列表")
    # 官方存在空候选列表和重复候选；不删样本，也不改写候选实体内容或顺序。
    return {"entities": list(entities)}


def lf_record(row):
    """每次复制输入，只向 LF 提供正文和候选实体，不提供编号、金标或来源。"""
    return {"text": row["text"], "inputs": parse_inputs(row["inputs"])}


def _source_record(record, source_name):
    """官方六列格式为 id、标题、正文、候选实体、二分类标签、负面主体。"""
    if not isinstance(record, list) or len(record) != 6:
        raise DataSplitError(f"{source_name} 不是官方六列记录")
    source_id, _title, text, entities, label, _subjects = record
    if not isinstance(source_id, str) or not source_id.strip() or not isinstance(text, str) or not text.strip():
        raise DataSplitError(f"{source_name} 存在空编号或正文")
    if not isinstance(entities, str) or isinstance(label, bool) or str(label) not in LABELS:
        raise DataSplitError(f"{source_name} 存在无效的候选实体字段或 0/1 标签")
    inputs = parse_inputs({"entities": entities.split(";") if entities else []})
    return {"source_id": source_id, "text": text,
            "inputs": json.dumps(inputs, ensure_ascii=False), "label": str(label)}


def _load_sources(source_paths=None):
    if source_paths is not None and set(source_paths) != set(SOURCE_FILES):
        raise DataSplitError("本地导入必须同时提供官方 train 和 dev 文件")
    records, sources = {}, {}
    for name, filename in SOURCE_FILES.items():
        url = f"{OFFICIAL_BASE_URL}/{filename}"
        if source_paths is None:
            with urlopen(url, timeout=30) as response:
                raw = response.read()
        else:
            raw = Path(source_paths[name]).read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
        if sha256 != SOURCE_SHA256[name]:
            raise DataSplitError(f"官方 {filename} SHA-256 校验失败，未修改现有划分")
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, list) or len(data) != SOURCE_COUNTS[name]:
            raise DataSplitError(f"官方 {filename} 必须有 {SOURCE_COUNTS[name]} 行")
        rows, removed = [], []
        for index, record in enumerate(data):
            header = (isinstance(record, list) and len(record) == 6
                      and isinstance(record[0], str)
                      and [record[0].lstrip("\ufeff"), *record[1:]] == SOURCE_HEADER)
            if header:
                removed.append({"index": index, "reason": "embedded_header", "fields": record})
            else:
                rows.append(_source_record(record, name))
        if len(removed) != (1 if name == "train" else 0):
            raise DataSplitError(f"{filename} 的混入表头数量与固定版本不一致")
        if len({row["source_id"] for row in rows}) != len(rows):
            raise DataSplitError(f"{filename} 存在重复编号")
        records[name] = rows
        sources[name] = {"url": url, "sha256": sha256, "raw_count": len(data),
                         "n_samples": len(rows), "removed_rows": removed}
    if {row["source_id"] for row in records["train"]} & {row["source_id"] for row in records["dev"]}:
        raise DataSplitError("官方 train 和 dev 存在重复编号")
    return records, sources


def create_split(folder, source_paths=None):
    """校验固定源文件后一次划分，按源顺序输出；训练金标只用于分层，不落盘。"""
    records, sources = _load_sources(source_paths)
    official_train = records["train"]
    train_indices, valid_indices = train_test_split(
        list(range(len(official_train))),
        test_size=SPLIT_COUNTS["valid"], random_state=SPLIT_SEED,
        stratify=[row["label"] for row in official_train],
    )
    selected = {
        "train": [official_train[i] for i in sorted(train_indices)],
        "valid": [official_train[i] for i in sorted(valid_indices)],
        "test": records["dev"],
    }
    if {name: len(rows) for name, rows in selected.items()} != SPLIT_COUNTS:
        raise DataSplitError("有效样本数不符合确认的 3499/500/500 划分")
    splits = {}
    for name, rows in selected.items():
        splits[name] = [
            {"id": f"finnsp_{name}_{row['source_id']}", "text": row["text"], "inputs": row["inputs"],
             **({"label": row["label"]} if name != "train" else {})}
            for row in rows
        ]
    data_dir = Path(folder) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        columns = ["id", "text", "inputs"] + ([] if name == "train" else ["label"])
        write_csv(data_dir / f"{name}.csv", rows, columns)
    metadata = {
        "dataset": "FinNSP", "revision": OFFICIAL_REVISION, "source_reference": OFFICIAL_REPOSITORY,
        "scheme": SPLIT_SCHEME, "seed": SPLIT_SEED, "sources": sources,
        "n_samples": sum(SPLIT_COUNTS.values()), "counts": SPLIT_COUNTS,
        "csv_sha256": {name: digest(data_dir / f"{name}.csv") for name in splits},
        "class_counts": {
            name: {label: sum(row["label"] == label for row in splits[name]) for label in LABELS}
            for name in ("valid", "test")
        },
        "source_membership": {name: [row["source_id"] for row in rows] for name, rows in selected.items()},
        "split_sources": {"train": "official_train", "valid": "official_train", "test": "official_dev"},
        "roles": {"train": "仅用 LF 投票拟合模型，不保存金标或负面主体",
                  "valid": "类别先验、开发反馈和 LF 质量分析",
                  "test": "官方 dev 的全部 500 条，仅评价，不进入拟合、先验或 agent 反馈"},
        "sampling": "官方 train 剔除 1 行混入表头后，按 0/1 分层随机抽 500 条验证，其余 3499 条训练；官方 dev 全部作为测试。各集合保留源顺序，不按正文去重或分组。",
        "training_source_gold_usage": "stratified_split_only_then_removed",
        "input_normalization": "空候选实体串转为空列表；保留重复候选、候选顺序和正文。",
        "original_csv_used": False,
    }
    (data_dir / "split.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_data(folder):
    """已有快照离线核验重用，缺失或变化直接报错，不自动重分。"""
    data_dir = Path(folder) / "data"
    metadata_path = data_dir / "split.json"
    paths = {name: data_dir / f"{name}.csv" for name in SPLIT_COUNTS}
    existing = [path.exists() for path in [*paths.values(), metadata_path]]
    if not any(existing):
        create_split(folder)
    elif not all(existing):
        raise DataSplitError("固定划分不完整，请检查 data/train.csv、valid.csv、test.csv 和 split.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (metadata.get("dataset") != "FinNSP" or metadata.get("counts") != SPLIT_COUNTS
            or metadata.get("n_samples") != sum(SPLIT_COUNTS.values())
            or metadata.get("revision") != OFFICIAL_REVISION or metadata.get("scheme") != SPLIT_SCHEME
            or metadata.get("seed") != SPLIT_SEED or metadata.get("source_reference") != OFFICIAL_REPOSITORY
            or metadata.get("split_sources") != {"train": "official_train", "valid": "official_train", "test": "official_dev"}):
        raise DataSplitError("已有数据不是当前确认的 3499/500/500 划分，请显式重新导入")
    for name, filename in SOURCE_FILES.items():
        source = metadata.get("sources", {}).get(name, {})
        if (source.get("url") != f"{OFFICIAL_BASE_URL}/{filename}"
                or source.get("sha256") != SOURCE_SHA256[name]
                or source.get("raw_count") != SOURCE_COUNTS[name]
                or source.get("n_samples") != SOURCE_COUNTS[name] - (name == "train")):
            raise DataSplitError(f"{name} 的官方来源记录不一致")
    splits, all_source_ids = {}, set()
    for name, path in paths.items():
        if digest(path) != metadata.get("csv_sha256", {}).get(name):
            raise DataSplitError(f"{name}.csv 已变化，不能重用固定划分")
        columns = ["id", "text", "inputs"] + ([] if name == "train" else ["label"])
        rows = read_csv(path, columns)
        if len(rows) != SPLIT_COUNTS[name]:
            raise DataSplitError(f"{name}.csv 必须有 {SPLIT_COUNTS[name]} 条")
        source_ids = metadata.get("source_membership", {}).get(name)
        if (not isinstance(source_ids, list) or len(source_ids) != len(rows)
                or any(not isinstance(source_id, str) or not source_id for source_id in source_ids)
                or len(set(source_ids)) != len(rows) or set(source_ids) & all_source_ids
                or [row["id"] for row in rows] != [f"finnsp_{name}_{source_id}" for source_id in source_ids]
                or any(not row["text"].strip() for row in rows)):
            raise DataSplitError(f"{name}.csv 的编号、来源归属或正文无效")
        for row in rows:
            row["inputs"] = parse_inputs(row["inputs"])
        if name != "train":
            if any(row["label"] not in LABELS for row in rows):
                raise DataSplitError(f"{name}.csv 存在无效标签")
            counts = {label: sum(row["label"] == label for row in rows) for label in LABELS}
            if counts != metadata.get("class_counts", {}).get(name):
                raise DataSplitError(f"{name}.csv 类别条数与固定划分记录不一致")
        all_source_ids.update(source_ids)
        splits[name] = rows
    return splits


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, help="可选：固定版本官方 train_list.json 的本地原文件")
    parser.add_argument("--dev", type=Path, help="可选：固定版本官方 eval_list.json 的本地原文件")
    args = parser.parse_args()
    if (args.train is None) != (args.dev is None):
        parser.error("--train 和 --dev 必须同时提供")
    try:
        folder = Path(__file__).resolve().parents[1]
        create_split(folder, {"train": args.train, "dev": args.dev} if args.train is not None else None)
        load_data(folder)
    except (ValueError, OSError) as error:
        parser.exit(1, f"导入失败：{error}\n")
    print("已冻结 train=3499 / valid=500 / test=500；训练不含金标，测试来自官方 dev。")


if __name__ == "__main__":
    main()
