"""Verify downloaded bytes and record structure/counts without changing data."""
import collections
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    report = {"verified_files": 0, "files": {}, "notes": {}}
    for filename in ("manifest.json", "manifest_extra.json"):
        if not (ROOT / filename).exists():
            continue
        for item in read(ROOT / filename)["files"]:
            data = (ROOT / item["path"]).read_bytes()
            assert len(data) == item["bytes"], item["path"]
            assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
            report["verified_files"] += 1
    for task, text_index in (("finnsp", 2), ("finnl", 0)):
        for path in sorted((ROOT / task / "raw").glob("*.json")):
            data = read(path)
            report["files"][path.relative_to(ROOT).as_posix()] = {
                "rows": len(data), "format": "JSON array",
                "row_type": type(data[0]).__name__,
                "row_lengths": dict(collections.Counter(len(row) for row in data)),
                "label_availability": "not provided" if path.name == "problem_list.json" else "provided",
            }
        train = read(ROOT / task / "raw/train_list.json")
        evaluation = read(ROOT / task / "raw/eval_list.json")
        train_text = {row[text_index] for row in train}
        eval_text = {row[text_index] for row in evaluation}
        report["notes"][task] = {
            "unique_train_texts": len(train_text), "unique_eval_texts": len(eval_text),
            "exact_train_eval_text_overlap": len(train_text & eval_text),
            "problem_list": "Unlabeled input records; no local gold scoring available.",
        }
        if task == "finnl":
            report["notes"][task]["train_label_tokens_split_by_whitespace"] = dict(collections.Counter(label for row in train for label in row[1].split()))
    for path in sorted((ROOT / "fewfc").rglob("*.json")):
        data = read(path)
        report["files"][path.relative_to(ROOT).as_posix()] = {"rows": len(data), "format": "JSON array", "fields": sorted(data[0])}
    for path in sorted((ROOT / "financial_phrasebank/raw").rglob("Sentences*.txt")):
        rows = path.read_text(encoding="cp1252").splitlines()
        labels = collections.Counter(row.rsplit("@", 1)[-1] for row in rows)
        assert set(labels) <= {"positive", "neutral", "negative"}
        report["files"][path.relative_to(ROOT).as_posix()] = {"rows": len(rows), "encoding": "cp1252", "labels": dict(labels)}
    for path in sorted((ROOT / "fincausal2020/data").rglob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=";"))
        report["files"][path.relative_to(ROOT).as_posix()] = {"rows_excluding_header": len(rows) - 1, "columns": rows[0], "format": "semicolon CSV"}
    for path in sorted((ROOT / "edt/Event_detection").glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        # Upstream README defines articles as blocks separated by blank lines.
        import re
        blocks = re.split(r"\n\s*\n", text)
        report["files"][path.relative_to(ROOT).as_posix()] = {"article_blocks": len(blocks), "nonempty_lines": sum(bool(line.strip()) for line in text.splitlines()), "format": "token labels, blank-line-separated articles"}
    (ROOT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified_files": report["verified_files"], "files": report["files"]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
