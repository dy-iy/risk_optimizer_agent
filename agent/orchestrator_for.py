from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from tqdm import tqdm


# =========================================================
# 固定运行参数
# 说明：
# 1. 版本号仍然在运行时手动输入
# 2. 模块不再手动选择，固定跑：
#    runner -> merger -> evaluator -> slicer -> analyzer_llm -> patcher
# 3. 不再运行 comparator
# 4. 不再保留 nollm analyzer
# =========================================================
INPUT_CSV: Optional[str] = None
GOLD_CSV: Optional[str] = None

# 如果想固定解释器，可以改成：
# PYTHON_EXECUTABLE = r"D:\Miniconda\python.exe"
PYTHON_EXECUTABLE: Optional[str] = None

TIMEOUT = 300

MODEL = "deepseek-v4-pro"
SAMPLE_ROWS = 12
TEXT_LIMIT = 220

ENABLE_PATCHER = True
PATCH_MODEL: Optional[str] = None
PATCH_TEMPERATURE = 0.2

# 一般不用填。默认会使用 analyzer_llm 刚生成的 analysis_json。
# 如果你想让 patcher 使用某个指定 analysis 文件，可以填绝对路径。
PATCH_SOURCE_ANALYSIS_JSON: Optional[str] = None


# =========================
# 进度显示
# =========================
class StageProgress:
    def __init__(self, total: int, enabled: bool = True, desc: str = "Orchestrator"):
        self.enabled = enabled
        self.total = total
        self.current = 0
        self.bar = None

        if self.enabled and tqdm is not None:
            self.bar = tqdm(total=total, desc=desc, ncols=100)
        elif self.enabled:
            print(f"[0/{total}] {desc}", flush=True)

    def update(self, message: str) -> None:
        self.current += 1
        if self.bar is not None:
            self.bar.set_postfix_str(message)
            self.bar.update(1)
        elif self.enabled:
            print(f"[{self.current}/{self.total}] {message}", flush=True)

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


# =========================
# 结果结构
# =========================
@dataclass
class OrchestrateResult:
    success: bool
    current_version: str
    next_version: str

    script_path: str
    input_csv: str
    gold_csv: str

    prediction_csv: str
    merged_csv: str
    eval_json: str
    eval_details_json: str

    false_positive_csv: str
    false_negative_csv: str
    type_mismatch_csv: str
    score_diff_top_csv: str
    slice_log_json: str

    analysis_json: str = ""
    analysis_markdown: str = ""
    llm_payload_json: str = ""

    patched_script: str = ""
    patch_report_json: str = ""
    patch_report_markdown: str = ""
    patch_syntax_ok: bool = False

    orchestrate_json: str = ""
    steps_completed: int = 0
    error_message: str = ""


# =========================
# 通用工具
# =========================
def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: dict) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_project_root() -> Path:
    # 假设 orchestrator.py 放在 agent/ 下
    return Path(__file__).resolve().parent.parent


def normalize_version_name(version: str | int) -> str:
    s = str(version).strip().lower()
    if s.startswith("v"):
        s = s[1:]
    if not s.isdigit():
        raise ValueError(f"版本号不合法: {version}")
    return f"v{s}"


def resolve_version_script(project_root: Path, version_name: str) -> Path:
    candidates = [
        project_root / version_name / "scripts" / f"risk_labeler_{version_name}.py",
        project_root / f"risk_labeler_{version_name}.py",
        project_root / "src" / f"risk_labeler_{version_name}.py",
        project_root / "app" / f"risk_labeler_{version_name}.py",
    ]
    for p in candidates:
        if p.exists():
            return p

    # 默认返回版本文件夹下的脚本路径
    return project_root / version_name / "scripts" / f"risk_labeler_{version_name}.py"


def resolve_default_input_csv(project_root: Path) -> Path:
    return project_root / "data" / "input" / "raw_1000_news.csv"


def resolve_default_gold_csv(project_root: Path) -> Path:
    return project_root / "data" / "gold" / "cleared_news_v2_deepseek_1000_labeled.csv"


# =========================
# 动态导入
# =========================
def import_runner():
    from runner import run_rule_script, save_run_result
    return run_rule_script, save_run_result


