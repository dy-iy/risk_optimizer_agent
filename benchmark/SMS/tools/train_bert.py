"""Train bert-base-cased full fine-tuning for this task using simple_vote and Snorkel hard weak labels.

Run this file directly to train two BERT models, one per aggregator.
By default each aggregator selects its best non-rejected LF version using validation Macro-F1.
Examples:
    python tools/train_bert.py
    python tools/train_bert.py --version v5 --aggregator Snorkel
    python tools/train_bert.py --plan
    python tools/train_bert.py --check-data

Only non-abstained training weak labels are fitted. Test labels are for final evaluation.
Results: versions/vN/{simple_vote,Snorkel}/bert_base_cased_results/.
Existing results are replaced only after successful training and reload verification.
Use --no-overwrite to prevent replacement, or --no-resume to retrain completed models.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
import csv
from datetime import datetime
from functools import lru_cache
import gc
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import warnings
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import numpy as np

if __package__:
    from .decision import was_rejected
else:
    from decision import was_rejected

TASK_DIR = Path(__file__).resolve().parents[1]
TASK_NAME = TASK_DIR.name
AGGREGATORS = ("simple_vote", "Snorkel")
SPLITS = ("train", "valid", "test")
OUTPUTS = {"bert": "bert_base_cased_results"}
MODEL = "bert"
MODEL_ID = "bert-base-cased"
SCHEMA = 1


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    if not rows or any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError(f"Empty or malformed CSV: {path}")
    return rows


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def object_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(TASK_DIR).as_posix()


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def load_task():
    """???????????????????? python -m ???"""
    folder = TASK_DIR
    for filename in ("split.json", "train.csv", "valid.csv", "test.csv"):
        if not (folder / "data" / filename).is_file():
            raise FileNotFoundError(folder / "data" / filename)
    data_module = (importlib.import_module(".data", __package__) if __package__
                   else importlib.import_module("data"))
    splits = data_module.load_data(folder)
    metadata = read_json(folder / "data/split.json")
    if TASK_NAME == "Finnsp":
        names = titles = ("0", "1")
    else:
        module = (importlib.import_module(".task", __package__) if __package__
                  else importlib.import_module("task"))
        names, titles = module.CLASS_NAMES, module.CLASS_TITLES
    if any("label" in row for row in splits["train"]):
        raise ValueError(f"{TASK_NAME}: training data must not contain gold labels")
    # Data loaders may already encode gold labels as integer class IDs.
    lookup = {**{i: i for i in range(len(names))},
              **{s: i for i, s in enumerate(titles)},
              **{s: i for i, s in enumerate(names)}}
    return {"task": TASK_NAME, "folder": folder, "splits": splits, "metadata": metadata,
            "names": list(names), "titles": list(titles), "lookup": lookup}


def score(gold, predictions, names):
    """Explicit class metrics: upstream abstention is a false negative."""
    gold, predictions = np.asarray(gold), np.asarray(predictions)
    if len(gold) == 0 or gold.shape != predictions.shape:
        raise ValueError("Gold/prediction length mismatch")
    if not np.isin(gold, range(len(names))).all():
        raise ValueError("Unknown gold class")
    if not np.isin(predictions, [-1, *range(len(names))]).all():
        raise ValueError("Unknown predicted class")
    result = {"n_samples": len(gold), "accuracy": float((gold == predictions).mean())}
    for i, name in enumerate(names):
        tp = int(((gold == i) & (predictions == i)).sum())
        fp = int(((gold != i) & (predictions == i)).sum())
        fn = int(((gold == i) & (predictions != i)).sum())
        result[name] = {"precision": tp / (tp + fp) if tp + fp else 0.0,
                        "recall": tp / (tp + fn) if tp + fn else 0.0,
                        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    result["macro_f1"] = float(np.mean([result[n]["f1"] for n in names]))
    result["confusion_matrix"] = [
        [int(((gold == i) & (predictions == j)).sum()) for j in range(len(names))]
        for i in range(len(names))]
    return result


def gold_labels(task, split):
    return np.array([task["lookup"][r["label"]] for r in task["splits"][split]])


def validate_upstream(task, version, aggregator):
    path = version / aggregator / "metrics.json"
    if task["task"] == "Finnsp" and not path.exists():
        path = version / "results/metrics.json"
    metrics = read_json(path)
    if metrics["lf_version"] != version.name:
        raise ValueError(f"Wrong LF version: {path}")
    if metrics["data_split"]["csv_sha256"] != task["metadata"]["csv_sha256"]:
        raise ValueError(f"Frozen split mismatch: {path}")
    if metrics.get("aggregator", aggregator) != aggregator:
        raise ValueError(f"Wrong aggregator: {path}")
    return path, metrics


def finnsp_votes(task, version):
    """Reconstruct unweighted majority vote; ties and no votes abstain."""
    rows = read_csv(version / "results/lf_votes.csv")
    columns = [key for key in rows[0] if key not in ("split", "id")]
    upstream = read_json(version / "results/metrics.json")
    if not columns or len(columns) != upstream["n_lfs"]:
        raise ValueError(f"LF column count mismatch: {version}")
    expected = {(split, r["id"]) for split in SPLITS for r in task["splits"][split]}
    actual = [(r["split"], r["id"]) for r in rows]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError(f"LF vote ID mismatch: {version}")
    result = {}
    for split in SPLITS:
        selected = [r for r in rows if r["split"] == split]
        matrix = np.asarray([[int(r[c]) for c in columns] for r in selected])
        if not np.isin(matrix, [-1, 0, 1]).all():
            raise ValueError(f"Invalid LF votes: {version}")
        counts = np.column_stack([(matrix == i).sum(axis=1) for i in (0, 1)])
        totals = counts.sum(axis=1, keepdims=True)
        probs = np.full(counts.shape, 0.5, dtype=float)
        np.divide(counts, totals, out=probs, where=totals != 0)
        predicted = counts.argmax(axis=1)
        predicted[counts[:, 0] == counts[:, 1]] = -1
        result[split] = {r["id"]: (int(p), probability.tolist())
                         for r, p, probability in zip(selected, predicted, probs)}
    return result


def select_versions(aggregators, version_name=None):
    selections = []
    name = TASK_NAME
    task = load_task()
    versions = sorted((p for p in (task["folder"] / "versions").iterdir()
                       if p.is_dir() and re.fullmatch(r"v[1-9]\d*", p.name)),
                      key=lambda p: int(p.name[1:]))
    if version_name is not None:
        versions = [v for v in versions if v.name == version_name]
    else:
        versions = [v for v in versions if not was_rejected(v)]
    for aggregator in aggregators:
        candidates = []
        for version in versions:
            # Incomplete LF iterations are not valid selection candidates.
            legacy = name == "Finnsp"
            source = version / ("results" if legacy else aggregator) / "metrics.json"
            weak = source.parent / "weak_labels.csv"
            required = [source, weak, version / "lfs.py"]
            if legacy and aggregator == "simple_vote":
                required.append(version / "results/lf_votes.csv")
            if not all(p.is_file() for p in required):
                continue
            _, metrics = validate_upstream(task, version, "Snorkel" if legacy else aggregator)
            if legacy and aggregator == "simple_vote":
                votes = finnsp_votes(task, version)["valid"]
                predictions = [votes[r["id"]][0] for r in task["splits"]["valid"]]
                value = score(gold_labels(task, "valid"), predictions, task["names"])["macro_f1"]
            else:
                value = metrics["valid"]["all_samples"]["macro_f1"]
            if not math.isfinite(value):
                raise ValueError(f"Invalid validation score: {source}")
            candidates.append({"version": version.name, "valid_macro_f1": value})
        if not candidates:
            raise ValueError(f"No completed LF versions: {name}/{aggregator}/{version_name or 'best'}")
        best = max(candidates, key=lambda c: c["valid_macro_f1"])
        selections.append({"task": name, "aggregator": aggregator, **best,
                           "metric": "explicit --version" if version_name else "valid.all_samples.macro_f1",
                           "tie_break": "earliest_version", "test_used_for_selection": False,
                           "candidates": candidates})
    return selections


def model_input(task, row):
    if task["task"] == "Finnsp":
        inputs = row["inputs"]
        inputs = json.loads(inputs) if isinstance(inputs, str) else inputs
        return "正文：" + row["text"] + "\n候选实体：" + "；".join(inputs["entities"])
    return row["text"]


def prepare(selection):
    task = load_task()
    # Validate evaluation labels before starting an expensive training run.
    gold_labels(task, "valid")
    version = task["folder"] / "versions" / selection["version"]
    aggregator = selection["aggregator"]
    legacy = task["task"] == "Finnsp"
    source, upstream = validate_upstream(task, version, "Snorkel" if legacy else aggregator)
    sources = [source, version / "lfs.py", task["folder"] / "data/split.json",
               *[task["folder"] / "data" / f"{split}.csv" for split in SPLITS]]
    generated = legacy and aggregator == "simple_vote"
    if generated:
        votes = finnsp_votes(task, version)
        sources.append(version / "results/lf_votes.csv")
        weak_rows = []
        for split in SPLITS:
            for row in task["splits"][split]:
                label, probs = votes[split][row["id"]]
                inputs = row["inputs"]
                weak_rows.append({"split": split, "id": row["id"], "text": row["text"],
                                  "inputs": json.dumps(inputs, ensure_ascii=False),
                                  "weak_label": "abstain" if label == -1 else str(label),
                                  "p_0": probs[0], "p_1": probs[1]})
        upstream = {"lf_version": version.name, "aggregator": aggregator,
                    "data_split": upstream["data_split"],
                    "label_model": {"library": "simple_vote", "vote_weight": 1,
                                    "tie_break_policy": "abstain", "all_abstain_policy": "abstain",
                                    "gold_used_for_fit": False},
                    "source_lf_votes": relative(version / "results/lf_votes.csv")}
    else:
        weak_path = source.parent / "weak_labels.csv"
        sources.append(weak_path)
        weak_rows = read_csv(weak_path)
    by_key = {(r["split"], r["id"]): r for r in weak_rows}
    expected = {(s, r["id"]) for s in SPLITS for r in task["splits"][s]}
    if len(by_key) != len(weak_rows) or set(by_key) != expected:
        raise ValueError(f"Weak-label ID mismatch: {version}/{aggregator}")
    labels = {"abstain": -1, **{n: i for i, n in enumerate(task["names"])}}
    for split in SPLITS:
        for row in task["splits"][split]:
            weak = by_key[split, row["id"]]
            if weak["weak_label"] not in labels or weak["text"] != row["text"]:
                raise ValueError(f"Weak-label text/class mismatch: {row['id']}")
            if legacy and json.loads(weak["inputs"]) != row["inputs"]:
                raise ValueError(f"Weak-label candidate entity mismatch: {row['id']}")
    train = [r for r in task["splits"]["train"]
             if by_key["train", r["id"]]["weak_label"] != "abstain"]
    targets = np.array([labels[by_key["train", r["id"]]["weak_label"]] for r in train])
    if set(targets) != set(range(len(task["names"]))):
        raise ValueError(f"Missing weak training classes: {version}/{aggregator}")
    if not generated and len(train) != upstream["train"]["n_labeled"]:
        raise ValueError(f"Weak training count mismatch: {version}/{aggregator}")
    manifest = [{"id": r["id"], "weak_label": task["names"][int(y)]}
                for r, y in zip(train, targets)]
    seen = {model_input(task, r) for r in train}
    return {"task": task, "selection": selection, "version": version,
            "train": train, "targets": targets, "manifest": manifest,
            "train_summary": {"total": len(task["splits"]["train"]), "used": len(train),
                              "abstained_removed": len(task["splits"]["train"]) - len(train),
                              "class_counts": dict(Counter(r["weak_label"] for r in manifest))},
            "overlap_audit": {s: sum(model_input(task, r) in seen for r in task["splits"][s])
                              for s in ("valid", "test")},
            "input_sha256": {relative(p): digest(p) for p in sources},
            "upstream": upstream, "generated_weak_rows": weak_rows if generated else None}


def selection_score(metrics, names):
    return metrics[names[1]]["f1"] if len(names) == 2 else metrics["macro_f1"]


def config_for(job, args):
    task = job["task"]
    config = {"schema_version": SCHEMA, "task": task["task"],
              "lf_version": job["selection"]["version"], "aggregator": job["selection"]["aggregator"],
              "model": MODEL, "seed": args.seed, "class_order": task["names"],
              "class_titles": task["titles"], "train_gold_used": False, "test_used_for_selection": False,
              "training_target": "non-abstained train hard weak labels",
              "selection": "valid class-1 F1" if len(task["names"]) == 2 else "valid Macro-F1",
              "lf_selection_sha256": object_digest(job["selection"]),
              "input_sha256": job["input_sha256"], "train_manifest_sha256": object_digest(job["manifest"]),
              "training_script_sha256": digest(Path(__file__)),
              "numpy_version": np.__version__}
    import torch
    import transformers
    local_model_hashes = None
    if args.model_path:
        local_model_hashes = {p.relative_to(args.model_path).as_posix(): digest(p)
                              for p in sorted(args.model_path.rglob("*")) if p.is_file()}
    config.update({"pretrained_model": MODEL_ID, "revision": args.revision if not args.model_path else None,
                   "model_path": str(args.model_path) if args.model_path else None,
                   "local_model_sha256": local_model_hashes,
                   "epochs": args.epochs, "learning_rate": args.learning_rate,
                   "batch_size": args.batch_size, "gradient_accumulation": args.gradient_accumulation,
                   "eval_batch_size": args.eval_batch_size, "weight_decay": args.weight_decay,
                   "warmup_ratio": args.warmup_ratio, "max_grad_norm": 1.0,
                   "max_length": args.max_length or (256 if task["task"] in ("IMDb", "Yelp", "Finnsp") else 128),
                   "device": args.device, "precision": args.precision,
                   "gradient_checkpointing": args.gradient_checkpointing,
                   "torch_version": torch.__version__, "transformers_version": transformers.__version__,
                   "input_format": "candidate_entities / text pair" if task["task"] == "Finnsp" else "text",
                   "truncation": "longest_first", "padding": "dynamic per batch",
                   "checkpoint_selection": "strict validation improvement; ties keep earliest epoch"})

    return config





def train_bert(job, config, output, args):
    import torch
    from torch.utils.data import DataLoader
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              DataCollatorWithPadding, get_linear_schedule_with_warmup, set_seed)
    task = job["task"]
    torch.set_num_threads(args.cpu_threads)
    set_seed(config["seed"])
    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available()
                          else "cpu" if args.device == "auto" else args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; select --device cpu explicitly if desired")
    precision = args.precision
    if precision == "auto":
        precision = ("bf16" if torch.cuda.is_bf16_supported() else "fp16") if device.type == "cuda" else "fp32"
    if precision != "fp32" and device.type != "cuda":
        raise ValueError("bf16/fp16 require CUDA; use --precision fp32 on CPU")
    if precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise ValueError("This GPU does not support bf16; use --precision fp16")
    dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[precision]
    source = args.model_path or MODEL_ID
    kwargs = {"local_files_only": not args.allow_download}
    if not args.model_path:
        kwargs["revision"] = args.revision
    tokenizer = AutoTokenizer.from_pretrained(source, **kwargs)
    if tokenizer.do_lower_case or len(tokenizer) != 28996:
        raise ValueError("Expected the bert-base-cased tokenizer (cased, vocabulary size 28996)")
    model = AutoModelForSequenceClassification.from_pretrained(
        source, num_labels=len(task["names"]), id2label=dict(enumerate(task["names"])),
        label2id={n: i for i, n in enumerate(task["names"])}, attn_implementation="sdpa", **kwargs)
    if (model.config.model_type != "bert" or model.config.hidden_size != 768
            or model.config.num_hidden_layers != 12 or model.config.num_attention_heads != 12
            or model.config.vocab_size != 28996):
        raise ValueError("Expected the bert-base-cased architecture")
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.to(device)
    collator = DataCollatorWithPadding(tokenizer, pad_to_multiple_of=8)
    tokenization_audit = {}

    def encode(rows, targets, split):
        records, unknown, tokens, at_limit = [], 0, 0, 0
        for start in range(0, len(rows), 512):
            batch = rows[start:start + 512]
            texts = [r["text"] for r in batch]
            if task["task"] == "Finnsp":
                entities = ["候选实体：" + "；".join(r["inputs"]["entities"]) for r in batch]
                encoded = tokenizer(entities, texts, truncation=True, max_length=config["max_length"])
            else:
                encoded = tokenizer(texts, truncation=True, max_length=config["max_length"])
            for i, ids in enumerate(encoded["input_ids"]):
                record = {k: v[i] for k, v in encoded.items()}
                if targets is not None:
                    record["labels"] = int(targets[start + i])
                records.append(record)
                unknown += ids.count(tokenizer.unk_token_id)
                tokens += len(ids)
                at_limit += len(ids) == config["max_length"]
        tokenization_audit[split] = {"n_samples": len(rows), "unk_token_fraction": unknown / tokens,
                                     "samples_at_length_limit": at_limit}
        return records

    train_records = encode(job["train"], job["targets"], "train")
    valid_records = encode(task["splits"]["valid"], None, "valid")
    generator = torch.Generator().manual_seed(config["seed"])
    train_loader = DataLoader(train_records, batch_size=args.batch_size, shuffle=True,
                              generator=generator, collate_fn=collator, num_workers=0,
                              pin_memory=device.type == "cuda")

    def autocast():
        return torch.autocast("cuda", dtype=dtype) if precision != "fp32" else nullcontext()

    def predict(records):
        loader = DataLoader(records, batch_size=args.eval_batch_size, collate_fn=collator,
                            num_workers=0, pin_memory=device.type == "cuda")
        model.eval()
        batches = []
        with torch.inference_mode():
            for batch in loader:
                with autocast():
                    logits = model(**{k: v.to(device) for k, v in batch.items()}).logits
                batches.append(torch.softmax(logits.float(), dim=-1).cpu().numpy())
        return np.concatenate(batches)

    decay, no_decay = [], []
    for name, parameter in model.named_parameters():
        (no_decay if name.endswith("bias") or "LayerNorm.weight" in name else decay).append(parameter)
    optimizer = torch.optim.AdamW([{"params": decay, "weight_decay": args.weight_decay},
                                  {"params": no_decay, "weight_decay": 0.0}],
                                 lr=args.learning_rate, fused=device.type == "cuda")
    steps_per_epoch = math.ceil(len(train_loader) / args.gradient_accumulation)
    total_steps = args.epochs * steps_per_epoch
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=int(total_steps * args.warmup_ratio), num_training_steps=total_steps)
    scaler = torch.amp.GradScaler("cuda", enabled=precision == "fp16")
    best_score, best_epoch, history = -1.0, None, []
    best_path = output / "best_weights.pt"
    global_step = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        total_loss, total_seen = 0.0, 0
        started = last_log = time.monotonic()
        for i, batch in enumerate(train_loader):
            # Weight each microbatch by its sample count, including the final partial group.
            group_start = (i // args.gradient_accumulation) * args.gradient_accumulation
            group_end = min(group_start + args.gradient_accumulation, len(train_loader))
            group_samples = min(group_end * args.batch_size, len(train_records)) - group_start * args.batch_size
            batch = {k: v.to(device) for k, v in batch.items()}
            batch_size = batch["labels"].shape[0]
            with autocast():
                loss = model(**batch).loss
            if not torch.isfinite(loss):
                raise RuntimeError(f"Non-finite training loss in epoch {epoch}, batch {i}")
            scaler.scale(loss * batch_size / group_samples).backward()
            total_loss += float(loss.detach()) * batch_size
            total_seen += batch_size
            if i + 1 == group_end:
                scaler.unscale_(optimizer)
                # fp16 overflow is handled by GradScaler, which skips that update.
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0,
                                               error_if_nonfinite=precision != "fp16")
                previous_scale = scaler.get_scale()
                scaler.step(optimizer)
                scaler.update()
                if scaler.get_scale() >= previous_scale:
                    scheduler.step()
                    global_step += 1
                optimizer.zero_grad(set_to_none=True)
            if time.monotonic() - last_log >= 30:
                print(f"  epoch {epoch}/{args.epochs}: {i + 1}/{len(train_loader)} batches, "
                      f"loss={total_loss / total_seen:.4f}", flush=True)
                last_log = time.monotonic()
        probs = predict(valid_records)
        metrics = score(gold_labels(task, "valid"), probs.argmax(axis=1), task["names"])
        value = selection_score(metrics, task["names"])
        history.append({"epoch": epoch, "train_loss": total_loss / total_seen,
                        "selection_score": value, "valid": metrics,
                        "optimizer_steps": global_step, "seconds": time.monotonic() - started})
        print(f"  epoch {epoch}: valid selection score={value:.6f}, "
              f"macro-F1={metrics['macro_f1']:.6f}", flush=True)
        if value > best_score:
            best_score, best_epoch = value, epoch
            torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, best_path)
    # Release optimizer state before loading/exporting the selected model.
    del optimizer, scheduler, train_loader, train_records
    gc.collect()
    model.load_state_dict(torch.load(best_path, map_location="cpu", weights_only=True))
    probabilities = {"valid": predict(valid_records)}
    # Test tokenization / inference happens only after selecting the validation checkpoint.
    test_records = encode(task["splits"]["test"], None, "test")
    probabilities["test"] = predict(test_records)
    model_dir = output / "model"
    model.save_pretrained(model_dir)
    tokenizer.save_pretrained(model_dir)
    resolved_revision = getattr(model.config, "_commit_hash", None)
    del model
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    model = AutoModelForSequenceClassification.from_pretrained(
        model_dir, local_files_only=True, attn_implementation="sdpa").to(device)
    for split, records in (("valid", valid_records), ("test", test_records)):
        restored = predict(records)
        np.testing.assert_allclose(restored, probabilities[split], rtol=1e-4, atol=1e-5)
        if not np.array_equal(restored.argmax(axis=1), probabilities[split].argmax(axis=1)):
            raise RuntimeError("Reloaded BERT predictions differ")
    best_path.unlink()
    extras = {"best_epoch": best_epoch, "resolved_revision": resolved_revision,
              "device": str(device), "precision": precision,
              "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
              "tokenization_audit": tokenization_audit}
    del model
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return probabilities, history, extras


def finish(job, model, config, output, probabilities, history, extras):
    task, selection = job["task"], job["selection"]
    result = {"task": task["task"], "lf_version": selection["version"],
              "aggregator": selection["aggregator"], "model": model,
              "train": job["train_summary"], "overlap_audit": job["overlap_audit"],
              "input_sha256": job["input_sha256"], **extras}
    for split, probs in probabilities.items():
        if probs.shape != (len(task["splits"][split]), len(task["names"])):
            raise ValueError(f"Wrong probability shape: {split}")
        if not np.isfinite(probs).all() or (probs < 0).any():
            raise ValueError(f"Invalid probabilities: {split}")
        np.testing.assert_allclose(probs.sum(axis=1), 1, rtol=1e-5, atol=1e-5)
        predicted = probs.argmax(axis=1)
        result[split] = score(gold_labels(task, split), predicted, task["names"])
        write_csv(output / f"{split}_predictions.csv", [
            {"id": r["id"], "gold": r["label"], "prediction": task["names"][int(predicted[i])],
             **{f"p_{n}": float(probs[i, j]) for j, n in enumerate(task["names"])}}
            for i, r in enumerate(task["splits"][split])])
    write_csv(output / "train_manifest.csv", job["manifest"])
    write_json(output / "metrics.json", result)
    write_json(output / "run_config.json", config)
    write_json(output / "selection.json", selection)
    write_json(output / "upstream_config.json", job["upstream"]["label_model"])
    write_json(output / "tuning_history.json", history)
    lines = [f"# {task['task']} / {selection['version']} / {selection['aggregator']} / {model}", "",
             f"训练 {len(job['train']):,}/{len(task['splits']['train']):,} 条非弃权硬弱标签；seed={config['seed']}。", "",
             "LF 版本按验证集全量 Macro-F1 选择，同分取较早版本。下游按验证集正类 F1（二分类）或 Macro-F1（多分类）选参；测试集仅作最终评价。", "",
             "| 集合 | Accuracy | Macro-F1 | " + " | ".join(f"{n} F1" for n in task["titles"]) + " |",
             "| --- | ---: | ---: | " + " | ".join("---:" for _ in task["names"]) + " |"]
    for split in ("valid", "test"):
        m = result[split]
        lines.append("| " + " | ".join([split, f"{m['accuracy']:.2%}", f"{m['macro_f1']:.2%}",
                                         *[f"{m[n]['f1']:.2%}" for n in task["names"]]]) + " |")
    lines.extend(["", f"训练输入与验证/测试完全相同的记录数：{job['overlap_audit']}。沿用冻结划分，不另行去重。", "",
                  "配置、输入哈希、训练 ID/弱标签、候选版本和逐轮结果见同目录 JSON/CSV。"])
    if model == "bert" and task["task"] == "Finnsp":
        lines.extend(["", "按指定使用 bert-base-cased 的英文词表处理中文；UNK 占比见 metrics.json 的 tokenization_audit。"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Written last: this marker denotes a complete, reload-verified run.
    write_json(output / "completed.json", {
        "completed_at": now(), "config_sha256": object_digest(config),
        "artifact_sha256": {p.relative_to(output).as_posix(): digest(p)
                            for p in sorted(output.rglob("*")) if p.is_file() and p.name != "completed.json"}})
    return result


def complete_matches(output, config):
    marker = output / "completed.json"
    if not marker.is_file():
        return False
    try:
        completed = read_json(marker)
        artifacts = completed["artifact_sha256"]
        if completed.get("config_sha256") != object_digest(config) or not artifacts:
            return False
        return all((output / name).is_file() and digest(output / name) == value
                   for name, value in artifacts.items())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def materialize_finnsp_vote(job):
    if job["generated_weak_rows"] is None:
        return
    output = job["version"] / "simple_vote"
    output.mkdir(exist_ok=True)
    write_csv(output / "weak_labels.csv", job["generated_weak_rows"])
    # Keep generated upstream scores separate from the original Snorkel results.
    metrics = {**job["upstream"], "train": {"n_samples": job["train_summary"]["total"],
                                           "n_labeled": job["train_summary"]["used"]}}
    task = job["task"]
    by_key = {(r["split"], r["id"]): r for r in job["generated_weak_rows"]}
    for split in ("valid", "test"):
        labels = [by_key[split, r["id"]]["weak_label"] for r in task["splits"][split]]
        predicted = [-1 if label == "abstain" else int(label) for label in labels]
        metrics[split] = {"all_samples": score(gold_labels(task, split), predicted, task["names"]),
                          "coverage": float(np.mean(np.array(predicted) != -1))}
    write_json(output / "metrics.json", metrics)


def write_summary(selections):
    rows = []
    for selection in selections:
        task = load_task()
        for model, dirname in OUTPUTS.items():
            output = task["folder"] / "versions" / selection["version"] / selection["aggregator"] / dirname
            if not (output / "completed.json").is_file():
                continue
            metrics = read_json(output / "metrics.json")
            rows.append({"task": selection["task"], "version": selection["version"],
                         "aggregator": selection["aggregator"], "model": model,
                         "lf_valid_macro_f1": selection["valid_macro_f1"],
                         "train_used": metrics["train"]["used"],
                         "valid_macro_f1": metrics["valid"]["macro_f1"],
                         "test_accuracy": metrics["test"]["accuracy"],
                         "test_macro_f1": metrics["test"]["macro_f1"],
                         "report": relative(output / "report.md")})
    if not rows:
        return
    folder = TASK_DIR
    # A subset invocation keeps prior rows for the other aggregator in this task.
    previous_path = folder / "bert_summary.json"
    if previous_path.is_file():
        updated = {s["aggregator"] for s in selections}
        previous = read_json(previous_path)["results"]
        rows.extend(r for r in previous if r["aggregator"] not in updated
                    and (TASK_DIR / r["report"]).is_file())
    rows.sort(key=lambda r: (AGGREGATORS.index(r["aggregator"]),
                            list(OUTPUTS).index(r["model"])))
    write_csv(folder / "bert_summary.csv", rows)
    write_json(folder / "bert_summary.json", {"generated_at": now(), "results": rows})
    lines = ["# 最佳 LF 版本的 LR / BERT 下游结果", "", f"更新时间：{now()}。仅列出已完成并保存的模型。", "",
             "| 任务 | LF 版本 | 聚合器 | 模型 | 训练数 | 验证 Macro-F1 | 测试 Accuracy | 测试 Macro-F1 |",
             "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for r in rows:
        link = os.path.relpath(TASK_DIR / r["report"], folder).replace("\\", "/")
        lines.append(f"| {r['task']} | [{r['version']}]({link}) | {r['aggregator']} | {r['model']} | "
                     f"{r['train_used']:,} | {r['valid_macro_f1']:.2%} | "
                     f"{r['test_accuracy']:.2%} | {r['test_macro_f1']:.2%} |")
    (folder / "bert_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="LF version such as v5; default: best non-rejected version by validation Macro-F1 per aggregator")
    parser.add_argument("--aggregator", "--aggregators", dest="aggregators", nargs="+",
                        choices=AGGREGATORS, default=list(AGGREGATORS))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Print selected versions without training or writes")
    mode.add_argument("--check-data", action="store_true", help="Validate data without training or writes")
    parser.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=True,
                        help="Replace existing outputs after successful training; --no-overwrite prevents replacement")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                        help="Skip verified completed runs; --no-resume forces retraining")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--eval-batch-size", type=int, default=16)
    parser.add_argument("--max-length", type=int, help="Default: 256 for IMDb/Yelp/Finnsp, 128 otherwise")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--precision", choices=("auto", "bf16", "fp16", "fp32"), default="auto")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--revision", default="cd5ef92a9fb2f889e972770a36d4ed042daf221e")
    parser.add_argument("--model-path", type=Path, help="Local bert-base-cased pretrained model directory")
    parser.add_argument("--allow-download", action="store_true", help="Allow downloading uncached pretrained files")
    args = parser.parse_args(argv)
    if args.version is not None and not re.fullmatch(r"v[1-9]\d*", args.version):
        parser.error("--version must be v1, v5, etc.; omit it to select the best version")
    if not 0 <= args.seed < 2**32:
        parser.error("--seed must be in [0, 2**32)")
    for name in ("epochs", "batch_size", "gradient_accumulation", "eval_batch_size", "cpu_threads"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.max_length is not None and not 8 <= args.max_length <= 512:
        parser.error("--max-length must be in [8, 512]")
    if not 0 <= args.warmup_ratio < 1:
        parser.error("--warmup-ratio must be in [0, 1)")
    if not math.isfinite(args.weight_decay) or args.weight_decay < 0:
        parser.error("--weight-decay must be finite and nonnegative")
    if not math.isfinite(args.learning_rate) or args.learning_rate <= 0:
        parser.error("--learning-rate must be finite and positive")
    if args.model_path is not None:
        args.model_path = args.model_path.resolve()
        if not args.model_path.is_dir():
            parser.error("--model-path must be an existing directory")
    args.aggregators = list(dict.fromkeys(args.aggregators))
    return args


def main(version_dir=None, argv=None):
    # Preserve FinNSP's existing versions/vN/train_logistic.py launcher API.
    if version_dir is not None:
        argv = ["--version", Path(version_dir).name, *(sys.argv[1:] if argv is None else argv)]
    args = parse_args(argv)
    if args.allow_download:
        os.environ.pop("HF_HUB_OFFLINE", None)
    selections = select_versions(args.aggregators, args.version)
    for selection in selections:
        print(f"{selection['task']:10s} {selection['aggregator']:12s} {selection['version']:4s} "
              f"LF valid macro-F1={selection['valid_macro_f1']:.6f}", flush=True)
    if args.plan:
        print(f"Plan: {len(selections)} BERT model(s); no training or writes.")
        return
    jobs = [prepare(selection) for selection in selections]
    for job in jobs:
        s = job["selection"]
        print(f"Checked {s['task']}/{s['version']}/{s['aggregator']}: {job['train_summary']}", flush=True)
    if args.check_data:
        print("All selected inputs passed; no training or files written.")
        return
    work = []
    for job in jobs:
        config = config_for(job, args)
        output = job["version"] / job["selection"]["aggregator"] / OUTPUTS[MODEL]
        if args.resume and complete_matches(output, config):
            print(f"Skip verified completed run: {relative(output)}", flush=True)
            continue
        if output.exists() and any(output.iterdir()) and not args.overwrite:
            raise FileExistsError(f"Existing output: {output}; use --overwrite to replace it.")
        work.append((job, config, output))
    for job, config, output in work:
        print(f"\nTraining {relative(output)}", flush=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{OUTPUTS[MODEL]}-", dir=output.parent) as temp:
            stage = Path(temp)
            probabilities, history, extras = train_bert(job, config, stage, args)
            result = finish(job, MODEL, config, stage, probabilities, history, extras)
            materialize_finnsp_vote(job)
            expected_parent = (job["version"] / job["selection"]["aggregator"]).resolve()
            if (not output.resolve().is_relative_to(TASK_DIR) or output.resolve().parent != expected_parent
                    or output.name != OUTPUTS[MODEL]):
                raise ValueError(f"Unsafe output path: {output}")
            if output.exists():
                shutil.rmtree(output)
            output.mkdir()
            for artifact in stage.iterdir():
                shutil.move(str(artifact), output / artifact.name)
        write_summary(selections)
        print(f"Saved: {relative(output)}; test macro-F1={result['test']['macro_f1']:.6f}", flush=True)
        gc.collect()
    write_summary(selections)
    print(f"Finished. Summary: {TASK_DIR / 'bert_summary.md'}", flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted; managed temporary files were cleaned. Completed runs remain available for --resume.", file=sys.stderr)
        sys.exit(130)
