"""分析主程序：仅依据训练错例提出有证据的 LF 修改建议，不生成代码。"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    __package__ = "workflows.v4_widely.tasks.phrasebank"

def analyze(client, source, errors, iteration, version):
    feedback = None
    for attempt in (1, 2):
        try:
            result = client.request("analyze", {"source": source, "training_errors": errors,
                "max_changes": client.task.max_changes, "validation_feedback": feedback,
                "response_schema": {"summary": "brief diagnosis", "target_functions": ["lf_01"],
                    "changes": [{"function": "lf_01", "evidence_ids": ["ID from training_errors"],
                                 "description": "specific local change"}]}},
                stage="analyze", iteration=iteration, generation_attempt=attempt, version=version)
            targets = result["target_functions"]
            if (not isinstance(targets, list) or not 1 <= len(targets) <= client.task.max_changes
                    or len(set(targets)) != len(targets) or not set(targets) <= set(client.task.lf_names)):
                raise ValueError("Analyzer selected invalid LF targets")
            changes = result["changes"]
            ids = {r["id"] for r in errors}
            if {c["function"] for c in changes} != set(targets):
                raise ValueError("Every target needs an evidence-backed change")
            for change in changes:
                if not change["evidence_ids"] or not set(change["evidence_ids"]) <= ids or not change["description"]:
                    raise ValueError("Missing or invalid training evidence")
            return result
        except (ValueError, KeyError, TypeError) as error:
            feedback = str(error)
            if attempt == 2:
                raise ValueError(f"Analyzer validation failed: {feedback}") from error


def main(argv=None):
    import argparse
    from .tools.common import digest, write_json
    from .tools.data import load_prepared, manifest_path
    from .tools.model import ModelCaller, UsageLedger, ModelSession
    from .tools.paths import TASK_DIR, get_task, build_paths, current_version
    from .tools.reporter import load_checkpoint, next_iteration
    from .tools.runner import read_script, run_records
    from .tools.slicer import sample_errors
    parser = argparse.ArgumentParser(description="分析一个已保存版本的训练集错误")
    parser.add_argument("--version", help="省略时使用当前已接受版本")
    parser.add_argument("--data-dir", type=Path, default=TASK_DIR / "data")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)
    task = get_task(seed=args.seed)
    version = args.version or current_version()
    paths = build_paths(version)
    parts, _ = load_prepared(task, args.data_dir)
    source, support = read_script(paths["script"])
    predictions = run_records(task, source, parts["train"], parts["train"], support, not args.no_progress)
    errors = sample_errors(task, parts["train"], predictions)
    if not errors:
        raise ValueError("No training errors to analyze")
    saved = load_checkpoint(TASK_DIR)
    iteration = next_iteration(TASK_DIR, saved)
    client = ModelSession(task, ModelCaller(UsageLedger(
        TASK_DIR, task.name, TASK_DIR.name, debug=saved.get("debug", False))))
    client.frozen_support = support
    result = analyze(client, source, errors, iteration, version)
    result["context"] = {"task": task.name, "version": version, "iteration": iteration,
                         "script_sha256": digest(paths["script"]),
                         "manifest_sha256": digest(manifest_path(args.data_dir)), "selection_split": "train"}
    write_json(paths["analysis"], result)
    print(paths["analysis"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; recorded usage has been saved.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