def import_merger():
    from merger import merge_gold_and_predictions
    return merge_gold_and_predictions


def import_evaluator():
    from evaluator import evaluate_merged_file
    return evaluate_merged_file


def import_slicer():
    from slicer import slice_errors
    return slice_errors


def import_analyzer_llm():
    from analyzer_llm import analyze_error_files_with_llm
    return analyze_error_files_with_llm


def import_patcher():
    from patcher_llm_v2 import patch_script_with_llm
    return patch_script_with_llm


# comparator 暂时不用，先注释掉
# def import_comparator():
#     from comparator import compare_versions
#     return compare_versions


# =========================
# 路径构造
# =========================
def build_paths(project_root: Path, current_version: str, next_version: str) -> dict[str, Path]:
    current_dir = project_root / current_version
    next_dir = project_root / next_version

    return {
        # 当前版本运行产物
        "prediction_csv": current_dir / "reports" / "predictions" / f"risk_labeler_{current_version}_output.csv",
        "runlog_json": current_dir / "reports" / "predictions" / f"risk_labeler_{current_version}_runlog.json",

        "merged_csv": current_dir / "reports" / "merged" / f"risk_labeler_{current_version}_merged.csv",
        "merge_log_json": current_dir / "reports" / "merged" / f"risk_labeler_{current_version}_merge_log.json",

        "eval_json": current_dir / "reports" / "evals" / f"risk_labeler_{current_version}_eval.json",
        "eval_details_json": current_dir / "reports" / "evals" / f"risk_labeler_{current_version}_eval_details.json",

        "false_positive_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_false_positive.csv",
        "false_negative_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_false_negative.csv",
        "type_mismatch_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_type_mismatch.csv",
        "score_diff_top_csv": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_score_diff_top.csv",
        "slice_log_json": current_dir / "reports" / "errors" / f"risk_labeler_{current_version}_slice_log.json",

        # 只保留 analyzer_llm
        "analysis_json_llm": current_dir / "reports" / "analysis" / f"risk_labeler_{current_version}_analysis_llm.json",
        "analysis_markdown_llm": current_dir / "reports" / "analysis" / f"risk_labeler_{current_version}_analysis_llm.md",
        "llm_payload_json": current_dir / "reports" / "analysis" / f"risk_labeler_{current_version}_analysis_llm_payload.json",

        # 下一版本 patch 产物
        "patched_script": next_dir / "scripts" / f"risk_labeler_{next_version}.py",
        "patch_report_json": next_dir / "reports" / "patched" / f"risk_labeler_{next_version}_patch_report.json",
        "patch_report_markdown": next_dir / "reports" / "patched" / f"risk_labeler_{next_version}_patch_report.md",

        # orchestrator 结果
        "orchestrate_json": next_dir / "reports" / "orchestrations" / f"{current_version}_to_{next_version}_orchestrate.json",

        # compare 结果暂时不用
        # "compare_json": next_dir / "reports" / "compare" / f"{current_version}_vs_{next_version}.json",
        # "compare_markdown": next_dir / "reports" / "compare" / f"{current_version}_vs_{next_version}.md",
    }


# =========================
# 结果组装
# =========================
def build_result(
    *,
    current_version: str,
    next_version: str,
    script_path: Path,
    input_csv: Path,
    gold_csv: Path,
    paths: dict[str, Path],
    analysis_json: str = "",
    analysis_markdown: str = "",
    llm_payload_json: str = "",
    patched_script: str = "",
    patch_report_json: str = "",
    patch_report_markdown: str = "",
    patch_syntax_ok: bool = False,
    steps_completed: int = 0,
    success: bool = False,
    error_message: str = "",
) -> OrchestrateResult:
    return OrchestrateResult(
        success=success,
        current_version=current_version,
        next_version=next_version,

        script_path=str(script_path),
        input_csv=str(input_csv),
        gold_csv=str(gold_csv),

        prediction_csv=str(paths["prediction_csv"]),
        merged_csv=str(paths["merged_csv"]),
        eval_json=str(paths["eval_json"]),
        eval_details_json=str(paths["eval_details_json"]),

        false_positive_csv=str(paths["false_positive_csv"]),
        false_negative_csv=str(paths["false_negative_csv"]),
        type_mismatch_csv=str(paths["type_mismatch_csv"]),
        score_diff_top_csv=str(paths["score_diff_top_csv"]),
        slice_log_json=str(paths["slice_log_json"]),

        analysis_json=analysis_json,
        analysis_markdown=analysis_markdown,
        llm_payload_json=llm_payload_json,

        patched_script=patched_script,
        patch_report_json=patch_report_json,
        patch_report_markdown=patch_report_markdown,
        patch_syntax_ok=patch_syntax_ok,

        orchestrate_json=str(paths["orchestrate_json"]),
        steps_completed=steps_completed,
        error_message=error_message,
    )


