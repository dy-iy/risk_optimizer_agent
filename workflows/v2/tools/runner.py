from __future__ import annotations

import os
import sys
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
    from .common import ensure_parent_dir, write_json_file
    from .paths import resolve_project_root, resolve_versions_root
except ImportError:
    from common import ensure_parent_dir, write_json_file
    from paths import resolve_project_root, resolve_versions_root


@dataclass
class RunResult:
    success: bool
    script_path: str
    input_csv: str
    output_csv: str
    return_code: int
    stdout: str
    stderr: str
    error_message: str = ""


def run_rule_script(
    script_path: str | Path,
    input_csv: str | Path,
    output_csv: str | Path,
    python_executable: Optional[str] = None,
    timeout: int = 300,
) -> RunResult:
    """
    运行规则脚本，并通过环境变量传入：
    - CSV_PATH: 输入新闻 csv
    - OUT_PATH: 输出预测 csv

    参数:
        script_path: 规则脚本路径，例如 scripts/risk_labeler_v1.py
        input_csv: 输入新闻 csv 路径
        output_csv: 输出预测 csv 路径
        python_executable: Python 解释器路径，默认使用当前解释器
        timeout: 超时时间（秒）

    返回:
        RunResult
    """
    script_path = Path(script_path).resolve()
    input_csv = Path(input_csv).resolve()
    output_csv = Path(output_csv).resolve()

    if python_executable is None:
        python_executable = sys.executable

    if not script_path.exists():
        return RunResult(
            success=False,
            script_path=str(script_path),
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            return_code=-1,
            stdout="",
            stderr="",
            error_message=f"规则脚本不存在: {script_path}",
        )

    if not input_csv.exists():
        return RunResult(
            success=False,
            script_path=str(script_path),
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            return_code=-1,
            stdout="",
            stderr="",
            error_message=f"输入 CSV 不存在: {input_csv}",
        )

    ensure_parent_dir(output_csv)

    env = os.environ.copy()
    env["CSV_PATH"] = str(input_csv)
    env["OUT_PATH"] = str(output_csv)

    try:
        completed = subprocess.run(
            [python_executable, str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            success=False,
            script_path=str(script_path),
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            return_code=-2,
            stdout=e.stdout or "",
            stderr=e.stderr or "",
            error_message=f"脚本运行超时（>{timeout}s）: {script_path}",
        )
    except Exception as e:
        return RunResult(
            success=False,
            script_path=str(script_path),
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            return_code=-3,
            stdout="",
            stderr="",
            error_message=f"运行脚本时发生异常: {e}",
        )

    # 先看进程退出码
    if completed.returncode != 0:
        return RunResult(
            success=False,
            script_path=str(script_path),
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error_message="脚本返回非 0 退出码",
        )

    # 再看输出文件是否真的生成
    if not output_csv.exists():
        return RunResult(
            success=False,
            script_path=str(script_path),
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error_message="脚本运行成功，但未找到输出文件",
        )

    return RunResult(
        success=True,
        script_path=str(script_path),
        input_csv=str(input_csv),
        output_csv=str(output_csv),
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        error_message="",
    )


def save_run_result(result: RunResult, save_path: str | Path) -> None:
    """
    把运行结果保存成 json，方便后续排查日志。
    """
    save_path = Path(save_path).resolve()
    write_json_file(save_path, asdict(result))


if __name__ == "__main__":
    # 这里先给你一个可直接测试的入口
    project_root = resolve_project_root()

    version_dir = resolve_versions_root(project_root) / "v1"
    script_path = version_dir / "scripts" / "risk_labeler_v1.py"
    input_csv = project_root / "data" / "input" / "raw_1000_news.csv"
    output_csv = version_dir / "reports" / "predictions" / "risk_labeler_v1_output.csv"
    log_json = version_dir / "reports" / "predictions" / "risk_labeler_v1_runlog.json"

    result = run_rule_script(
        script_path=script_path,
        input_csv=input_csv,
        output_csv=output_csv,
    )

    save_run_result(result, log_json)

    print("=" * 60)
    print("RUN RESULT")
    print("=" * 60)
    print(f"success      : {result.success}")
    print(f"script_path  : {result.script_path}")
    print(f"input_csv    : {result.input_csv}")
    print(f"output_csv   : {result.output_csv}")
    print(f"return_code  : {result.return_code}")

    if result.error_message:
        print(f"error_message: {result.error_message}")

    print("\n--- STDOUT ---")
    print(result.stdout.strip() if result.stdout else "(empty)")

    print("\n--- STDERR ---")
    print(result.stderr.strip() if result.stderr else "(empty)")
