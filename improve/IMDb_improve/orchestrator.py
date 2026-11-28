"""命令行入口：运行 LangGraph LF 迭代工作流。"""

import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True

if __package__:
    from .workflow import WorkflowConfig, run_iterations as run_workflow
else:
    from workflow import WorkflowConfig, run_iterations as run_workflow


# 常用参数集中在这里，命令行入口与直接调用 run_iterations 均使用这些值。
MAX_LF_CHANGES = 5
# 样例基数 n：add 两组各最多 2n；modify 三组各最多 n。
MAX_EXAMPLES_PER_GROUP = 30
FEEDBACK_SEED = 42
# 判定参数：C = min(训练简单投票输出覆盖率, 验证简单投票输出覆盖率)。
# Q = 验证集已输出样本的准确率；
# C < 0.80 为低覆盖，Q >= 0.90 为高准确率。
LOW_COVERAGE_THRESHOLD = 0.80
HIGH_COVERED_ACCURACY_THRESHOLD = 0.80
# C 低、Q 高：仅 add，扩大正确覆盖。
# C 低、Q 低：仅 modify，先修高影响误标。
# C 高、Q 低：仅 modify，减少误标。
# C 高、Q 高：add 或 modify，处理剩余缺口。


def run_iterations(folder, current_version, rounds):
    return run_workflow(folder, current_version, rounds, config=WorkflowConfig(
        max_lf_changes=MAX_LF_CHANGES,
        max_examples_per_group=MAX_EXAMPLES_PER_GROUP,
        feedback_seed=FEEDBACK_SEED,
        low_coverage_threshold=LOW_COVERAGE_THRESHOLD,
        high_covered_accuracy_threshold=HIGH_COVERED_ACCURACY_THRESHOLD,
    ))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="起始版本；不填写时交互询问")
    parser.add_argument("--iterations", type=int, help="追加轮数；0 表示只评价；不填写时交互询问")
    args = parser.parse_args()
    try:
        current_version = args.version if args.version is not None else input("当前版本（例如 v1）：").strip()
        rounds = args.iterations if args.iterations is not None else int(input("本次追加迭代轮数（0 表示只评价）：").strip())
        run_iterations(Path(__file__).resolve().parent, current_version, rounds)
    except (EOFError, KeyboardInterrupt):
        print("\n已停止。", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\n停止：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
