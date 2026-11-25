"""Train bert-base-cased full fine-tuning for this task using simple_vote and Snorkel hard weak labels.

Run this file directly to train two BERT models, one per aggregator.
By default each aggregator selects its best LF version using validation Macro-F1.
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
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

sys.dont_write_bytecode = True
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import numpy as np

if __package__:
    from .data import load_data, write_csv as write_rows
    from .downstream import AGGREGATORS, EXPLICIT_VERSION, input_hashes, score, select_versions, training_data
    from .task import BERT_MODEL_ID, BERT_REVISION, BERT_VOCAB_SIZE, CLASS_NAMES, CLASS_TITLES, DATASET
else:
    from data import load_data, write_csv as write_rows
    from downstream import AGGREGATORS, EXPLICIT_VERSION, input_hashes, score, select_versions, training_data
    from task import BERT_MODEL_ID, BERT_REVISION, BERT_VOCAB_SIZE, CLASS_NAMES, CLASS_TITLES, DATASET

TASK_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BERT_MODEL_ID.replace("-", "_") + "_results"
MODEL = "bert"
SCHEMA = 1
DEFAULT_MAX_LENGTH = 128


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    write_rows(path, rows, list(rows[0]))


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
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def prepare(selection, splits, metadata):
    version = TASK_DIR / "versions" / selection["version"]
    aggregator = selection["aggregator"]
    train, targets, counts, upstream = training_data(version, aggregator, splits, metadata)
    manifest = [{"id": row["id"], "weak_label": CLASS_NAMES[int(target)]}
                for row, target in zip(train, targets)]
    seen = {row["text"] for row in train}
    return {
        "selection": selection, "version": version, "splits": splits,
        "train": train, "targets": targets, "manifest": manifest, "upstream": upstream,
        "train_summary": {"total": len(splits["train"]), "used": len(train), "abstained_removed": counts["abstain"],
                          "class_counts": {name: counts[name] for name in CLASS_NAMES}},
        "overlap_audit": {split: sum(row["text"] in seen for row in splits[split]) for split in ("valid", "test")},
        "input_sha256": input_hashes(TASK_DIR, version, aggregator),
    }


def config_for(job, args):
    config = {"schema_version": SCHEMA, "task": DATASET,
              "lf_version": job["selection"]["version"], "aggregator": job["selection"]["aggregator"],
              "model": MODEL, "seed": args.seed, "class_order": list(CLASS_NAMES),
              "class_titles": list(CLASS_TITLES), "train_gold_used": False, "test_used_for_selection": False,
              "training_target": "non-abstained train hard weak labels",
              "selection": f"valid {CLASS_TITLES[1]} F1",
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
    config.update({"pretrained_model": BERT_MODEL_ID, "revision": args.revision if not args.model_path else None,
                   "model_path": str(args.model_path) if args.model_path else None,
                   "local_model_sha256": local_model_hashes,
                   "epochs": args.epochs, "learning_rate": args.learning_rate,
                   "batch_size": args.batch_size, "gradient_accumulation": args.gradient_accumulation,
                   "eval_batch_size": args.eval_batch_size, "weight_decay": args.weight_decay,
                   "warmup_ratio": args.warmup_ratio, "max_grad_norm": 1.0,
                   "max_length": args.max_length,
                   "device": args.device, "precision": args.precision,
                   "gradient_checkpointing": args.gradient_checkpointing,
                   "torch_version": torch.__version__, "transformers_version": transformers.__version__,
                   "input_format": "raw document text",
                   "truncation": "head_tail", "padding": "dynamic per batch",
                   "checkpoint_selection": "strict validation improvement; ties keep earliest epoch"})

    return config


def train_bert(job, config, output, args):
    import torch
    from torch.utils.data import DataLoader
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              DataCollatorWithPadding, get_linear_schedule_with_warmup, set_seed)
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
    source = args.model_path or BERT_MODEL_ID
    kwargs = {"local_files_only": not args.allow_download}
    if not args.model_path:
        kwargs["revision"] = args.revision
    tokenizer = AutoTokenizer.from_pretrained(source, **kwargs)
    if tokenizer.do_lower_case or len(tokenizer) != BERT_VOCAB_SIZE:
        raise ValueError(f"Expected the {BERT_MODEL_ID} tokenizer (vocabulary size {BERT_VOCAB_SIZE})")
    model = AutoModelForSequenceClassification.from_pretrained(
        source, num_labels=len(CLASS_NAMES), id2label=dict(enumerate(CLASS_NAMES)),
        label2id={n: i for i, n in enumerate(CLASS_NAMES)}, attn_implementation="sdpa", **kwargs)
    if (model.config.model_type != "bert" or model.config.hidden_size != 768
            or model.config.num_hidden_layers != 12 or model.config.num_attention_heads != 12
            or model.config.vocab_size != BERT_VOCAB_SIZE):
        raise ValueError(f"Expected the {BERT_MODEL_ID} architecture")
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.to(device)
    collator = DataCollatorWithPadding(tokenizer, pad_to_multiple_of=8)
    tokenization_audit = {}

    def encode(rows, targets, split):
        records, unknown, tokens, truncated = [], 0, 0, 0
        # Keep both ends of a document within BERT's token budget.
        budget = config["max_length"] - 2  # [CLS] and [SEP]
        head = (budget + 1) // 2
        tail = budget - head
        for start in range(0, len(rows), 128):
            batch = rows[start:start + 128]
            encoded = tokenizer([row["text"] for row in batch], add_special_tokens=False,
                                truncation=False, return_attention_mask=False,
                                return_token_type_ids=False, verbose=False)
            for i, ids in enumerate(encoded["input_ids"]):
                if len(ids) > budget:
                    ids = ids[:head] + ids[-tail:]
                    truncated += 1
                input_ids = [tokenizer.cls_token_id, *ids, tokenizer.sep_token_id]
                record = {"input_ids": input_ids}
                if targets is not None:
                    record["labels"] = int(targets[start + i])
                records.append(record)
                unknown += ids.count(tokenizer.unk_token_id)
                tokens += len(input_ids)
        tokenization_audit[split] = {"n_samples": len(rows), "unk_token_fraction": unknown / tokens,
                                     "samples_truncated": truncated}
        return records

    train_records = encode(job["train"], job["targets"], "train")
    valid_records = encode(job["splits"]["valid"], None, "valid")
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
        metrics = score(job["splits"]["valid"], probs.argmax(axis=1))
        value = metrics[CLASS_NAMES[1]]["f1"]
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
    test_records = encode(job["splits"]["test"], None, "test")
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
    selection = job["selection"]
    result = {"task": DATASET, "lf_version": selection["version"],
              "aggregator": selection["aggregator"], "model": model,
              "train": job["train_summary"], "overlap_audit": job["overlap_audit"],
              "input_sha256": job["input_sha256"], **extras}
    for split, probs in probabilities.items():
        if probs.shape != (len(job["splits"][split]), len(CLASS_NAMES)):
            raise ValueError(f"Wrong probability shape: {split}")
        if not np.isfinite(probs).all() or (probs < 0).any():
            raise ValueError(f"Invalid probabilities: {split}")
        np.testing.assert_allclose(probs.sum(axis=1), 1, rtol=1e-5, atol=1e-5)
        predicted = probs.argmax(axis=1)
        result[split] = score(job["splits"][split], predicted)
        write_csv(output / f"{split}_predictions.csv", [
            {"id": r["id"], "gold": r["label"], "prediction": CLASS_NAMES[int(predicted[i])],
             **{f"p_{n}": float(probs[i, j]) for j, n in enumerate(CLASS_NAMES)}}
            for i, r in enumerate(job["splits"][split])])
    write_csv(output / "train_manifest.csv", job["manifest"])
    write_json(output / "metrics.json", result)
    write_json(output / "run_config.json", config)
    write_json(output / "selection.json", selection)
    write_json(output / "upstream_config.json", job["upstream"]["label_model"])
    write_json(output / "tuning_history.json", history)
    lines = [f"# {DATASET} / {selection['version']} / {selection['aggregator']} / {model}", "",
             f"训练 {len(job['train']):,}/{len(job['splits']['train']):,} 条非弃权硬弱标签；seed={config['seed']}。", "",
             "默认按验证集全量 Macro-F1 选 LF 版本，同分取较早版本。下游按验证集性别歧视类 F1 选检查点，测试集用于最终评价。", "",
             "| 集合 | Accuracy | Macro-F1 | " + " | ".join(f"{n} F1" for n in CLASS_TITLES) + " |",
             "| --- | ---: | ---: | " + " | ".join("---:" for _ in CLASS_NAMES) + " |"]
    for split in ("valid", "test"):
        m = result[split]
        lines.append("| " + " | ".join([split, f"{m['accuracy']:.2%}", f"{m['macro_f1']:.2%}",
                                         *[f"{m[n]['f1']:.2%}" for n in CLASS_NAMES]]) + " |")
    lines.extend(["", f"训练输入与验证/测试完全相同的记录数：{job['overlap_audit']}。沿用冻结划分，不另行去重。", "",
                  "配置、输入哈希、训练 ID/弱标签、候选版本和逐轮结果见同目录 JSON/CSV。"])
    lines.extend(["", f"最长 {config['max_length']} 个 token，超长文本保留开头和结尾。"
                  "各集合的截断数见 metrics.json。"])
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


def publish(stage, output):
    # Stage and output share a parent volume. Preserve the previous run until the new run is ready.
    backup = stage.parent / "previous"
    if output.exists():
        output.rename(backup)
    try:
        stage.rename(output)
    except BaseException:
        if backup.exists():
            backup.rename(output)
        raise


def write_summary(updated):
    rows = []
    path = TASK_DIR / "bert_summary.json"
    if path.is_file():
        rows = [row for row in read_json(path)["results"]
                if row["aggregator"] != updated["aggregator"]
                and (TASK_DIR / row["report"]).is_file()]
    rows.append(updated)
    rows.sort(key=lambda row: AGGREGATORS.index(row["aggregator"]))
    write_csv(TASK_DIR / "bert_summary.csv", rows)
    write_json(path, {"generated_at": now(), "results": rows})
    lines = ["# BERT 下游结果", "", "| LF 版本 | 聚合器 | 训练数 | 验证 Macro-F1 | 测试 Accuracy | 测试 Macro-F1 |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| [{row['version']}]({row['report']}) | {row['aggregator']} | "
                     f"{row['train_used']:,} | {row['valid_macro_f1']:.2%} | "
                     f"{row['test_accuracy']:.2%} | {row['test_macro_f1']:.2%} |")
    (TASK_DIR / "bert_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="LF version such as v5; default: best validation Macro-F1 per aggregator")
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
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH,
                        help=f"Token budget (default: {DEFAULT_MAX_LENGTH}); preserve head and tail")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--precision", choices=("auto", "bf16", "fp16", "fp32"), default="auto")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--revision", default=BERT_REVISION)
    parser.add_argument("--model-path", type=Path, help=f"Local {BERT_MODEL_ID} pretrained model directory")
    parser.add_argument("--allow-download", action="store_true", help="Allow downloading uncached pretrained files")
    args = parser.parse_args(argv)
    if args.version is not None and not re.fullmatch(EXPLICIT_VERSION, args.version):
        parser.error("--version must be v1, v5, etc.; omit it to select the best version")
    if not 0 <= args.seed < 2**32:
        parser.error("--seed must be in [0, 2**32)")
    for name in ("epochs", "batch_size", "gradient_accumulation", "eval_batch_size", "cpu_threads"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not 8 <= args.max_length <= 512:
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


def main(argv=None):
    args = parse_args(argv)
    splits = load_data(TASK_DIR)
    metadata = read_json(TASK_DIR / "data/split.json")
    selections = select_versions(TASK_DIR, args.aggregators, metadata, args.version)
    for selection in selections:
        print(f"{selection['version']} / {selection['aggregator']}: "
              f"LF valid Macro-F1={selection['valid_macro_f1']:.6f}", flush=True)
    if args.plan:
        return

    jobs = [prepare(selection, splits, metadata) for selection in selections]
    for job in jobs:
        selection = job["selection"]
        print(f"Checked {selection['version']}/{selection['aggregator']}: {job['train_summary']}", flush=True)
    if args.check_data:
        return
    if args.allow_download:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    work = []
    for job in jobs:
        selection = job["selection"]
        config = config_for(job, args)
        output = job["version"] / selection["aggregator"] / OUTPUT_DIR
        parent = output.parent.resolve()
        if not parent.is_relative_to(TASK_DIR) or output.is_symlink() or output.resolve().parent != parent:
            raise ValueError(f"Unsafe output path: {output}")
        verified = args.resume and complete_matches(output, config)
        if not verified and output.exists() and any(output.iterdir()) and not args.overwrite:
            raise FileExistsError(f"Existing output: {output}; use --overwrite to replace it.")
        work.append((job, config, output, verified))

    for job, config, output, verified in work:
        selection = job["selection"]
        if verified:
            print(f"Skip verified completed run: {relative(output)}", flush=True)
            result = read_json(output / "metrics.json")
        else:
            print(f"\nTraining {relative(output)}", flush=True)
            with tempfile.TemporaryDirectory(prefix=f".{OUTPUT_DIR}-", dir=output.parent) as temporary:
                stage = Path(temporary) / "new"
                stage.mkdir()
                probabilities, history, extras = train_bert(job, config, stage, args)
                result = finish(job, MODEL, config, stage, probabilities, history, extras)
                publish(stage, output)
            print(f"Saved: {relative(output)}; test macro-F1={result['test']['macro_f1']:.6f}", flush=True)
        write_summary({"task": DATASET, "version": selection["version"], "aggregator": selection["aggregator"],
                       "model": MODEL, "lf_valid_macro_f1": selection["valid_macro_f1"],
                       "train_used": result["train"]["used"], "valid_macro_f1": result["valid"]["macro_f1"],
                       "test_accuracy": result["test"]["accuracy"], "test_macro_f1": result["test"]["macro_f1"],
                       "report": relative(output / "report.md")})
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
