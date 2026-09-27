"""数据工具：上游字段映射、CSV 读写、划分、标签和数据清单校验。"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .common import ROOT, digest, read_json, text_key, write_json


def _csv(path, **kwargs):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, **kwargs))


class RecordBuilder:
    """Shared record serialization; each task owns its field mapping."""
    def __init__(self):
        self.records, self.sources = [], []

    def add(self, path, index, text, gold, inputs=None, record_id=None, split="pool", raw_label=None):
        self.records.append({"id": str(record_id or f"{path.stem}:{index}"), "text": text,
                             "inputs": inputs or {}, "gold": gold,
                             "metadata": {"source": path.relative_to(ROOT).as_posix(), "row": index,
                                          "upstream_split": split, "raw_label": raw_label}})

    def finish(self):
        if len({r["id"] for r in self.records}) != len(self.records):
            raise ValueError("Duplicate record IDs in adapter")
        return self.records, {p.relative_to(ROOT).as_posix(): digest(p) for p in self.sources}


def load_records(task):
    root = ROOT / "data/benchmarks"
    builder = RecordBuilder()
    sources, add = builder.sources, builder.add
    for split in ('train', 'eval'):
        path = root / task.name / 'raw' / f'{split}_list.json'
        sources.append(path)
        for i, row in enumerate(read_json(path), 1):
            add(path, i, row[0], {task.field: row[1].split()}, split=split, raw_label=row[1])
    return builder.finish()


def validate_gold(task, gold):
    if set(gold) != ({"categories", "level"} if task.kind == "joint" else {task.field}):
        raise ValueError(f"Incorrect output fields: {gold}")
    value = gold[task.field]
    if task.kind == "single":
        if not isinstance(value, str) or value not in task.labels:
            raise ValueError(f"Unknown label: {value!r}")
    elif not isinstance(value, list) or any(v not in task.labels for v in value):
        raise ValueError(f"Unknown labels: {value!r}")
    if task.kind == "joint" and gold["level"] not in ("low", "medium", "high"):
        raise ValueError("Unknown risk level")


def _apply_review(task, records, sources, out, review_path):
    needs_review = task.name in ("finnl", "finnsp")
    template = {"task": task.name, "approved": False, "version": "", "sources": sources,
                "labels": task.labels, "label_mapping": {}, "record_gold_overrides": {}}
    review = read_json(review_path) if review_path else None
    exclusions = {}
    if review:
        if (review.get("task") != task.name or review.get("sources") != sources
                or review.get("approved") is not True or not review.get("version")):
            raise ValueError("Review must be explicitly approved, versioned and match current source hashes")
        labels = review.get("labels", task.labels)
        if not labels or len(set(labels)) != len(labels) or not all(isinstance(v, str) for v in labels):
            raise ValueError("Invalid reviewed vocabulary")
        if task.name != "finnl" and labels != task.labels:
            raise ValueError("Only FinNL vocabulary is review-configurable")
        task.labels = labels
        overrides = review.get("record_gold_overrides", {})
        exclusions = review.get("excluded_records", {})
        known_ids = {r["id"] for r in records}
        if (not isinstance(exclusions, dict) or not set(exclusions).issubset(known_ids)
                or any(not isinstance(reason, str) or not reason.strip() for reason in exclusions.values())
                or set(exclusions) & set(overrides)):
            raise ValueError("Invalid reviewed exclusions")
        if not set(overrides).issubset({r["id"] for r in records}):
            raise ValueError("Review contains unknown record IDs")
        for record in records:
            raw = record["metadata"]["raw_label"]
            if raw in review.get("label_mapping", {}):
                record["gold"] = {task.field: review["label_mapping"][raw]}
            if record["id"] in overrides:
                record["gold"] = overrides[record["id"]]
        records[:] = [r for r in records if r["id"] not in exclusions]
    anomalies = []
    for record in records:
        try:
            validate_gold(task, record["gold"])
        except ValueError as exc:
            anomalies.append({"id": record["id"], "raw_label": record["metadata"]["raw_label"],
                              "text": record["text"], "gold": record["gold"], "reason": str(exc)})
    groups = defaultdict(list)
    for record in records:
        groups[text_key(record["text"])].append(record)
    conflicts = []
    for key, group in groups.items():
        signatures = {json.dumps({k: sorted(set(v)) if isinstance(v, list) else v for k, v in r["gold"].items()},
                                 sort_keys=True, ensure_ascii=False) for r in group}
        if len(signatures) > 1:
            conflicts.append({"text_hash": key, "text": group[0]["text"],
                              "records": [{"id": r["id"], "gold": r["gold"]} for r in group]})
    report = {"task": task.name, "sources": sources, "anomalies": anomalies, "conflicts": conflicts,
              "raw_label_counts": dict(Counter(r["metadata"]["raw_label"] for r in records
                                               if r["metadata"]["raw_label"] is not None)),
              "review_required": needs_review and review is None}
    if anomalies or conflicts or (needs_review and review is None):
        write_json(out / "review_report.json", report)
        if not (out / "review_template.json").exists():
            write_json(out / "review_template.json", template)
        raise ValueError(f"Manual review required; see {out / 'review_report.json'} and review_template.json")
    return {"version": review["version"], "sha256": digest(review_path),
            "excluded": [{"id": key, "reason": reason} for key, reason in exclusions.items()]} if review else None


def label_families(task, records):
    """Balance each output vocabulary; also limit empty vs nonempty label lists."""
    families = [[(task.field, label) for label in task.labels]]
    if task.kind == "joint":
        families.append([("level", value) for value in ("low", "medium", "high")])
    if task.kind != "single" and any(not r["gold"][task.field] for r in records):
        families.append([("label_presence", "empty"), ("label_presence", "nonempty")])
    return families


def record_labels(task, record):
    gold = record["gold"]
    values = [gold[task.field]] if task.kind == "single" else sorted(set(gold[task.field]))
    labels = {(task.field, value) for value in values}
    if task.kind == "joint":
        labels.add(("level", gold["level"]))
    if task.kind != "single":
        labels.add(("label_presence", "nonempty" if values else "empty"))
    return labels


def balanced_split(task, records, test_fraction=.2, max_ratio=3):
    """Jointly undersample and stratify whole text groups, with hard label caps.

    Integer variables count text groups of each label combination in each split.
    Maximize retained rows subject to coverage, <=3:1 class ratios in BOTH splits,
    and approximately 80:20 allocation for the total and every label. No label is
    changed, no row is copied, and labels absent from the source stay absent.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    if not 0 < test_fraction < 1 or max_ratio < 1:
        raise ValueError("Invalid split fraction or class ratio")
    if not records or len({r["id"] for r in records}) != len(records):
        raise ValueError("Records must be nonempty with unique IDs")
    groups = defaultdict(list)
    for record in records:
        validate_gold(task, record["gold"])
        groups[text_key(record["text"])].append(record)
    families = label_families(task, records)
    features = [label for family in families for label in family]
    patterns = defaultdict(list)
    support = Counter()
    for key, group in sorted(groups.items()):
        labels = record_labels(task, group[0])
        if any(record_labels(task, row) != labels for row in group):
            raise ValueError(f"Conflicting labels for text group: {key}")
        signature = tuple(int(feature in labels) for feature in features)
        support.update(feature for feature in features if feature in labels)
        patterns[(signature, len(group))].append(key)
    missing = [feature for feature in features if support[feature] == 0]
    scarce = [feature for feature in features if support[feature] == 1]
    if scarce:
        raise ValueError(f"Cannot cover labels in both splits: only one text group for {scarce}")
    keys = sorted(patterns)
    count = len(keys)
    sizes = np.array([key[1] for key in keys], dtype=float)
    indicators = np.array([key[0] for key in keys], dtype=float).T * sizes
    limits = np.array([len(patterns[key]) for key in keys], dtype=float)
    rows, lower, upper = [], [], []

    def constrain(row, lo=-np.inf, hi=np.inf):
        rows.append(row)
        lower.append(lo)
        upper.append(hi)

    # Inventory: a text group goes to train, test, or is dropped, never two places.
    for index in range(count):
        row = np.zeros(2 * count)
        row[index] = row[count + index] = 1
        constrain(row, hi=limits[index])
    for split in range(2):
        offset = split * count
        for index, feature in enumerate(features):
            if support[feature]:
                row = np.zeros(2 * count)
                row[offset:offset + count] = indicators[index]
                constrain(row, lo=1)
        for family in families:
            active = [features.index(feature) for feature in family if support[feature]]
            for a in active:
                for b in active:
                    if a != b:
                        row = np.zeros(2 * count)
                        row[offset:offset + count] = indicators[a] - max_ratio * indicators[b]
                        constrain(row, hi=0)
    # Allow at most one indivisible text group of rounding per label and total.
    for values in [sizes, *indicators]:
        tolerance = max((size for size, value in zip(sizes, values) if value), default=1)
        row = np.concatenate((-test_fraction * values, (1 - test_fraction) * values))
        constrain(row, lo=-tolerance, hi=tolerance)
    weights = np.concatenate((sizes, sizes))
    result = milp(-weights, integrality=np.ones(2 * count),
                  bounds=Bounds(np.zeros(2 * count), np.tile(limits, 2)),
                  constraints=LinearConstraint(np.array(rows), lower, upper),
                  options={"time_limit": 60, "mip_rel_gap": 0.0})
    if not result.success:
        raise ValueError(f"Cannot produce optimal balanced train/test datasets: {result.message}")
    allocation = np.rint(result.x).astype(int).reshape(2, count)
    parts = {"train": [], "test": []}
    selected = set()
    stable_key = lambda key: (text_key(f"{task.seed}:{key}"), key)
    for index, pattern in enumerate(keys):
        available = sorted(patterns[pattern], key=stable_key)
        start = 0
        for split, name in enumerate(parts):
            end = start + int(allocation[split, index])
            for key in available[start:end]:
                selected.add(key)
                parts[name].extend(sorted(groups[key], key=lambda r: r["id"]))
            start = end
    for part in parts.values():
        part.sort(key=lambda r: (*stable_key(text_key(r["text"])), r["id"]))
    excluded = [{"id": record["id"], "reason": f"label-balanced undersampling (max ratio {max_ratio}:1)"}
                for record in sorted(records, key=lambda r: r["id"])
                if text_key(record["text"]) not in selected]
    policy = {"method": "label-balanced-group-allocation-v1", "max_class_ratio": max_ratio,
              "test_fraction": test_fraction, "seed_role": "stable ordering within label combinations only",
              "rounding": "at most one text group per label and total",
              "input_rows": len(records), "retained_rows": sum(map(len, parts.values())),
              "dropped_rows": len(excluded), "absent_labels": [list(label) for label in missing]}
    validate_balance(task, parts, policy)
    return parts, excluded, policy