def persist_and_return(result: OrchestrateResult, save_path: Path) -> OrchestrateResult:
    write_json(save_path, asdict(result))
    return result


# =========================
# 主流程
# =========================
def orchestrate_pipeline(
    current_version: str | int,
    next_version: str | int,
    input_csv: str | Path | None = None,
    gold_csv: str | Path | None = None,
    python_executable: Optional[str] = None,
    timeout: int = 300,
    model: Optional[str] = None,
    sample_rows: int = 12,
    text_limit: int = 220,
    enable_patcher: bool = True,
    patch_model: Optional[str] = None,
    patch_temperature: float = 0.2,
    patch_source_analysis_json: Optional[str | Path] = None,
    show_progress: bool = True,
) -> OrchestrateResult:
    project_root = resolve_project_root()

    current_version_name = normalize_version_name(current_version)
    next_version_name = normalize_version_name(next_version)

    if current_version_name == next_version_name:
        raise ValueError("当前版本号和下一版本号不能相同")

    script_path = resolve_version_script(project_root, current_version_name)

    if input_csv is None:
        input_csv = resolve_default_input_csv(project_root)
    else:
        input_csv = Path(input_csv).resolve()

    if gold_csv is None:
        gold_csv = resolve_default_gold_csv(project_root)
    else:
        gold_csv = Path(gold_csv).resolve()

    paths = build_paths(project_root, current_version_name, next_version_name)

    total_steps = 5 + (1 if enable_patcher else 0)
    progress = StageProgress(
        total=total_steps,
        enabled=show_progress,
        desc=f"Pipeline {current_version_name}->{next_version_name}",
    )

    steps_completed = 0

    analysis_json = ""
    analysis_markdown = ""
    llm_payload_json = ""

    patched_script = ""
    patch_report_json = ""
    patch_report_markdown = ""
    patch_syntax_ok = False

    try:
        # 0) 先检查当前脚本是否存在
        if not script_path.exists():
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"当前版本脚本不存在: {script_path}",
                ),
                paths["orchestrate_json"],
            )

        # 1) runner
        run_rule_script, save_run_result = import_runner()
        run_result = run_rule_script(
            script_path=script_path,
            input_csv=input_csv,
            output_csv=paths["prediction_csv"],
            python_executable=python_executable,
            timeout=timeout,
        )
        save_run_result(run_result, paths["runlog_json"])

        if not run_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"runner 失败: {run_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("runner 完成")

        # 2) merger
        merge_gold_and_predictions = import_merger()
        merge_result = merge_gold_and_predictions(
            gold_csv=gold_csv,
            pred_csv=paths["prediction_csv"],
            merged_csv=paths["merged_csv"],
            log_json=paths["merge_log_json"],
        )

        if not merge_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"merger 失败: {merge_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("merger 完成")

        # 3) evaluator
        evaluate_merged_file = import_evaluator()
        eval_result = evaluate_merged_file(
            merged_csv=paths["merged_csv"],
            eval_json=paths["eval_json"],
            details_json=paths["eval_details_json"],
        )

        if not eval_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"evaluator 失败: {eval_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("evaluator 完成")

        # 4) slicer
        slice_errors = import_slicer()
        slice_result = slice_errors(
            merged_csv=paths["merged_csv"],
            false_positive_csv=paths["false_positive_csv"],
            false_negative_csv=paths["false_negative_csv"],
            type_mismatch_csv=paths["type_mismatch_csv"],
            score_diff_top_csv=paths["score_diff_top_csv"],
            log_json=paths["slice_log_json"],
            topn_score_diff=200,
        )

        if not slice_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"slicer 失败: {slice_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        steps_completed += 1
        progress.update("slicer 完成")

        # 5) analyzer_llm：固定运行
        analyze_error_files_with_llm = import_analyzer_llm()
        ana_result = analyze_error_files_with_llm(
            false_positive_csv=paths["false_positive_csv"],
            false_negative_csv=paths["false_negative_csv"],
            type_mismatch_csv=paths["type_mismatch_csv"],
            score_diff_top_csv=paths["score_diff_top_csv"],
            analysis_json=paths["analysis_json_llm"],
            analysis_markdown=paths["analysis_markdown_llm"],
            llm_payload_json=paths["llm_payload_json"],
            model=model or "deepseek-chat",
            sample_rows=sample_rows,
            text_limit=text_limit,
            show_progress=True,
        )

        if not ana_result.success:
            progress.close()
            return persist_and_return(
                build_result(
                    current_version=current_version_name,
                    next_version=next_version_name,
                    script_path=script_path,
                    input_csv=input_csv,
                    gold_csv=gold_csv,
                    paths=paths,
                    steps_completed=steps_completed,
                    success=False,
                    error_message=f"analyzer_llm 失败: {ana_result.error_message}",
                ),
                paths["orchestrate_json"],
            )

        analysis_json = str(paths["analysis_json_llm"])
        analysis_markdown = str(paths["analysis_markdown_llm"])
        llm_payload_json = str(paths["llm_payload_json"])

        steps_completed += 1
        progress.update("analyzer_llm 完成")

        # 6) patcher：默认固定运行
        if enable_patcher:
            chosen_analysis_json = None

            if patch_source_analysis_json:
                chosen_analysis_json = Path(patch_source_analysis_json).resolve()
            elif analysis_json:
                chosen_analysis_json = Path(analysis_json).resolve()

            if chosen_analysis_json is None or not chosen_analysis_json.exists():
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        steps_completed=steps_completed,
                        success=False,
                        error_message="patcher 失败: 未找到可用的 analysis_json。",
                    ),
                    paths["orchestrate_json"],
                )

            patch_script_with_llm = import_patcher()
            patch_result = patch_script_with_llm(
                source_script=script_path,
                analysis_json=chosen_analysis_json,
                output_script=paths["patched_script"],
                patch_report_json=paths["patch_report_json"],
                patch_report_markdown=paths["patch_report_markdown"],
                model=patch_model or model or "deepseek-chat",
                temperature=patch_temperature,
                show_progress=True,
            )

            if not patch_result.success:
                progress.close()
                return persist_and_return(
                    build_result(
                        current_version=current_version_name,
                        next_version=next_version_name,
                        script_path=script_path,
                        input_csv=input_csv,
                        gold_csv=gold_csv,
                        paths=paths,
                        analysis_json=analysis_json,
                        analysis_markdown=analysis_markdown,
                        llm_payload_json=llm_payload_json,
                        patched_script=str(paths["patched_script"]),
                        patch_report_json=str(paths["patch_report_json"]),
                        patch_report_markdown=str(paths["patch_report_markdown"]),
                        patch_syntax_ok=patch_result.syntax_ok,
                        steps_completed=steps_completed,
                        success=False,
                        error_message=f"patcher 失败: {patch_result.error_message}",
                    ),
                    paths["orchestrate_json"],
                )

            patched_script = str(paths["patched_script"])
            patch_report_json = str(paths["patch_report_json"])
            patch_report_markdown = str(paths["patch_report_markdown"])
            patch_syntax_ok = patch_result.syntax_ok

            steps_completed += 1
            progress.update("patcher 完成")

        progress.close()

        return persist_and_return(
            build_result(
                current_version=current_version_name,
                next_version=next_version_name,
                script_path=script_path,
                input_csv=input_csv,
                gold_csv=gold_csv,
                paths=paths,
                analysis_json=analysis_json,
                analysis_markdown=analysis_markdown,
                llm_payload_json=llm_payload_json,
                patched_script=patched_script,
                patch_report_json=patch_report_json,
                patch_report_markdown=patch_report_markdown,
                patch_syntax_ok=patch_syntax_ok,
                steps_completed=steps_completed,
                success=True,
                error_message="",
            ),
            paths["orchestrate_json"],
        )

    except Exception as e:
        progress.close()
        return persist_and_return(
            build_result(
                current_version=current_version_name,
                next_version=next_version_name,
                script_path=script_path,
                input_csv=input_csv,
                gold_csv=gold_csv,
                paths=paths,
                analysis_json=analysis_json,
                analysis_markdown=analysis_markdown,
                llm_payload_json=llm_payload_json,
                patched_script=patched_script,
                patch_report_json=patch_report_json,
                patch_report_markdown=patch_report_markdown,
                patch_syntax_ok=patch_syntax_ok,
                steps_completed=steps_completed,
                success=False,
                error_message=f"orchestrator 异常: {e}",
            ),
            paths["orchestrate_json"],
        )


