"""修改主程序：仅替换分析指定的 LF，保留其余代码并生成候选脚本。"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    __package__ = "workflows.v4_widely.tasks.finnl"

from .tools.runner import validate_code


def apply_replacements(source, replacements, allowed, names, max_changes=6):
    if not isinstance(replacements, list) or not 1 <= len(replacements) <= max_changes:
        raise ValueError("Invalid number of replacements")
    requested = [r["name"] for r in replacements]
    if len(set(requested)) != len(requested) or not set(requested) <= set(allowed):
        raise ValueError("Duplicate or unauthorized LF replacement")
    tree = validate_code(source, names)
    existing = {n.name: n for n in tree.body}
    edits = []
    for item in replacements:
        validate_code(item["code"], [item["name"]])
        node = existing[item["name"]]
        edits.append((node.lineno - 1, node.end_lineno, item["code"].strip() + "\n"))
    lines = source.splitlines(keepends=True)
    for start, end, code in sorted(edits, reverse=True):
        lines[start:end] = [code]
    updated = "".join(lines)
    validate_code(updated, names)
    if updated == source:
        raise ValueError("No actual modification")
    return updated


def patch(client, source, analysis, iteration, version, candidate_attempt, version_feedback):
    feedback = None
    for attempt in (1, 2):
        try:
            result = client.request("patch", {"source": source, "analysis": analysis,
                "version_feedback": version_feedback, "validation_feedback": feedback,
                "response_schema": {"functions": [{"name": "lf_01", "code": "complete replacement"}],
                                    "changes": ["summary"]}}, stage="patch", iteration=iteration,
                candidate_attempt=candidate_attempt, generation_attempt=attempt, version=version)
            updated = apply_replacements(source, result["functions"], analysis["target_functions"],
                                         client.task.lf_names, client.task.max_changes)
            return updated, {"generation_attempt": attempt, "changes": result.get("changes", []),
                             "modified_functions": [r["name"] for r in result["functions"]]}
        except (ValueError, KeyError, TypeError, SyntaxError) as error:
            feedback = str(error)
            if attempt == 2:
                raise ValueError(f"Patch generation failed: {feedback}") from error


def main(argv=None):
    import argparse
    from .tools.common import digest, read_json, write_json
    from .tools.data import load_prepared, manifest_path
    from .tools.model import ModelCaller, UsageLedger, ModelSession
    from .tools.paths import TASK_DIR, get_task, build_paths, current_version
    from .tools.reporter import load_checkpoint
    from .tools.runner import read_script
    parser = argparse.ArgumentParser(description="根据分析生成候选脚本；版本接受由 orchestrator 决定")
    parser.add_argument("--version", help="待修改版本；省略时使用当前已接受版本")
    parser.add_argument("--data-dir", type=Path, default=TASK_DIR / "data")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--analysis", type=Path, help="默认读取该版本 reports/analysis/analysis.json")
    args = parser.parse_args(argv)
    task = get_task(seed=args.seed)
    version = args.version or current_version()
    paths = build_paths(version)
    load_prepared(task, args.data_dir)
    source, support = read_script(paths["script"])
    analysis = read_json(args.analysis or paths["analysis"])
    context = analysis.get("context", {})
    expected = {"task": task.name, "version": version, "script_sha256": digest(paths["script"]),
                "manifest_sha256": digest(manifest_path(args.data_dir)), "selection_split": "train"}
    if any(context.get(key) != value for key, value in expected.items()):
        raise ValueError("Analysis does not match this source/data; run analyzer.py again")
    iteration = context["iteration"]
    attempt = 1
    while (paths["reports"] / "patch" / f"attempt_{attempt}").exists():
        attempt += 1
    saved = load_checkpoint(TASK_DIR)
    client = ModelSession(task, ModelCaller(UsageLedger(
        TASK_DIR, task.name, TASK_DIR.name, debug=saved.get("debug", False))))
    client.frozen_support = support
    source, report = patch(client, source, analysis, iteration, f"v{int(version[1:]) + 1}", attempt, None)
    out = paths["reports"] / "patch" / f"attempt_{attempt}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "rules.py").write_text(source, encoding="utf-8")
    if support:
        (out / "support.py").write_text(support, encoding="utf-8")
    write_json(out / "patch_report.json", {**report, "source_version": version, "selection_split": "train"})
    print(out / "rules.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; recorded usage has been saved.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
