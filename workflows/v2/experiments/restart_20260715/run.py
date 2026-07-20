from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
VERSIONS_ROOT = EXPERIMENT_ROOT / "versions"


def configure_environment() -> None:
    os.environ["RISK_VERSIONS_DIR"] = str(VERSIONS_ROOT)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.chdir(PROJECT_ROOT)
    project_root_text = str(PROJECT_ROOT)
    if project_root_text not in sys.path:
        sys.path.insert(0, project_root_text)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def discover_latest_version() -> int:
    versions: list[int] = []
    if not VERSIONS_ROOT.exists():
        return 1
    for version_dir in VERSIONS_ROOT.iterdir():
        name = version_dir.name.lower()
        if not version_dir.is_dir() or not name.startswith("v") or not name[1:].isdigit():
            continue
        number = int(name[1:])
        script = version_dir / "scripts" / f"risk_labeler_v{number}.py"
        if script.exists():
            versions.append(number)
    return max(versions, default=1)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是大于等于 1 的整数")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="连续执行风险规则迭代实验")
    parser.add_argument("--start-version", type=positive_int, help="起始版本号，例如 2 表示从 v2 开始")
    parser.add_argument("--iterations", type=positive_int, help="计划迭代次数")
    parser.add_argument(
        "--promotion-mode",
        choices=["strict", "relaxed"],
        default="relaxed",
        help="晋级策略；当前实验默认 relaxed，严格模式用 strict",
    )
    return parser.parse_args()


def prompt_if_missing(args: argparse.Namespace) -> tuple[int, int]:
    latest = discover_latest_version()
    if args.start_version is None:
        raw_start = input(f"请输入起始版本号（默认 v{latest}）：").strip()
        start_version = positive_int(raw_start) if raw_start else latest
    else:
        start_version = args.start_version

    if args.iterations is None:
        raw_iterations = input("请输入迭代次数（默认 1）：").strip()
        iterations = positive_int(raw_iterations) if raw_iterations else 1
    else:
        iterations = args.iterations
    return start_version, iterations


def save_batch_report(report: dict) -> Path:
    output_dir = EXPERIMENT_ROOT / "batch_runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"batch_{timestamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def run_iterations(
    start_version: int,
    iterations: int,
    promotion_mode: str = "relaxed",
) -> dict:
    from orchestrator import (
        ENABLE_PATCHER,
        GOLD_CSV,
        INPUT_CSV,
        MODEL,
        PATCH_MODEL,
        PATCH_SOURCE_ANALYSIS_JSON,
        PATCH_TEMPERATURE,
        PYTHON_EXECUTABLE,
        SAMPLE_ROWS,
        TEXT_LIMIT,
        TIMEOUT,
        orchestrate_pipeline,
    )

    report = {
        "experiment_root": str(EXPERIMENT_ROOT),
        "versions_root": str(VERSIONS_ROOT),
        "start_version": start_version,
        "requested_iterations": iterations,
        "promotion_mode": promotion_mode,
        "attempted_iterations": 0,
        "accepted_iterations": 0,
        "total_patch_attempts": 0,
        "stop_reason": "completed",
        "results": [],
    }

    for current_number in range(start_version, start_version + iterations):
        next_number = current_number + 1
        print("\n" + "=" * 80)
        print(f"批量迭代：v{current_number} -> v{next_number}")
        print("=" * 80)

        result = orchestrate_pipeline(
            current_version=current_number,
            next_version=next_number,
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
            relaxed_promotion=promotion_mode == "relaxed",
        )

        report["attempted_iterations"] += 1
        report["total_patch_attempts"] += int(getattr(result, "patch_attempts", 1) or 1)
        report["results"].append(asdict(result))

        print(f"success            : {result.success}")
        print(f"candidate_accepted : {result.candidate_accepted}")
        print(f"promotion_decision : {result.promotion_decision}")
        print(f"compare_winner     : {result.compare_winner}")
        print(f"patch_attempts     : {getattr(result, 'patch_attempts', 1)}")
        if result.error_message:
            print(f"error_message      : {result.error_message}")

        if not result.success:
            report["stop_reason"] = f"pipeline_failed_at_v{current_number}_to_v{next_number}"
            break

        if ENABLE_PATCHER and not result.candidate_accepted:
            report["stop_reason"] = f"candidate_rejected_at_v{current_number}_to_v{next_number}"
            break

        report["accepted_iterations"] += 1

    return report


def main() -> None:
    configure_environment()
    args = parse_args()
    start_version, iterations = prompt_if_missing(args)
    report = run_iterations(
        start_version,
        iterations,
        promotion_mode=args.promotion_mode,
    )
    report_path = save_batch_report(report)

    print("\n" + "=" * 80)
    print("批量迭代结束")
    print(f"计划轮数 : {report['requested_iterations']}")
    print(f"尝试轮数 : {report['attempted_iterations']}")
    print(f"晋级轮数 : {report['accepted_iterations']}")
    print(f"补丁尝试 : {report['total_patch_attempts']}")
    print(f"晋级策略 : {report['promotion_mode']}")
    print(f"停止原因 : {report['stop_reason']}")
    print(f"批次报告 : {report_path}")


if __name__ == "__main__":
    main()
