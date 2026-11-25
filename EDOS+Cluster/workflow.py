"""LangGraph：隔离运行、簇反馈、独立候选生成、选择集验收和最终测试。"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import TypedDict
from uuid import uuid4

sys.dont_write_bytecode = True

from langgraph.graph import END, START, StateGraph

if __package__:
    from .tools.acceptance import AcceptanceConfig, assess_candidate
    from .tools.analyzer import AnalyzerResponseError, analyze
    from .tools.clustering import ClusterConfig, build_cluster
    from .tools.data import development_roles, load_data, split_identity
    from .tools.embeddings import EmbeddingConfig, EmbeddingStore
    from .tools.feedback import build_feedback, save_feedback
    from .tools.impact import AGGREGATORS, record_result_impact, show_impact
    from .tools.llm import LLMAPIError, LLMResponseError, call_llm, check_config, show_token_summary
    from .tools.patcher import merge_functions, patch, validate_changes
    from .tools.policy import build_strategy, validate_thresholds
    from .tools.records import save_error, show_result, update_iterations, write_changes
    from .tools.runner import LFValidationError, run_version
else:
    from tools.acceptance import AcceptanceConfig, assess_candidate
    from tools.analyzer import AnalyzerResponseError, analyze
    from tools.clustering import ClusterConfig, build_cluster
    from tools.data import development_roles, load_data, split_identity
    from tools.embeddings import EmbeddingConfig, EmbeddingStore
    from tools.feedback import build_feedback, save_feedback
    from tools.impact import AGGREGATORS, record_result_impact, show_impact
    from tools.llm import LLMAPIError, LLMResponseError, call_llm, check_config, show_token_summary
    from tools.patcher import merge_functions, patch, validate_changes
    from tools.policy import build_strategy, validate_thresholds
    from tools.records import save_error, show_result, update_iterations, write_changes
    from tools.runner import LFValidationError, run_version


@dataclass(frozen=True)
class WorkflowConfig:
    max_lf_changes: int
    max_examples_per_group: int
    feedback_seed: int
    low_coverage_threshold: float
    high_covered_accuracy_threshold: float
    embeddings: EmbeddingConfig
    clusters: ClusterConfig
    acceptance: AcceptanceConfig
    candidate_count: int
    selection_fraction: float
    final_test: bool

    def __post_init__(self):
        validate_thresholds(self.low_coverage_threshold, self.high_covered_accuracy_threshold)
        if type(self.max_lf_changes) is not int or self.max_lf_changes < 1:
            raise ValueError("LF 改动上限必须为正整数")
        if type(self.max_examples_per_group) is not int or self.max_examples_per_group < 0:
            raise ValueError("modify 样例数量必须为非负整数")
        if type(self.feedback_seed) is not int or not 0 < self.selection_fraction < 1:
            raise ValueError("seed 必须是整数，选择集比例须在 (0,1) 内")
        if type(self.candidate_count) is not int or not 1 <= self.candidate_count <= 3:
            raise ValueError("每轮独立候选数量须在 1 至 3 之间")
        if not self.acceptance.enabled and self.candidate_count != 1:
            raise ValueError("关闭选择集验收时只能使用一个候选，避免隐式择优")
        if type(self.final_test) is not bool:
            raise ValueError("final_test 必须是布尔值")


class WorkflowState(TypedDict, total=False):
    folder: Path
    task_folder: Path
    settings: WorkflowConfig
    roles: dict
    run_id: str
    rounds: int
    round_index: int
    current_version: str
    current: dict
    next_version: str
    current_code: str
    round_dir: Path
    candidate_root: Path
    candidate_index: int
    feedback: dict
    cluster: dict | None
    skip_reason: str | None
    analysis: dict | None
    analysis_attempt: int
    analysis_response: str | None
    analysis_error: str | None
    functions_code: str | None
    patch_attempt: int
    patch_error: str | None
    changes: list | None
    candidate_result: dict | None
    candidate_failed: bool
    candidates: list
    vectors: dict
    label_attempts: dict
    attempted_clusters: list
    history: list


def _json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    try:
        pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _output(state):
    output = state["candidate_root"] / "versions" / state["next_version"] / "results"
    output.mkdir(parents=True, exist_ok=True)
    return output


def _ask(state, stage):
    return partial(call_llm, version_name=f"r{state['round_index']:03d}/c{state['candidate_index']:02d}/{state['next_version']}",
                   folder=state["folder"], task_folder=state["task_folder"], run_id=state["run_id"], stage=stage)


def _history_row(state, result, status, *, action="", accepted_candidate=None, decision=None):
    metrics = result["aggregators"]["simple_vote"]["metrics"]
    quality = (decision or {}).get("selection_quality", {})
    return {"round": state["round_index"], "version": metrics["lf_version"], "status": status,
            "action": action, "mode": state["settings"].clusters.mode, "accepted_candidate": accepted_candidate,
            "train_output_coverage": metrics["train"]["coverage"],
            "train_lf_coverage": metrics["train"]["lf_coverage"],
            "discovery_output_coverage": metrics["valid"]["coverage"],
            "discovery_covered_accuracy": metrics["valid"]["covered_only"]["accuracy"],
            "discovery_macro_f1": metrics["valid"]["all_samples"]["macro_f1"],
            "selection_output_coverage": metrics["selection"]["coverage"],
            "selection_covered_accuracy": metrics["selection"]["covered_only"]["accuracy"],
            "selection_macro_f1": metrics["selection"]["all_samples"]["macro_f1"],
            "new_correct_from_all_abstain": quality.get("new_correct_from_all_abstain", 0),
            "new_wrong_from_all_abstain": quality.get("new_wrong_from_all_abstain", 0),
            "net_correct_gain": quality.get("net_correct_gain", 0)}


def _save_progress(state, current_version, history):
    import csv
    _json(state["folder"] / "rounds.json", history)
    with (state["folder"] / "rounds.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    _json(state["folder"] / "run_state.json", {
        "status": "running", "current_version": current_version, "rounds_consumed": state["round_index"],
        "label_attempts": state.get("label_attempts", {}), "attempted_clusters": state.get("attempted_clusters", []),
    })


def evaluate_initial(state):
    print(f"\n评价起点 {state['current_version']} · 输出 {state['folder']}", flush=True)
    current = run_version(state["folder"], state["current_version"], task_folder=state["task_folder"], roles=state["roles"])
    for item in current["aggregators"].values():
        update_iterations(state["folder"], item["metrics"])
        show_result(item["metrics"])
    history = [_history_row(state, current, "baseline")]
    _save_progress(state, state["current_version"], history)
    return {"current": current, "history": history}


def _candidate_reset(state, index):
    root = state["round_dir"] / f"candidate_{index:02d}"
    root.mkdir(parents=True, exist_ok=False)
    return {"candidate_root": root, "candidate_index": index, "analysis": None, "analysis_attempt": 0,
            "analysis_response": None, "analysis_error": None, "functions_code": None, "patch_attempt": 0,
            "patch_error": None, "changes": None, "candidate_result": None, "candidate_failed": False}


def prepare_iteration(state):
    settings, current = state["settings"], state["current"]
    round_index = state["round_index"] + 1
    round_dir = state["folder"] / "rounds" / f"r{round_index:03d}"
    round_dir.mkdir(parents=True, exist_ok=False)
    next_version = f"v{int(state['current_version'][1:]) + 1}"
    current_code = (state["folder"] / "versions" / state["current_version"] / "lfs.py").read_text(encoding="utf-8-sig")
    simple = current["aggregators"]["simple_vote"]
    strategy = build_strategy({"aggregator": "simple_vote", "train_statistics": simple["metrics"]["train"],
                               "valid_metrics": simple["metrics"]["valid"]}, settings.max_lf_changes,
                              settings.low_coverage_threshold, settings.high_covered_accuracy_threshold)
    cluster, skip_reason = None, None
    vectors = state.get("vectors")
    attempts = dict(state.get("label_attempts", {}))
    attempted = list(state.get("attempted_clusters", []))
    if strategy["selected_action"] == "add":
        strategy["max_changes"] = 1
        if vectors is None:
            store = EmbeddingStore(state["task_folder"] / "cache" / "embeddings", settings.embeddings)
            vectors, stats = {}, {}
            for split in ("train", "valid"):
                vectors[split], stats[split] = store.encode(simple[split]["rows"], split)
            _json(state["folder"] / "embedding_cache.json", stats)
        cluster, catalog = build_cluster(
            {k: simple[k] for k in ("valid", "train", "lf_names")}, vectors, settings.clusters,
            seed=settings.feedback_seed, round_index=round_index, label_attempts=attempts, excluded=attempted,
        )
        _json(round_dir / "clusters.json", catalog)
        if cluster is None:
            skip_reason = "no_eligible_cluster"
        else:
            key = str(cluster["target_label"])
            attempts[key] = attempts.get(key, 0) + 1
            attempted.append(cluster["cluster_id"])
    feedback = None
    if skip_reason is None:
        feedback = build_feedback(current, current_code, state["current_version"], settings.max_examples_per_group,
                                  settings.feedback_seed, action=strategy["selected_action"], cluster=cluster)
        feedback["strategy_context"] = strategy
        save_feedback(round_dir, feedback)
    print(f"\n第 {round_index}/{state['rounds']} 轮 · {strategy['state']} · {strategy['selected_action']}"
          + (f" · {cluster['cluster_id']}" if cluster else "") + (f" · {skip_reason}" if skip_reason else ""), flush=True)
    update = {"round_index": round_index, "round_dir": round_dir, "next_version": next_version,
              "current_code": current_code, "feedback": feedback, "cluster": cluster, "skip_reason": skip_reason,
              "candidates": [], "label_attempts": attempts, "attempted_clusters": attempted}
    if vectors is not None:
        update["vectors"] = vectors
    update.update(_candidate_reset({**state, **update}, 1))
    return update


def analyze_plan(state):
    attempt = state["analysis_attempt"] + 1
    stage = "Analyzer" if attempt == 1 else "Analyzer 修复"
    output = _output(state)
    feedback = {**state["feedback"], "candidate_context": {
        "index": state["candidate_index"], "count": state["settings"].candidate_count,
        "previous_plans": [c["analysis"]["operations"] for c in state["candidates"] if c.get("analysis")],
    }}
    save_feedback(output, feedback)
    raw = output / ("analysis_raw.txt" if attempt == 1 else "analysis_repair_raw.txt")
    try:
        analysis = analyze(feedback, _ask(state, stage), on_response=partial(raw.write_text, encoding="utf-8"),
                           previous_response=state["analysis_response"], error=state["analysis_error"])
    except (json.JSONDecodeError, AnalyzerResponseError) as error:
        previous = error.raw_response if isinstance(error, AnalyzerResponseError) else error.doc
        if isinstance(error, AnalyzerResponseError):
            _json(output / f"analysis_invalid_{attempt}.json", error.parsed_response)
        save_error(output, stage, error, attempt)
        return {"analysis_attempt": attempt, "analysis_response": previous,
                "analysis_error": f"{type(error).__name__}: {error}", "candidate_failed": attempt >= 2}
    except Exception as error:
        if isinstance(error, LLMResponseError):
            raw.write_text(error.content, encoding="utf-8")
        save_error(output, stage, error, attempt)
        raise
    _json(output / "analysis.json", analysis)
    return {"analysis": analysis, "analysis_attempt": attempt, "analysis_error": None}


def _patch_failure(state, error, attempt, functions_code):
    output = _output(state)
    stage = "Patcher" if attempt == 1 else "Patcher 修复"
    save_error(output, stage, error, attempt)
    if isinstance(error, (LLMAPIError, OSError)):
        raise error
    if isinstance(error, LLMResponseError):
        functions_code = error.content
        (output / ("patcher_raw.txt" if attempt == 1 else "patcher_repair_raw.txt")).write_text(error.content, encoding="utf-8")
    return {"patch_attempt": attempt, "functions_code": functions_code,
            "patch_error": f"{type(error).__name__}: {error}", "changes": None,
            "candidate_result": None, "candidate_failed": attempt >= 2}


def patch_candidate(state):
    attempt, analysis = state["patch_attempt"] + 1, state["analysis"]
    stage = "Patcher" if attempt == 1 else "Patcher 修复"
    output = _output(state)
    functions_code = state["functions_code"]
    budget = state["feedback"]["strategy_context"]["max_changes"]
    try:
        functions_code = patch(state["current_code"], analysis, budget, _ask(state, stage),
                               error=state["patch_error"], previous_functions=functions_code,
                               on_response=partial((output / ("patcher_raw.txt" if attempt == 1 else "patcher_repair_raw.txt")).write_text,
                                                   encoding="utf-8"), cluster=state["cluster"])
        candidate = merge_functions(state["current_code"], analysis, functions_code)
        changes = validate_changes(state["current_code"], candidate, budget)
        actual = {(item["action"], item["target"]) for item in changes}
        proposed = {(analysis["action"], item["target"]) for item in analysis["operations"]}
        if actual != proposed:
            raise ValueError(f"实际 LF 改动与计划不符：多改 {sorted(actual - proposed)}；漏改 {sorted(proposed - actual)}")
        (output.parent / "lfs.py").write_text(candidate, encoding="utf-8", newline="")
    except Exception as error:
        return _patch_failure(state, error, attempt, functions_code)
    return {"patch_attempt": attempt, "functions_code": functions_code, "patch_error": None, "changes": changes}


def evaluate_candidate(state):
    try:
        result = run_version(state["candidate_root"], state["next_version"],
                             task_folder=state["task_folder"], roles=state["roles"])
        if state["analysis"]["action"] == "add":
            simple = result["aggregators"]["simple_vote"]
            label = state["cluster"]["target_label"]
            for operation in state["analysis"]["operations"]:
                index = simple["lf_names"].index(operation["target"])
                for split in ("train", "valid"):
                    if any(int(v) not in (-1, label) for v in simple[split]["L"][:, index]):
                        raise LFValidationError(f"新增 LF {operation['target']} 只能返回目标标签 {label} 或 -1")
    except Exception as error:
        return _patch_failure(state, error, state["patch_attempt"], state["functions_code"])
    return {"candidate_result": result, "patch_error": None}


def finish_candidate(state):
    output = _output(state)
    if state["candidate_failed"]:
        decision = {"eligible": False, "reasons": ["technical_validation_failed"], "rank": []}
    else:
        result = state["candidate_result"]
        write_changes(output, state["current_version"], state["next_version"], state["changes"], state["analysis"])
        for aggregator in AGGREGATORS:
            impact = record_result_impact(output.parent / aggregator, state["current"], result, aggregator)
            show_impact(impact)
        decision = assess_candidate(state["current"], result, state["analysis"]["action"], state["settings"].acceptance)
    _json(output / "acceptance.json", decision)
    print(f"候选 {state['candidate_index']}：{'通过验收' if decision['eligible'] else '未接受'} · "
          + ", ".join(decision["reasons"]), flush=True)
    entry = {"index": state["candidate_index"], "root": state["candidate_root"], "analysis": state["analysis"],
             "result": state["candidate_result"], "decision": decision}
    return {"candidates": [*state["candidates"], entry]}


def next_candidate(state):
    return _candidate_reset(state, state["candidate_index"] + 1)


def persist_iteration(state):
    current, version = state["current"], state["current_version"]
    eligible = [c for c in state["candidates"] if c["decision"]["eligible"]]
    winner = max(eligible, key=lambda c: (*c["decision"]["rank"], -c["index"])) if eligible else None
    status = "skipped" if state["skip_reason"] else "rejected"
    if winner is not None:
        version = state["next_version"]
        shutil.copytree(winner["root"] / "versions" / version, state["folder"] / "versions" / version)
        current = winner["result"]
        status = "accepted"
        for item in current["aggregators"].values():
            update_iterations(state["folder"], item["metrics"])
            show_result(item["metrics"])
    _json(state["round_dir"] / "selection.json", {
        "status": status, "reason": state["skip_reason"], "baseline_version": state["current_version"],
        "current_version": version, "accepted_candidate": winner["index"] if winner else None,
        "candidates": [{"index": c["index"], "decision": c["decision"]} for c in state["candidates"]],
    })
    action = state["feedback"]["strategy_context"]["selected_action"] if state["feedback"] else "add"
    history = [*state["history"], _history_row(state, current, status, action=action,
               accepted_candidate=winner["index"] if winner else None, decision=winner["decision"] if winner else None)]
    _save_progress(state, version, history)
    return {"current_version": version, "current": current, "history": history}


def next_iteration(state):
    return "continue" if state["round_index"] < state["rounds"] else "done"


def after_analysis(state):
    return "failed" if state["candidate_failed"] else ("repair" if state["analysis_error"] else "ready")


def after_patch(state):
    return "failed" if state["candidate_failed"] else ("repair" if state["patch_error"] else "ready")


def build_workflow():
    graph = StateGraph(WorkflowState)
    for name, function in (("evaluate_initial", evaluate_initial), ("prepare_iteration", prepare_iteration),
                           ("analyze", analyze_plan), ("patch", patch_candidate), ("evaluate_candidate", evaluate_candidate),
                           ("finish_candidate", finish_candidate), ("next_candidate", next_candidate),
                           ("persist_iteration", persist_iteration)):
        graph.add_node(name, function)
    graph.add_edge(START, "evaluate_initial")
    graph.add_conditional_edges("evaluate_initial", next_iteration, {"continue": "prepare_iteration", "done": END})
    graph.add_conditional_edges("prepare_iteration", lambda s: "skip" if s["skip_reason"] else "ready",
                                {"skip": "persist_iteration", "ready": "analyze"})
    graph.add_conditional_edges("analyze", after_analysis, {"failed": "finish_candidate", "repair": "analyze", "ready": "patch"})
    graph.add_conditional_edges("patch", after_patch, {"failed": "finish_candidate", "repair": "patch", "ready": "evaluate_candidate"})
    graph.add_conditional_edges("evaluate_candidate", after_patch,
                                {"failed": "finish_candidate", "repair": "patch", "ready": "finish_candidate"})
    graph.add_conditional_edges("finish_candidate", lambda s: "next" if s["candidate_index"] < s["settings"].candidate_count else "done",
                                {"next": "next_candidate", "done": "persist_iteration"})
    graph.add_edge("next_candidate", "analyze")
    graph.add_conditional_edges("persist_iteration", next_iteration, {"continue": "prepare_iteration", "done": END})
    return graph.compile()


def run_iterations(folder, current_version, rounds, *, config, run_dir=None):
    task_folder = Path(folder).resolve()
    if not isinstance(config, WorkflowConfig):
        raise TypeError("config 必须为总控构造的 WorkflowConfig")
    if not re.fullmatch(r"v[1-9]\d*", current_version):
        raise ValueError("起始版本必须为 v1、v2 等")
    source = task_folder / "versions" / current_version / "lfs.py"
    if not source.is_file() or type(rounds) is not int or rounds < 0:
        raise ValueError("起始 LF 必须存在，追加轮数必须为非负整数")
    llm_identity = check_config(task_folder) if rounds else None
    run_id = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    output = Path(run_dir).resolve() if run_dir else task_folder / "runs" / f"{config.clusters.mode}-{run_id}"
    # 新运行目录必须不存在，防止覆盖历史实验或混合候选。
    output.mkdir(parents=True, exist_ok=False)
    initial = output / "versions" / current_version
    initial.mkdir(parents=True)
    shutil.copy2(source, initial / "lfs.py")
    data = load_data(task_folder, include_test=False)
    metadata = json.loads((task_folder / "data" / "split.json").read_text(encoding="utf-8"))
    roles = development_roles(data["valid"], metadata["csv_sha256"]["valid"], selection_fraction=config.selection_fraction,
                              seed=config.feedback_seed, path=output / "development_roles.json")
    _json(output / "run_config.json", {"protocol": 1, "run_id": run_id, "config": asdict(config), "llm": llm_identity,
          "max_llm_calls": rounds * config.candidate_count * 4,
          "requested_rounds": rounds, "task_folder": str(task_folder), "source_version": current_version,
          "source_lfs_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
          "data_split": split_identity(metadata), "development_roles_sha256": roles["sha256"]})
    state = {"folder": output, "task_folder": task_folder, "settings": config, "roles": roles, "run_id": run_id,
             "rounds": rounds, "round_index": 0, "current_version": current_version,
             "label_attempts": {}, "attempted_clusters": [], "history": []}
    try:
        result = build_workflow().invoke(state, {"recursion_limit": max(25, (20 * config.candidate_count + 4) * rounds + 5)})
        if config.final_test:
            final_root = output / "final"
            final_version = final_root / "versions" / result["current_version"]
            final_version.mkdir(parents=True)
            shutil.copy2(output / "versions" / result["current_version"] / "lfs.py", final_version / "lfs.py")
            _json(final_root / "development_roles.json", roles)
            # 测试只在全部选择结束后运行；错误不回流给 Patcher。
            final = run_version(final_root, result["current_version"], task_folder=task_folder, include_test=True, roles=roles)
            for item in final["aggregators"].values():
                show_result(item["metrics"])
        progress = json.loads((output / "run_state.json").read_text(encoding="utf-8"))
        _json(output / "run_state.json", {**progress, "status": "complete", "final_test_evaluated": config.final_test})
        print(f"\n完成：{result['current_version']} · 消耗 {result['round_index']} 轮 · {output}", flush=True)
        return {**result["current"], "run_folder": str(output), "current_version": result["current_version"]}
    except BaseException:
        path = output / "run_state.json"
        progress = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"current_version": current_version, "rounds_consumed": 0}
        _json(path, {**progress, "status": "interrupted"})
        raise
    finally:
        show_token_summary(output, run_id)