def validate_balance(task, parts, policy):
    """Fail closed on overlap, missing labels or class imbalance after loading."""
    records = [record for part in parts.values() for record in part]
    families = label_families(task, records)
    absent = {tuple(label) for label in policy["absent_labels"]}
    seen_ids, seen_texts = set(), set()
    for name, part in parts.items():
        ids, texts = {r["id"] for r in part}, {text_key(r["text"]) for r in part}
        if not part or len(ids) != len(part) or seen_ids & ids or seen_texts & texts:
            raise ValueError(f"Empty dataset, duplicate IDs or text leakage: {name}")
        seen_ids.update(ids)
        seen_texts.update(texts)
        counts = Counter(label for record in part for label in record_labels(task, record))
        for family in families:
            values = [counts[label] for label in family if label not in absent]
            if values and (min(values) == 0 or max(values) > policy["max_class_ratio"] * min(values)):
                raise ValueError(f"Label coverage or balance violation: {name}, {family}, {values}")


def metadata_directory(directory):
    """Keep manifests/review files outside the directory containing only CSV data."""
    directory = Path(directory)
    return directory.parent / "artifacts" / f"{directory.name}_metadata"


def manifest_path(directory):
    return metadata_directory(directory) / "manifest.json"


def csv_fields(task):
    gold_fields = ["categories", "level"] if task.kind == "joint" else [task.field]
    return ["id", "text", *gold_fields, "inputs", "metadata"]