# =========================
# 主程序入口
# =========================
if __name__ == "__main__":
    start_version = int(input("请输入起始版本号：").strip())
    loop_times = int(input("请输入循环次数：").strip())

    project_root = resolve_project_root()

    for i in range(start_version,start_version+loop_times):
        current_version = f"v{i}"
        next_version = f"v{i+1}"

        print("\n" + "=" * 80)
        print(f"开始执行：{current_version} -> {next_version}")
        print("=" * 80)

        next_dir = project_root / next_version
        (next_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "predictions").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "merged").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "evals").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "errors").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "analysis").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "patched").mkdir(parents=True, exist_ok=True)
        (next_dir / "reports" / "orchestrations").mkdir(parents=True, exist_ok=True)

        result = orchestrate_pipeline(
            current_version=current_version,
            next_version=next_version,
            input_csv=INPUT_CSV,
            gold_csv=GOLD_CSV,
            python_executable=PYTHON_EXECUTABLE,
            timeout=TIMEOUT,
            model=MODEL,
            sample_rows=SAMPLE_ROWS,
            text_limit=TEXT_LIMIT,
            enable_patcher=ENABLE_PATCHER,
            patch_model=PATCH_MODEL,
            patch_temperature=PATCH_TEMPERATURE,
            patch_source_analysis_json=PATCH_SOURCE_ANALYSIS_JSON,
            show_progress=True,
        )

        print("=" * 60)
        print("ORCHESTRATE RESULT")
        print("=" * 60)
        print(f"success              : {result.success}")
        print(f"current_version      : {result.current_version}")
        print(f"next_version         : {result.next_version}")
        print(f"script_path          : {result.script_path}")
        print(f"input_csv            : {result.input_csv}")
        print(f"gold_csv             : {result.gold_csv}")

        print(f"prediction_csv       : {result.prediction_csv}")
        print(f"merged_csv           : {result.merged_csv}")
        print(f"eval_json            : {result.eval_json}")
        print(f"eval_details_json    : {result.eval_details_json}")

        print(f"false_positive_csv   : {result.false_positive_csv}")
        print(f"false_negative_csv   : {result.false_negative_csv}")
        print(f"type_mismatch_csv    : {result.type_mismatch_csv}")
        print(f"score_diff_top_csv   : {result.score_diff_top_csv}")
        print(f"slice_log_json       : {result.slice_log_json}")

        print(f"analysis_json        : {result.analysis_json}")
        print(f"analysis_markdown    : {result.analysis_markdown}")
        print(f"llm_payload_json     : {result.llm_payload_json}")

        print(f"patched_script       : {result.patched_script}")
        print(f"patch_report_json    : {result.patch_report_json}")
        print(f"patch_report_markdown: {result.patch_report_markdown}")
        print(f"patch_syntax_ok      : {result.patch_syntax_ok}")

        print(f"orchestrate_json     : {result.orchestrate_json}")
        print(f"steps_completed      : {result.steps_completed}")

        if result.error_message:
            print(f"error_message        : {result.error_message}")

        print("\nResult JSON:")
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        
        if not result.success:
            print(f"\n{current_version} -> {next_version} 失败，停止后续循环。")
            break
