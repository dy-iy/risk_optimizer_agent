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
MAX_EXAMPLES_PER_GROUP = 30
FEEDBACK_SEED = 42
LOW_COVERAGE_THRESHOLD = 0.8
DEFAULT_ACCEPTANCE_EPSILON = WorkflowConfig.acceptance_epsilon


def run_iterations(folder, current_version, rounds, *, acceptance_epsilon=DEFAULT_ACCEPTANCE_EPSILON):
    return run_workflow(folder, current_version, rounds, config=WorkflowConfig(
        max_lf_changes=MAX_LF_CHANGES,
        max_examples_per_group=MAX_EXAMPLES_PER_GROUP,
        feedback_seed=FEEDBACK_SEED,
        low_coverage_threshold=LOW_COVERAGE_THRESHOLD,
        acceptance_epsilon=acceptance_epsilon,
    ))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="起始版本；不填写时交互询问")
    parser.add_argument("--iterations", type=int, help="追加轮数；0 表示只评价；不填写时交互询问")
    parser.add_argument("--acceptance-epsilon", type=float, default=DEFAULT_ACCEPTANCE_EPSILON,
                        help="验收要求验证集 ΔQ 严格大于此值（默认 %(default)s）")
    args = parser.parse_args()
    try:
        current_version = args.version if args.version is not None else input("当前版本（例如 v1）：").strip()
        rounds = args.iterations if args.iterations is not None else int(input("本次追加迭代轮数（0 表示只评价）：").strip())
        run_iterations(Path(__file__).resolve().parent, current_version, rounds,
                       acceptance_epsilon=args.acceptance_epsilon)
    except (EOFError, KeyboardInterrupt):
        print("\n已停止。", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\n停止：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
