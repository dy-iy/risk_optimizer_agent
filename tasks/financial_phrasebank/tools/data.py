"""固定总计 100 条标注样本、未标注集和测试集；只在首次划分读取源金标。"""

import csv
import hashlib
import json
from pathlib import Path
import random
import re

if __package__:
    from .task import CLASS_NAMES, LABEL_IDS
else:
    from task import CLASS_NAMES, LABEL_IDS

SPLIT_SEED = 42
TEST_FRACTION = 0.2
# 与 SMS 保持相同的累计 100 条预算；三类尽量均衡。
LABELED_CLASS_COUNTS = {"negative": 34, "neutral": 33, "positive": 33}
SPLIT_NAMES = ("labeled", "unlabeled", "test")
SOURCE_COLUMNS = ["id", "text", "label", "label_name", "agreement_level", "source_line"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_key(text):
    return re.sub(r"\s+", "", text)


def read_csv(path, columns):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise ValueError(f"{path.name} 字段不符合约定")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path.name} 存在列数不一致的记录")
    return rows


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: row[key] for key in columns} for row in rows)


def create_split(folder):
    folder = Path(folder)
    source = folder / "financial_phrasebank.csv"
    raw = read_csv(source, SOURCE_COLUMNS)
    groups, ids = {}, set()
    for row in raw:
        label = row["label_name"]
        if (not row["id"].isdigit() or row["id"] in ids or not text_key(row["text"])
                or label not in LABEL_IDS or row["label"] != str(LABEL_IDS[label])):
            raise ValueError("源数据存在重复/无效编号、空正文或标签映射错误")
        ids.add(row["id"])
        # 丢弃标签名称、agreement_level 和 source_line，LF 只能收到正文。
        record = {"id": row["id"], "text": row["text"], "label": row["label"]}
        groups.setdefault(text_key(row["text"]), []).append(record)
    if any(len({row["label"] for row in group}) != 1 for group in groups.values()):
        raise ValueError("忽略空白后的相同正文仍有冲突标签，需先清理源数据")

    rng = random.Random(SPLIT_SEED)
    labeled, unlabeled, test = [], [], []
    for label in CLASS_NAMES:
        candidates = [group for group in groups.values() if group[0]["label"] == str(LABEL_IDS[label])]
        rng.shuffle(candidates)
        target = round(sum(map(len, candidates)) * TEST_FRACTION)
        counts = [0]
        for group in candidates:
            counts.append(counts[-1] + len(group))
        cut = min(range(len(counts)), key=lambda i: abs(counts[i] - target))
        test.extend(row for group in candidates[:cut] for row in group)
        remaining = candidates[cut:]
        rng.shuffle(remaining)
        needed = LABELED_CLASS_COUNTS[label]
        for group in remaining:
            if len(group) <= needed:
                labeled.extend(group)
                needed -= len(group)
            else:
                unlabeled.extend(group)
        if needed:
            raise ValueError(f"无法按正文整组凑满 {label} 的 {LABELED_CLASS_COUNTS[label]} 条标注样本")

    splits = {"labeled": labeled, "unlabeled": unlabeled, "test": test}
    data_dir = folder / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        rows.sort(key=lambda row: int(row["id"]))
        columns = ["id", "text"] + ([] if name == "unlabeled" else ["label"])
        write_csv(data_dir / f"{name}.csv", rows, columns)
    metadata = {
        "task": "financial_phrasebank", "seed": SPLIT_SEED, "test_fraction": TEST_FRACTION,
        "source": source.name, "source_sha256": digest(source), "n_samples": len(raw),
        "label_mapping": LABEL_IDS, "labeled_budget": sum(LABELED_CLASS_COUNTS.values()),
        "counts": {name: len(rows) for name, rows in splits.items()},
        "labeled_class_counts": LABELED_CLASS_COUNTS,
        "test_class_counts": {label: sum(row["label"] == str(LABEL_IDS[label]) for row in test) for label in CLASS_NAMES},
        "split_sha256": {name: digest(data_dir / f"{name}.csv") for name in SPLIT_NAMES},
        "group_key": "正文去除空白后相同，整组分配，不跨集合",
        "sampling": "先按类别抽约20%测试集，再抽负面34/中性33/正面33条固定标注样本，其余隐藏标签。",
        "excluded_input_fields": ["label_name", "agreement_level", "source_line"],
    }
    (data_dir / "split.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_data(folder):
    """已有快照只校验和重用，不重新解析源 CSV 中的未标注金标。"""
    folder = Path(folder)
    data_dir = folder / "data"
    paths = {name: data_dir / f"{name}.csv" for name in SPLIT_NAMES}
    metadata_path = data_dir / "split.json"
    existing = [path.exists() for path in [*paths.values(), metadata_path]]
    if not any(existing):
        create_split(folder)
    elif not all(existing):
        raise ValueError("固定划分不完整，请检查 data 中的三个 CSV 和 split.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if digest(folder / "financial_phrasebank.csv") != metadata["source_sha256"]:
        raise ValueError("源 CSV 已变化，不能重用原有固定划分")
    if metadata["label_mapping"] != LABEL_IDS:
        raise ValueError("固定划分中的标签映射不一致")
    splits, all_ids, all_texts = {}, set(), set()
    for name, path in paths.items():
        if digest(path) != metadata["split_sha256"][name]:
            raise ValueError(f"{name}.csv 已变化，与固定快照不一致")
        columns = ["id", "text"] + ([] if name == "unlabeled" else ["label"])
        rows = read_csv(path, columns)
        if not rows or len(rows) != metadata["counts"][name]:
            raise ValueError(f"{name}.csv 条数与固定快照不一致")
        if any(not row["id"].isdigit() or not text_key(row["text"]) for row in rows):
            raise ValueError(f"{name}.csv 存在无效编号或空正文")
        if name != "unlabeled" and any(row["label"] not in ("0", "1", "2") for row in rows):
            raise ValueError(f"{name}.csv 存在无效标签")
        ids, texts = {row["id"] for row in rows}, {text_key(row["text"]) for row in rows}
        if len(ids) != len(rows) or ids & all_ids or texts & all_texts:
            raise ValueError("划分中存在重复编号或相同正文跨集合")
        all_ids.update(ids)
        all_texts.update(texts)
        splits[name] = rows
    if len(all_ids) != metadata["n_samples"]:
        raise ValueError("划分总条数与源数据不一致")
    for label, count in metadata["labeled_class_counts"].items():
        if sum(row["label"] == str(LABEL_IDS[label]) for row in splits["labeled"]) != count:
            raise ValueError("固定标注集不符合冻结的标注预算")
    return splits
