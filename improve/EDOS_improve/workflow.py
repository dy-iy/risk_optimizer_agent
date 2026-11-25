"""LangGraph 编排：评价、反馈、分析、修改、复核和保存。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
import json
from pathlib import Path
import re
import sys
from typing import TypedDict
from uuid import uuid4

sys.dont_write_bytecode = True

from langgraph.graph import END, START, StateGraph

if __package__:
    from .tools.analyzer import AnalyzerResponseError, analyze
    from .tools.feedback import build_feedback, save_feedback
    from .tools.impact import AGGREGATORS, record_impact, show_impact
    from .tools.llm import LLMAPIError, LLMResponseError, call_llm, check_config, show_token_summary
    from .tools.patcher import merge_functions, patch, validate_changes
    from .tools.policy import build_strategy, validate_thresholds
    from .tools.records import save_error, show_result, update_iterations, write_changes
    from .tools.runner import RESULT_FILES, SHARED_FILES, run_version
else:
    from tools.analyzer import AnalyzerResponseError, analyze
    from tools.feedback import build_feedback, save_feedback
    from tools.impact import AGGREGATORS, record_impact, show_impact
    from tools.llm import LLMAPIError, LLMResponseError, call_llm, check_config, show_token_summary
    from tools.patcher import merge_functions, patch, validate_changes
    from tools.policy import build_strategy, validate_thresholds
    from tools.records import save_error, show_result, update_iterations, write_changes
    from tools.runner import RESULT_FILES, SHARED_FILES, run_version


@dataclass(frozen=True)
class WorkflowConfig:
    max_lf_changes: int
    max_examples_per_group: int
    feedback_seed: int
    low_coverage_threshold: float
    high_covered_accuracy_threshold: float

    def __post_init__(self):
        validate_thresholds(self.low_coverage_threshold, self.high_covered_accuracy_threshold)
        if type(self.max_lf_changes) is not int or self.max_lf_changes < 1:
            raise ValueError("LF 改动上限必须是正整数")
        if type(self.max_examples_per_group) is not int or self.max_examples_per_group < 0:
            raise ValueError("每组反馈样例数必须是非负整数")
        if type(self.feedback_seed) is not int:
            raise ValueError("反馈抽样种子必须是整数")


class WorkflowState(TypedDict, total=False):
    folder: Path
    settings: WorkflowConfig
    run_id: str
    rounds: int
    round_index: int
    current_version: str
    current: dict
    next_version: str
    current_code: str
    feedback: dict
    analysis: dict | None
    analysis_attempt: int
    analysis_response: str | None
    analysis_error: str | None
    functions_code: str | None
    patch_attempt: int
    patch_error: str | None
    changes: list[dict] | None
    candidate_result: dict | None


def evaluate_initial(state: WorkflowState):
    print(f"\n评价起点 {state['current_version']}", flush=True)
    current = run_version(state["folder"], state["current_version"])
    for item in current["aggregators"].values():
        update_iterations(state["folder"], item["metrics"])
        show_result(item["metrics"])
    return {"current": current}


def prepare_iteration(state: WorkflowState):
    folder, settings = state["folder"], state["settings"]
    current_version = state["current_version"]
    next_version = f"v{int(current_version[1:]) + 1}"
    round_index = state["round_index"] + 1
    print(f"\n第 {round_index}/{state['rounds']} 轮：{current_version} → {next_version}", flush=True)
    with (folder / "versions" / current_version / "lfs.py").open(encoding="utf-8", newline="") as stream:
        current_code = stream.read()
    # 先仅用训练/验证统计确定本轮操作，再按操作抽样；不接触测试指标。
    metrics = state["current"]["aggregators"]["simple_vote"]["metrics"]
    strategy = build_strategy(
        {"aggregator": "simple_vote", "train_statistics": metrics["train"], "valid_metrics": metrics["valid"]},
        settings.max_lf_changes,
        settings.low_coverage_threshold, settings.high_covered_accuracy_threshold,
    )
    feedback = build_feedback(state["current"], current_code, current_version,
                              settings.max_examples_per_group, settings.feedback_seed,
                              action=strategy["selected_action"])
    feedback["strategy_context"] = strategy
    print(f"本轮策略：{strategy['state']} · {strategy['optimization_goal']} · "
          f"本轮操作 {strategy['selected_action']}", flush=True)
    analysis_output = folder / "versions" / current_version / "results"
    save_feedback(analysis_output, feedback)
    for name in ("analysis.json", "analysis_raw.txt", "analysis_repair_raw.txt",
                 "analysis_invalid.json", "analysis_repair_invalid.json"):
        (analysis_output / name).unlink(missing_ok=True)
    output = folder / "versions" / next_version / "results"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("feedback.json", "analysis.json", "analysis_raw.txt", "analysis_repair_raw.txt",
                 "analysis_invalid.json", "analysis_repair_invalid.json", "patcher_raw.txt",
                 "patcher_repair_raw.txt", "changes.md", "errors.json", *SHARED_FILES):
        (output / name).unlink(missing_ok=True)
    for aggregator in AGGREGATORS:
        for name in RESULT_FILES:
            (output.parent / aggregator / name).unlink(missing_ok=True)
    return {
        "round_index": round_index, "next_version": next_version,
        "current_code": current_code, "feedback": feedback,
        "analysis": None, "analysis_attempt": 0, "analysis_response": None, "analysis_error": None,
        "functions_code": None, "patch_attempt": 0, "patch_error": None,
        "changes": None, "candidate_result": None,
    }


def analyze_plan(state: WorkflowState):
    folder = state["folder"]
    attempt = state["analysis_attempt"] + 1
    stage = "Analyzer" if attempt == 1 else "Analyzer 修复"
    raw_name = "analysis_raw.txt" if attempt == 1 else "analysis_repair_raw.txt"
    analysis_output = folder / "versions" / state["current_version"] / "results"
    output = folder / "versions" / state["next_version"] / "results"
    ask = partial(call_llm, version_name=state["next_version"], folder=folder, run_id=state["run_id"], stage=stage)
    try:
        print(f"{stage} 分析中……", flush=True)
        analysis = analyze(
            state["feedback"], ask,
            on_response=partial((analysis_output / raw_name).write_text, encoding="utf-8"),
            previous_response=state["analysis_response"], error=state["analysis_error"],
        )
    except (json.JSONDecodeError, AnalyzerResponseError) as error:
        if isinstance(error, AnalyzerResponseError):
            previous_response = error.raw_response
            invalid_name = "analysis_invalid.json" if attempt == 1 else "analysis_repair_invalid.json"
            (analysis_output / invalid_name).write_text(
                json.dumps(error.parsed_response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        else:
            previous_response = error.doc
        save_error(output, stage, error, attempt)
        if attempt == 2:
            raise
        print("Analyzer 回复未通过解析或校验，反馈具体错误并纠正 1 次。", flush=True)
        return {"analysis_attempt": attempt, "analysis_response": previous_response,
                "analysis_error": f"{type(error).__name__}: {error}"}
    except Exception as error:
        if isinstance(error, LLMResponseError):
            (analysis_output / raw_name).write_text(error.content, encoding="utf-8")
        save_error(output, stage, error, attempt)
        raise
    action_name = {"add": "新增", "modify": "修改"}[analysis["action"]]
    print(f"本轮操作：{action_name} LF", flush=True)
    (analysis_output / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"analysis": analysis, "analysis_attempt": attempt, "analysis_error": None}


def _patch_failure(state: WorkflowState, error, attempt, functions_code):
    """生成和执行错误共用同一修复额度；API/文件错误立即停止。"""
    output = state["folder"] / "versions" / state["next_version"] / "results"
    stage = "Patcher" if attempt == 1 else "Patcher 修复"
    save_error(output, stage, error, attempt)
    if isinstance(error, (LLMAPIError, OSError)):
        raise error
    if isinstance(error, LLMResponseError):
        functions_code = error.content
        raw_name = "patcher_raw.txt" if attempt == 1 else "patcher_repair_raw.txt"
        (output / raw_name).write_text(error.content, encoding="utf-8")
    if attempt == 2:
        raise RuntimeError(f"{state['next_version']} {stage}失败，已停止，记录见 {output / 'errors.json'}") from error
    print("候选运行失败，反馈错误并额外修复 1 次。", flush=True)
    return {"patch_attempt": attempt, "functions_code": functions_code,
            "patch_error": f"{type(error).__name__}: {error}", "changes": None, "candidate_result": None}


def patch_candidate(state: WorkflowState):
    attempt = state["patch_attempt"] + 1
    settings, analysis = state["settings"], state["analysis"]
    stage = "Patcher" if attempt == 1 else "Patcher 修复"
    raw_name = "patcher_raw.txt" if attempt == 1 else "patcher_repair_raw.txt"
    output = state["folder"] / "versions" / state["next_version"] / "results"
    ask = partial(call_llm, version_name=state["next_version"], folder=state["folder"], run_id=state["run_id"], stage=stage)
    functions_code = state["functions_code"]
    try:
        print(f"{stage} 生成目标函数中……", flush=True)
        functions_code = patch(
            state["current_code"], analysis, settings.max_lf_changes, ask,
            error=state["patch_error"], previous_functions=functions_code,
            on_response=partial((output / raw_name).write_text, encoding="utf-8"),
        )
        candidate = merge_functions(state["current_code"], analysis, functions_code)
        (output.parent / "lfs.py").write_text(candidate, encoding="utf-8", newline="")
        changes = validate_changes(state["current_code"], candidate, settings.max_lf_changes)
        actual = {(item["action"], item["target"]) for item in changes}
        proposed = {(analysis["action"], item["target"]) for item in analysis["operations"]}
        if actual != proposed:
            raise ValueError(f"实际 LF 改动与计划不符：多改 {sorted(actual - proposed)}；漏改 {sorted(proposed - actual)}")
    except Exception as error:
        return _patch_failure(state, error, attempt, functions_code)
    return {"patch_attempt": attempt, "functions_code": functions_code, "patch_error": None,
            "changes": changes, "candidate_result": None}


def evaluate_candidate(state: WorkflowState):
    try:
        result = run_version(state["folder"], state["next_version"])
    except Exception as error:
        return _patch_failure(state, error, state["patch_attempt"], state["functions_code"])
    return {"candidate_result": result, "patch_error": None}


def persist_iteration(state: WorkflowState):
    folder = state["folder"]
    output = folder / "versions" / state["next_version"] / "results"
    write_changes(output, state["current_version"], state["next_version"], state["changes"], state["analysis"])
    try:
        for aggregator in AGGREGATORS:
            impact = record_impact(folder, state["current_version"], state["next_version"], aggregator)
            show_impact(impact)
    except Exception as error:
        save_error(output, "Impact", error)
        raise
    result = state["candidate_result"]
    for item in result["aggregators"].values():
        update_iterations(folder, item["metrics"])
        show_result(item["metrics"])
    return {"current_version": state["next_version"], "current": result}


def next_iteration(state: WorkflowState):
    return "continue" if state["round_index"] < state["rounds"] else "done"


def after_analysis(state: WorkflowState):
    return "repair" if state["analysis_error"] is not None else "ready"


def after_patch(state: WorkflowState):
    return "repair" if state["patch_error"] is not None else "ready"


def build_workflow():
    """节点不启用自动重试；纠正和修复额度由状态及条件边显式控制。"""
    graph = StateGraph(WorkflowState)
    graph.add_node("evaluate_initial", evaluate_initial)
    graph.add_node("prepare_iteration", prepare_iteration)
    graph.add_node("analyze", analyze_plan)
    graph.add_node("patch", patch_candidate)
    graph.add_node("evaluate_candidate", evaluate_candidate)
    graph.add_node("persist_iteration", persist_iteration)
    graph.add_edge(START, "evaluate_initial")
    graph.add_conditional_edges("evaluate_initial", next_iteration, {"continue": "prepare_iteration", "done": END})
    graph.add_edge("prepare_iteration", "analyze")
    graph.add_conditional_edges("analyze", after_analysis, {"repair": "analyze", "ready": "patch"})
    graph.add_conditional_edges("patch", after_patch, {"repair": "patch", "ready": "evaluate_candidate"})
    graph.add_conditional_edges("evaluate_candidate", after_patch, {"repair": "patch", "ready": "persist_iteration"})
    graph.add_conditional_edges("persist_iteration", next_iteration, {"continue": "prepare_iteration", "done": END})
    return graph.compile()


def run_iterations(folder, current_version, rounds, *, config):
    folder = Path(folder).resolve()
    if not isinstance(config, WorkflowConfig):
        raise TypeError("config 必须是总控构造的 WorkflowConfig")
    if not re.fullmatch(r"v[1-9]\d*", current_version):
        raise ValueError("当前版本请填写 v1、v2、v3 这样的名称")
    if not (folder / "versions" / current_version / "lfs.py").is_file():
        raise ValueError(f"{current_version} 没有 lfs.py")
    if not isinstance(rounds, int) or rounds < 0:
        raise ValueError("追加轮数必须是非负整数；0 表示只评价当前版本")
    if rounds:
        check_config(folder)
    run_id = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    state: WorkflowState = {
        "folder": folder, "settings": config,
        "run_id": run_id, "rounds": rounds, "round_index": 0, "current_version": current_version,
    }
    try:
        # 上限随轮数增长，覆盖每轮两次分析、两次修改/评价及保存，避免默认 25 步提前中断。
        result = build_workflow().invoke(state, {"recursion_limit": max(25, 10 * rounds + 5)})
        print(f"\n完成：当前版本 {result['current_version']}，追加 {rounds} 轮。", flush=True)
        return result["current"]
    finally:
        show_token_summary(folder, run_id)
