"""运行总控：初始化 → 训练评估 → 分析 → 修改 → 比较/重试 → 最终测试集报告。"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    __package__ = "workflows.v4_widely.tasks.crypto"

import time
import shutil
from typing import TypedDict

from tqdm import tqdm

from .init import initialize, examples
from .analyzer import analyze
from .patcher import patch
from .tools.evaluator import evaluate, quality_report
from .tools.slicer import sample_errors
from .tools.comparator import compare
from .tools.common import ROOT, digest, read_json, write_json
from .tools.data import load_prepared, prepare, manifest_path
from .tools.paths import TASK_NAME, TASK_NAMES, get_task, task_directory
from .tools.runner import run_records, import_legacy, legacy_support, validate_code
from .tools.model import ModelCaller, UsageLedger, ModelSession, task_usage
from .tools.reporter import METRICS, write_results, load_checkpoint, next_iteration


class RunState(TypedDict, total=False):
    iteration: int
    completed_iterations: int
    target_iterations: int
    accepted_versions: int
    version_number: int
    current_version: str
    candidate_version: str
    candidate_attempt: int
    feedback: dict | None
    analysis: dict
    current_metrics: dict
    candidate_metrics: dict | None
    stop_reason: str


class Workflow:
    def __init__(self, task, run_dir, prepared_dir, iterations=1, source_script=None, review_path=None,
                 model=None, model_name=None, debug=False, resume=False, show_progress=True, initialize_only=False):
        if iterations < 1:
            raise ValueError("Iterations must be positive")
        self.task, self.run_dir = task, Path(run_dir)
        self.show_progress = show_progress
        self.progress = None
        self.initialize_only = initialize_only
        if initialize_only and resume:
            raise ValueError("Initialization requires a task without existing versions")
        self.resuming = resume
        saved = load_checkpoint(self.run_dir) if resume else None
        if resume and source_script:
            raise ValueError("Resume uses the accepted version; do not supply source_script")
        if saved and saved.get("task", task.to_dict()) != task.to_dict():
            raise ValueError("Task configuration differs from the saved run")
        if saved and not (self.run_dir / "versions" / saved["state"]["current_version"] / "rules.py").is_file():
            raise ValueError("No complete accepted version to resume; use --new to initialize")
        if saved and saved.get("prepared_dir"):
            prepared_dir = saved["prepared_dir"]
        if saved:
            debug = saved["debug"]
        if not resume and any((self.run_dir / filename).exists() for filename in
                              ("checkpoint.json", "tokens.csv", "usage_calls.jsonl", "versions/v1/rules.py")):
            raise FileExistsError("Existing workflow history; resume instead of overwriting")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.prepared_dir, self.review_path = Path(prepared_dir), review_path
        snapshot_manifest = manifest_path(self.prepared_dir)
        self.prepared_digest = saved.get("prepared_digest") if saved else None
        if self.prepared_digest and (not snapshot_manifest.exists() or digest(snapshot_manifest) != self.prepared_digest):
            raise ValueError("Prepared data manifest differs from the saved run")
        self.source_script = Path(source_script).resolve() if source_script else None
        self.debug = debug
        self.evaluations = saved.get("evaluations", []) if saved else []
        self.comparisons = saved.get("comparisons", []) if saved else []
        self.previous_elapsed = saved.get("elapsed_seconds", 0) if saved else 0
        self.ledger = UsageLedger(self.run_dir, task.name, self.run_dir.name, debug=debug)
        if not resume:
            (self.run_dir / "changes.md").write_text("# 分析与修改记录\n", encoding="utf-8")
        self.client = ModelSession(task, ModelCaller(self.ledger, model, model_name))
        self.client.show_progress = show_progress
        self.state: RunState = {"iteration": 1, "completed_iterations": 0, "target_iterations": 0 if initialize_only else iterations,
                               "accepted_versions": 0, "version_number": 1, "current_version": "v1",
                               "candidate_attempt": 1, "feedback": None, "stop_reason": "running"}
        if saved:
            previous = saved["state"]
            self.state.update({k: previous[k] for k in (
                "completed_iterations", "accepted_versions", "version_number", "current_version")})
            self.state.update(iteration=next_iteration(self.run_dir, saved),
                              target_iterations=previous["completed_iterations"] + iterations)
            self._note("继续迭代", f"从 {self.state['current_version']} 继续，本次最多追加 {iterations} 轮。")
        self.started = time.monotonic()
        self.source = ""
        self.candidate_source = None
        self.parts = {}
        self.manifest = {}
        self.baseline_source = ""
        self.train_predictions = []
        self.test_comparison = None
        self.script_evaluations = []
        self.support = None

    def _save_state(self, state):
        self.state = dict(state)
        self._detail(self.run_dir / "state.json", self.state)
        write_json(self.run_dir / "checkpoint.json", {
            "task": self.task.to_dict(), "prepared_dir": str(self.prepared_dir.resolve()),
            "prepared_digest": self.prepared_digest,
            "debug": self.debug, "state": self.state,
            "evaluations": self.evaluations, "comparisons": self.comparisons,
            "elapsed_seconds": self.previous_elapsed + time.monotonic() - self.started})

    def _detail(self, path, value):
        if self.debug:
            write_json(path, value)

    def _note(self, title, text):
        with (self.run_dir / "changes.md").open("a", encoding="utf-8") as stream:
            stream.write(f"\n## {title}\n\n{text}\n")


    def prepare_data(self, state):
        if not manifest_path(self.prepared_dir).exists():
            prepare(self.task, self.prepared_dir, self.review_path)
        self.parts, self.manifest = load_prepared(self.task, self.prepared_dir)
        self.prepared_digest = digest(manifest_path(self.prepared_dir))
        if self.review_path and (self.manifest.get("mapping") or {}).get("sha256") != digest(self.review_path):
            raise ValueError("Review differs from prepared snapshot; choose a new --data-dir")
        self._detail(self.run_dir / "data_manifest.json", self.manifest)
        self._detail(self.run_dir / "task.json", self.task.to_dict())
        self._detail(self.run_dir / "examples.json", examples(self.task, self.parts["train"]))
        if self.debug and self.task.name in TASK_NAMES:
            directory = task_directory(self.task.name)
            snapshot = self.run_dir / "versions/task_snapshot"
            snapshot_hashes = {}
            for relative in ("artifacts/config.json", "tools/data.py", "tools/slicer.py",
                             *("artifacts/" + key for key in self.client.assets)):
                source = directory / relative
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Prompts/docs are frozen at ModelSession creation and used verbatim.
                if relative.removeprefix("artifacts/") in self.client.assets:
                    target.write_text(self.client.assets[relative.removeprefix("artifacts/")], encoding="utf-8")
                else:
                    shutil.copyfile(source, target)
                snapshot_hashes[relative] = digest(target)
            write_json(snapshot / "manifest.json", snapshot_hashes)
        if self.source_script:
            self._detail(self.run_dir / "source.json", {"path": str(self.source_script), "sha256": digest(self.source_script),
                                                      "note": "Loaded as local v1; historical usage excluded"})
        return {}

    def initialize_version(self, state):
        if self.resuming:
            directory = self.run_dir / "versions" / state["current_version"]
            self.source = (directory / "rules.py").read_text(encoding="utf-8")
            self.baseline_source = (self.run_dir / "versions/v1/rules.py").read_text(encoding="utf-8")
            if (directory / "support.py").exists():
                self.support = (directory / "support.py").read_text(encoding="utf-8")
            elif (directory / "support.json").exists():
                self.support = read_json(directory / "support.json")["source"]
            validate_code(self.source, self.task.lf_names)
            self.client.frozen_support = self.support
            return {}
        if self.source_script:
            source = self.source_script.read_text(encoding="utf-8")
            # Native code is validated; only crypto accepts the explicit legacy adapter.
            if self.task.name == "crypto" and "def label_csv(" in source:
                self.support = legacy_support(source, self.task)
                source = import_legacy(source, self.task)
            elif (self.source_script.parent / "support.json").exists():
                self.support = read_json(self.source_script.parent / "support.json")["source"]
            elif (self.source_script.parent / "support.py").exists():
                self.support = (self.source_script.parent / "support.py").read_text(encoding="utf-8")
            validate_code(source, self.task.lf_names)
        else:
            source = initialize(self.client, self.parts["train"])
        self.source = self.baseline_source = source
        self.client.frozen_support = self.support
        self._save_source(self.run_dir / "versions/v1", source)
        partial = self.run_dir / "initialization.py"
        if partial.exists():
            partial.unlink()
        return {}

    def _save_source(self, directory, source):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "rules.py").write_text(source, encoding="utf-8")
        if self.support:
            if self.debug:
                write_json(directory / "support.json", {"source": self.support, "immutable": True})
            else:
                (directory / "support.py").write_text(self.support, encoding="utf-8")

    def score(self, source, split, directory, record_result=True):
        predictions = run_records(self.task, source, self.parts["train"], self.parts[split],
                                  self.support, self.show_progress, f"{self.task.name} {split}")
        metrics = evaluate(self.task, self.parts[split], predictions)
        self._detail(directory / f"{split}_predictions.json", predictions)
        self._detail(directory / f"{split}_metrics.json", metrics)
        if self.debug:
            self._detail(directory / f"{split}_lf_quality.json", quality_report(self.task, self.parts[split], predictions))
        baseline = directory == self.run_dir / "versions/v1"
        if record_result:
            self.evaluations.append({"iteration": 0 if baseline or split == "test" else self.state["iteration"],
                                 "attempt": 0 if baseline or split == "test" else self.state["candidate_attempt"],
                                 "version": directory.name if split == "test" else "v1" if baseline else self.state["candidate_version"],
                                 "split": split, "dataset_sha256": self.prepared_digest, **{k: metrics[k] for k in METRICS if k in metrics}})
        return metrics, predictions

    def evaluate_current(self, state):
        directory = self.run_dir / "versions" / state["current_version"]
        metrics, self.train_predictions = self.score(
            self.source, "train", directory, record_result=not self.resuming)
        return {"current_metrics": metrics}

    def analyze_current(self, state):
        errors = sample_errors(self.task, self.parts["train"], self.train_predictions)
        directory = self.run_dir / "versions" / "iterations" / str(state["iteration"])
        self._detail(directory / "training_errors.json", errors)
        if self.debug:
            self._detail(directory / "training_lf_quality.json",
                         quality_report(self.task, self.parts["train"], self.train_predictions))
        if not errors:
            return {"stop_reason": "no_training_errors"}
        analysis = analyze(self.client, self.source, errors, state["iteration"], state["current_version"])
        self._detail(directory / "analysis.json", analysis)
        self._note(f"第 {state['iteration']} 轮分析", str(analysis.get("summary", "")) + "\n\n" +
                   "\n".join(f"- {c['function']}: {c['description']}；证据 {', '.join(map(str, c['evidence_ids']))}"
                             for c in analysis.get("changes", [])))
        return {"analysis": analysis, "candidate_attempt": 1, "feedback": None,
                "candidate_version": f"v{state['version_number'] + 1}"}

    def candidate_dir(self, state):
        return self.run_dir / "versions" / "iterations" / str(state["iteration"]) / f"attempt_{state['candidate_attempt']}"

    def patch_candidate(self, state):
        self.candidate_source, report = patch(self.client, self.source, state["analysis"], state["iteration"],
            state["candidate_version"], state["candidate_attempt"], state.get("feedback"))
        directory = self.candidate_dir(state)
        self._detail(directory / "patch_report.json", report)
        self._note(f"第 {state['iteration']} 轮修复，尝试 {state['candidate_attempt']}",
                   "\n".join(map(str, report.get("changes", []))) or str(report.get("reason", "已生成候选")))
        self._save_source(directory, self.candidate_source)
        return {"candidate_metrics": None}

    def evaluate_candidate(self, state):
        # Like V4, runtime errors stop the run; only metric rejection retries a candidate.
        metrics, predictions = self.score(self.candidate_source, "train", self.candidate_dir(state))
        self.candidate_train_predictions = predictions
        return {"candidate_metrics": metrics}

    def select_candidate(self, state):
        report = compare(self.task, state["current_metrics"], state["candidate_metrics"])
        self._detail(self.candidate_dir(state) / "comparison.json", report)
        self.comparisons.append({"iteration": state["iteration"], "attempt": state["candidate_attempt"],
                                 "selection_split": "train", "dataset_sha256": self.prepared_digest, **report})
        self._note(f"第 {state['iteration']} 轮评估，尝试 {state['candidate_attempt']}",
                   "接受候选。" if report["accepted"] else "拒绝：" + str(report["reasons"]))
        if not report["accepted"] and state["candidate_attempt"] < 2:
            return {"candidate_attempt": state["candidate_attempt"] + 1, "feedback": report}
        completed = state["completed_iterations"] + 1
        update = {"completed_iterations": completed, "feedback": report}
        if report["accepted"]:
            self.source = self.candidate_source
            self.train_predictions = self.candidate_train_predictions
            self._save_source(self.run_dir / "versions" / state["candidate_version"], self.source)
            # Accepted versions carry their own metrics and diagnostics, with full attempts retained.
            version_dir = self.run_dir / "versions" / state["candidate_version"]
            for path in self.candidate_dir(state).glob("*.json") if self.debug else []:
                if path.name != "support.json":
                    shutil.copyfile(path, version_dir / path.name)
            self._detail(version_dir / "provenance.json", {"iteration": state["iteration"],
                       "attempt": state["candidate_attempt"], "source_version": state["current_version"]})
            update.update(current_version=state["candidate_version"], version_number=state["version_number"] + 1,
                          current_metrics=state["candidate_metrics"], accepted_versions=state["accepted_versions"] + 1,
                          iteration=state["iteration"] + 1,
                          stop_reason="iteration_limit" if completed >= state["target_iterations"] else "running")
        else:
            update["stop_reason"] = "quality_gate_rejected"
        self.print_usage(state["iteration"])
        return update

    def evaluate_final(self, state):
        # No test labels or test mistakes have been passed to any model call.
        self.script_evaluations = []
        scripts = sorted((self.run_dir / "versions").glob("v*/rules.py"),
                         key=lambda p: int(p.parent.name[1:]))
        scripts += sorted((self.run_dir / "versions" / "iterations").glob("*/attempt_*/rules.py"),
                          key=lambda p: (int(p.parents[1].name), p.parent.name))
        for path in scripts:
            source = path.read_text(encoding="utf-8")
            metrics = {}
            for split in ("train", "test"):
                metrics[split], _ = self.score(source, split, path.parent, record_result=False)
            entry = {"script": path.relative_to(self.run_dir).as_posix(), "dataset_sha256": self.prepared_digest, **metrics}
            self.script_evaluations.append(entry)
            lines = ["# Script evaluation", "", "Selection uses train only; test is report-only.", "",
                     f"Dataset manifest SHA256: `{self.prepared_digest}`", "",
                     "| Metric | Train | Test |", "| --- | ---: | ---: |"]
            for key in METRICS:
                if key in metrics["train"]:
                    lines.append(f"| {key} | {metrics['train'][key]} | {metrics['test'][key]} |")
            lines.append(f"| rows | {metrics['train']['rows']} | {metrics['test']['rows']} |")
            if self.manifest.get("historically_exposed"):
                lines += ["", "Historical rules may have seen samples now in test; compatibility evaluation only."]
            (path.parent / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        by_script = {row["script"]: row for row in self.script_evaluations}
        before = by_script["versions/v1/rules.py"]["test"]
        after = by_script[f"versions/{state['current_version']}/rules.py"]["test"]
        self.test_comparison = {"baseline": before, "selected": after,
                      "changes": {k: round(after[k] - before[k], 6) for k in before
                                  if isinstance(before[k], (int, float)) and k != "rows"}}
        self._detail(self.run_dir / "versions/final/test_comparison.json", self.test_comparison)
        self._detail(self.run_dir / "selected_version.json", {"version": state["current_version"],
            "script": f"versions/{state['current_version']}/rules.py"})
        return {}


    def _step(self, function):
        """Record the state before/after each visible stage."""
        state = dict(self.state)
        if self.progress is not None:
            self.progress.set_description(f"{self.task.name} {function.__name__}")
            self.progress.set_postfix_str(f"round {state['iteration']}, try {state['candidate_attempt']}")
        self._save_state(state)
        update = function(state)
        self._save_state({**state, **update})
        if self.progress is not None:
            self.progress.update(self.state["completed_iterations"] - state["completed_iterations"])

    def _run_stages(self):
        self._step(self.prepare_data)
        self._step(self.initialize_version)
        self._step(self.evaluate_current)
        if self.initialize_only:
            self._save_state({**self.state, "stop_reason": "initialized"})
        while self.state["stop_reason"] == "running":
            self._step(self.analyze_current)
            if self.state["stop_reason"] != "running":
                break
            while True:
                self._step(self.patch_candidate)
                self._step(self.evaluate_candidate)
                self._step(self.select_candidate)
                if self.state["feedback"]["accepted"] or self.state["stop_reason"] != "running":
                    break
        self._step(self.evaluate_final)

    def print_usage(self, iteration):
        summary = self.ledger.summarize()
        current = summary["by_iteration"].get(str(iteration), {})
        tqdm.write(f"第 {iteration} 轮 token: {current.get('total_tokens', 0)}; "
              f"累计已知 token: {summary['total']['total_tokens']}; 未知请求: {summary['total']['unknown_calls']}")

    def run(self):
        self.progress = tqdm(
            total=self.state["target_iterations"] - self.state["completed_iterations"],
            desc=f"{self.task.name} iterations", unit="round",
            dynamic_ncols=True, disable=not self.show_progress,
            bar_format="{desc}: {n_fmt}/{total_fmt} rounds [{elapsed}] {postfix}",
        )
        try:
            self._run_stages()
        except BaseException as error:
            self.state["stop_reason"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "error"
            self.state["error_type"] = type(error).__name__
            self.state["error_message"] = str(error)
            self._note("运行停止", f"{type(error).__name__}: {error}")
            last_response = getattr(self.client.caller, "last_response", None)
            if not self.debug and last_response:
                (self.run_dir / "failed_response.txt").write_text(str(last_response["content"]), encoding="utf-8")
            self._save_state(self.state)
            raise
        finally:
            self.progress.set_postfix_str(self.state["stop_reason"])
            self.progress.close()
            self.progress = None
            usage = self.ledger.summarize()
            report = {"task": self.task.name, "run_id": self.run_dir.name, **self.state,
                      "usage": usage, "elapsed_seconds": self.previous_elapsed + time.monotonic() - self.started,
                      "script_evaluations": self.script_evaluations,
                      "selection_split": "train",
                      "test_comparison": self.test_comparison,
                      "dataset_sha256": self.prepared_digest,
                      "historically_exposed": self.manifest.get("historically_exposed", self.task.name == "crypto"),
                      "source_script": str(self.source_script) if self.source_script else None}
            self._detail(self.run_dir / "report.json", report)
            write_results(self.run_dir, report, self.evaluations, self.comparisons)
            print(f"任务 {self.task.name} / {self.run_dir.name}: "
                  f"目标 {self.state['target_iterations']} 轮，完成 {self.state['completed_iterations']} 轮，"
                  f"接受 {self.state['accepted_versions']} 个版本；{self.state['stop_reason']}")
            print(f"初始化 token: {usage['initialization']['total_tokens']}; "
                  f"迭代 token: {usage['iterations']['total_tokens']}; 本次已知用量: {usage['total']}")
        return report


def parser():
    result = argparse.ArgumentParser(description="crypto LF iteration and token accounting")
    result.add_argument("--init-only", action="store_true", help="只生成并评估首版，不做分析和修改")
    result.add_argument("--iterations", type=int, help="本次迭代上限；省略时交互询问")
    result.add_argument("--source-script", help="已有 LF 脚本路径；crypto 也可填原 v4 的 v1/v2 等")
    result.add_argument("--new", action="store_true", help="新建首版，跳过当前版本询问")
    result.add_argument("--seed", type=int, default=42)
    result.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bars")
    result.add_argument("--data-dir", "--prepared-dir", dest="prepared_dir", type=Path,
                        help="CSV 数据目录；默认本任务 data/（--prepared-dir 为兼容别名）")
    result.add_argument("--review", type=Path, help="人工确认且绑定源文件哈希的映射 JSON")
    result.add_argument("--prepare-only", action="store_true", help="只准备数据、划分或人工审查清单，无模型调用")
    result.add_argument("--usage-only", action="store_true", help="汇总本任务累计 token")
    result.add_argument("--debug", action="store_true", help="额外保存 JSON 诊断明细；默认只保存核心结果")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.prepare_only and not args.usage_only:
        missing = [name for name in ("openai", "dotenv")
                   if importlib.util.find_spec(name) is None]
        if missing:
            raise RuntimeError(
                f"当前 Python ({sys.executable}) 缺少依赖: {', '.join(missing)}。\n"
                "请先激活已有 Conda base 环境，再在项目根目录运行：\n"
                "python workflows/v4_widely/tasks/crypto/orchestrator.py\n"
                r"CMD 激活命令：D:\Miniconda\Scripts\activate && conda activate base")
    name = TASK_NAME
    task = get_task(name, args.seed)
    directory = task_directory(name)
    if args.usage_only:
        print(task_usage(directory))
        return
    prepared = args.prepared_dir or directory / "data"
    if args.prepare_only:
        parts = prepare(task, prepared, args.review)
        print({key: len(value) for key, value in parts.items()})
        return
    has_versions = any((directory / "versions").glob("v*/rules.py"))
    if args.init_only and has_versions:
        raise ValueError("已有版本时使用 orchestrator.py 继续迭代")
    if args.new and (args.source_script or has_versions):
        raise ValueError("已有版本时直接继续迭代；--new 仅用于尚无版本的任务，不覆盖历史版本")
    resumed = directory if has_versions else None
    source = args.source_script
    if resumed:
        checkpoint = load_checkpoint(directory)
        if source:
            expected = directory / "versions" / checkpoint["state"]["current_version"] / "rules.py"
            if source != checkpoint["state"]["current_version"] and Path(source).resolve() != expected.resolve():
                raise ValueError("已有版本时从当前已接受版本继续，不导入其他起点")
            source = None
        print(f"当前版本 {checkpoint['state']['current_version']}，继续迭代")
    elif source is None and not args.new:
        source = input("起始脚本 [留空生成 v1]: ").strip() or None
    if source and task.name == "crypto" and re.fullmatch(r"v\d+", source):
        source = ROOT / "workflows/v4/versions" / source / f"script_{source}.py"
    if source and not Path(source).is_file():
        raise FileNotFoundError(source)
    iterations = 1 if args.init_only else args.iterations
    if iterations is None:
        iterations = int(input("迭代轮数 [1]: ").strip() or "1")
    if iterations < 1:
        raise ValueError("迭代轮数必须大于 0")
    workflow = Workflow(task, directory, prepared, iterations,
                        source_script=source, review_path=args.review, debug=args.debug, resume=bool(resumed),
                        show_progress=not args.no_progress, initialize_only=args.init_only)
    workflow.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; recorded usage has been saved.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
