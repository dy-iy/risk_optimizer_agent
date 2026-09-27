"""Run V4 analysis and patch iterations."""

import re
import sys
from pathlib import Path

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflows.v4.analyzer import analyze
from workflows.v4.patcher import patch
from workflows.v4.tools.comparator import compare_versions
from workflows.v4.tools.runner import run_version


# ---------- 全局配置 ----------
START_VERSION = "v1"
ITERATIONS = 1
VERSION_ATTEMPTS = 2


def normalize_version(version: str) -> str:
    version = version.strip().lower()
    if version.isdigit():
        version = f"v{version}"
    if not re.fullmatch(r"v\d+", version):
        raise ValueError("版本必须是 v1、v2 等格式")
    return version


def next_version(version: str) -> str:
    return f"v{int(version[1:]) + 1}"


def orchestrate(start_version: str, iterations: int) -> str:
    if iterations < 1:
        raise ValueError("迭代轮数必须大于 0")

    current = normalize_version(start_version)
    steps_per_iteration = 1 + VERSION_ATTEMPTS * 3
    progress = tqdm(total=1 + iterations * steps_per_iteration, desc="V4 自动迭代", unit="步")

    run_version(current, show_metrics=False, show_progress=False)
    progress.set_postfix_str(f"{current} 运行完成")
    progress.update(1)

    for _ in range(iterations):
        target = next_version(current)

        analyze(current)
        progress.set_postfix_str(f"{current} 分析完成")
        progress.update(1)

        feedback = None
        accepted = False
        for attempt in range(1, VERSION_ATTEMPTS + 1):
            patch(current, target, version_feedback=feedback, version_attempt=attempt)
            progress.set_postfix_str(f"{target} 脚本完成，第 {attempt} 次")
            progress.update(1)

            run_version(target, show_metrics=False, show_progress=False)
            progress.set_postfix_str(f"{target} 运行完成，第 {attempt} 次")
            progress.update(1)

            report = compare_versions(current, target)
            progress.set_postfix_str(f"{current} vs {target} 对比完成")
            progress.update(1)

            if report[f"是否接受_{target}"]:
                accepted = True
                skipped_steps = (VERSION_ATTEMPTS - attempt) * 3
                progress.update(skipped_steps)
                current = target
                break
            feedback = report

        if not accepted:
            tqdm.write(f"{target} 连续 {VERSION_ATTEMPTS} 次未通过质量门槛，保留 {current} 并停止。")
            break

    progress.close()
    print(f"迭代完成，最新版本：{current}")
    return current


def main() -> None:
    version = input(f"当前版本 [{START_VERSION}]: ").strip() or START_VERSION
    raw_iterations = input(f"迭代轮数 [{ITERATIONS}]: ").strip()
    iterations = int(raw_iterations) if raw_iterations else ITERATIONS
    orchestrate(version, iterations)


if __name__ == "__main__":
    main()