def write_records_csv(task, path, records):
    """Store labels as columns and nested values as lossless JSON cells."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields(task))
        writer.writeheader()
        for record in records:
            validate_gold(task, record["gold"])
            row = {"id": record["id"], "text": record["text"], **record["gold"],
                   "inputs": record["inputs"], "metadata": record["metadata"]}
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict))
                             else value for key, value in row.items()})
    temporary.replace(path)


def read_records_csv(task, path):
    records = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != csv_fields(task):
            raise ValueError(f"Unexpected CSV columns: {path}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row {reader.line_num}: {path}")
            value = json.loads(row[task.field]) if task.kind != "single" else row[task.field]
            gold = {task.field: value}
            if task.kind == "joint":
                gold["level"] = row["level"]
            validate_gold(task, gold)
            inputs, metadata = json.loads(row["inputs"]), json.loads(row["metadata"])
            if not isinstance(inputs, dict) or not isinstance(metadata, dict):
                raise ValueError(f"Expected input/metadata objects: {path}")
            records.append({"id": row["id"], "text": row["text"], "inputs": inputs,
                            "gold": gold, "metadata": metadata})
    return records


def prepare(task, out, review_path=None):
    out = Path(out)
    if manifest_path(out).exists() or any((out / f"{split}.csv").exists() for split in ("train", "test")):
        raise FileExistsError(f"Prepared snapshot already exists: {out}; use a new directory")
    records, sources = load_records(task)
    mapping = _apply_review(task, records, sources, metadata_directory(out), review_path)
    excluded = list((mapping or {}).get("excluded", []))
    parts, dropped, policy = balanced_split(task, records)
    excluded.extend(dropped)
    manifest = {"task": task.to_dict(), "sources": sources, "mapping": mapping,
                "grouping": "sha256 of exact text; joint label quotas; no group crosses splits", "seed": task.seed,
                "balance": policy, "protocol": "train-only selection; test report-only",
                "excluded": excluded,
                "historically_exposed": task.name == "crypto" or any((out.parent / "versions").glob("v*/rules.py")),
                "format": "csv", "splits": {}}
    for name, rows in parts.items():
        path = out / f"{name}.csv"
        write_records_csv(task, path, rows)
        manifest["splits"][name] = {"rows": len(rows), "sha256": digest(path), "ids": [r["id"] for r in rows]}
    write_json(manifest_path(out), manifest)
    write_dataset_readme(task, out, parts, manifest)
    return parts


def load_prepared(task, directory):
    directory = Path(directory)
    manifest = read_json(manifest_path(directory))
    expected = manifest["task"]
    if expected["name"] != task.name or expected["seed"] != task.seed:
        raise ValueError("Prepared task/seed mismatch")
    task.labels = expected["labels"]
    if task.to_dict() != expected:
        raise ValueError("Task configuration changed; prepare a new snapshot")
    for source, expected_hash in manifest["sources"].items():
        if digest(ROOT / source) != expected_hash:
            raise ValueError(f"Source changed: {source}")
    if manifest.get("format") != "csv" or set(manifest["splits"]) != {"train", "test"}:
        raise ValueError("Expected train/test CSV data; migrate legacy JSON snapshots first")
    result = {}
    for split, spec in manifest["splits"].items():
        path = directory / f"{split}.csv"
        if digest(path) != spec["sha256"]:
            raise ValueError(f"Prepared data changed: {split}")
        result[split] = read_records_csv(task, path)
        if len(result[split]) != spec["rows"] or [r["id"] for r in result[split]] != spec["ids"]:
            raise ValueError(f"Prepared row count/IDs changed: {split}")
    if set(result) != {"train", "test"}:
        raise ValueError("Expected train/test data")
    if manifest.get("balance"):
        validate_balance(task, result, manifest["balance"])
    return result, manifest


def write_dataset_readme(task, directory, parts, manifest):
    """Keep the dataset description and distributions tied to the actual CSVs."""
    descriptions = {
        "crypto": "12 类风险多标签分类 + 风险等级三分类。识别加密货币新闻的风险类型与 low / medium / high 等级。",
        "fincausal": "金融因果关系存在性二分类。0 表示不存在因果关系，1 表示存在因果关系，不抽取原因/结果文本片段。",
        "finnl": "17 类金融新闻主题多标签分类。一条新闻可同时属于多个主题。",
        "finnsp": "针对候选实体的负面消息二分类。1 表示存在针对 inputs.entities 中实体的负面消息，0 表示不属于该情况。",
        "phrasebank": "金融情感三分类。negative、neutral、positive 分别表示投资者视角的负面、中性、正面情感。",
    }
    policy = manifest["balance"]
    ratio = policy["max_class_ratio"]
    metadata_name = metadata_directory(directory).name
    lines = [f"# {task.name} 数据集", "", "## 标注任务", "",
             f"**任务类型：{descriptions[task.name]}**", "",
             f"输入为 `text` 和 `inputs`，输出为 `{task.field}`" + (" 与 `level`。" if task.kind == "joint" else "。"), "",
             "## 数据规模与划分", "", "| 文件 | 样本数 | 用途 |", "| --- | ---: | --- |",
             f"| [train.csv](train.csv) | {len(parts['train']):,} | LF 生成、错误分析、修改与版本选择 |",
             f"| [test.csv](test.csv) | {len(parts['test']):,} | 迭代结束后评估 |",
             f"| 合计 | {sum(map(len, parts.values())):,} | |", "",
             f"复核后的候选池共 {policy['input_rows']:,} 条；按标签下采样删除 {policy['dropped_rows']:,} 条多数类样本。",
             f"在保持标签覆盖和每侧类别数量最大/最小不超过 {ratio}:1 的条件下，保留尽可能多的原始记录；不复制样本、不修改标签。",
             f"按标签组合联合分配 train/test，目标约 {1 - policy['test_fraction']:.0%}/{policy['test_fraction']:.0%}；每个标签及总数允许一个同文本组的整数取整误差。",
             "同文本组不会跨集合；seed 只决定同一标签组合内的稳定选择次序，不随机决定类别配额。原上游划分仅留作来源信息。", "",
             "这是下采样后的实验子集；测试分布也已均衡化，分数不直接代表原始自然分布或官方评测。", "",
             "## 标签分布", "", "占比以各集合的样本数为分母；同一条样本中的同名标签只计一次。"]
    if task.kind != "single":
        lines += ["", f"多标签按出现次数统计，比例不要求相加为 100%；空标签与非空标签数量另行约束在 {ratio}:1 内。"]
    if task.kind == "joint":
        lines += ["风险类别与风险等级分别约束，不将两个字段混为同一词表。"]
    all_records = [row for part in parts.values() for row in part]
    counts = {split: Counter(label for row in rows for label in record_labels(task, row))
              for split, rows in parts.items()}
    for family in label_families(task, all_records):
        lines += ["", f"### {family[0][0]}", "",
                  "| 标签 | 训练数量 | 训练占比 | 测试数量 | 测试占比 |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        for feature in family:
            a, b = counts["train"][feature], counts["test"][feature]
            lines.append(f"| {feature[1]} | {a:,} | {a / len(parts['train']):.2%} | {b:,} | {b / len(parts['test']):.2%} |")
        ratios = []
        for split in parts:
            active = [counts[split][label] for label in family if counts[split][label]]
            ratios.append(f"{split} 最大/最小类别数量 = {max(active) / min(active):.2f}:1" if active else f"{split} 无样本")
        lines += ["", "；".join(ratios) + "。"]
    if policy["absent_labels"]:
        labels = "、".join(label for _, label in policy["absent_labels"])
        lines += ["", f"源数据没有样本的标签：{labels}。仍保留原词表，无法通过划分创造样本；固定词表 Macro-F1 中该类 F1 为 0。"]
    if task.kind != "single":
        smallest = min(counts["test"][label] for label in label_families(task, all_records)[0] if counts["test"][label])
        lines += ["", f"测试集中最少的非空标签只有 {smallest} 条。严格均衡使样本量明显减少，少数类指标可能随个别预测大幅波动。"]
    if manifest.get("historically_exposed"):
        lines += ["", "已有规则曾接触旧训练数据；重新分配后的测试集对这些历史规则仅作兼容性对照，不视为独立泛化评估。"]
    lines += ["", "## 来源与字段", "",
              *[f"- `{source}`" for source in manifest["sources"]], "",
              "CSV 使用 UTF-8 BOM。`id` 为记录标识，`text` 为原文，标签直接存列；`inputs`、`metadata` 和多标签列表用 JSON 单元格保存。",
              "词表及标签定义见 [任务配置](../artifacts/config.json) 与 [标注说明](../artifacts/annotation.md)。",
              f"源文件哈希、划分、排除 ID 和均衡策略见 [数据清单](../artifacts/{metadata_name}/manifest.json)。说明中的数量直接统计自当前 CSV。"]
    (Path(directory) / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
