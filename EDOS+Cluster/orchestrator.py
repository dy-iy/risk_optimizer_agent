"""命令行入口：运行 LangGraph LF 迭代工作流。"""

import argparse
from dataclasses import replace
from pathlib import Path
import sys

sys.dont_write_bytecode = True

if __package__:
    from .workflow import WorkflowConfig, run_iterations as run_workflow
else:
    from workflow import WorkflowConfig, run_iterations as run_workflow


# 常用参数集中在这里，命令行入口与直接调用 run_iterations 均使用这些值。
MAX_LF_CHANGES = 5
# modify 三组各最多 n；add 每轮一个案例组、一条 LF。
MAX_EXAMPLES_PER_GROUP = 30
FEEDBACK_SEED = 42
# 判定参数：C = min(训练简单投票输出覆盖率, 验证简单投票输出覆盖率)。
# Q = 验证集已输出样本的准确率；
# C < 0.80 为低覆盖，Q >= 0.90 为高准确率。
LOW_COVERAGE_THRESHOLD = 0.80
HIGH_COVERED_ACCURACY_THRESHOLD = 0.90
FEEDBACK_MODE = "cluster_diverse"
SELECTION_FRACTION = 0.25
CANDIDATE_COUNT = 1
FINAL_TEST = True
EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"
EMBEDDING_REVISION = "e8c3b32edf5434bc2275fc9bab85f82640a19130"
EMBEDDING_DEVICE = "auto"
EMBEDDING_BATCH_SIZE = 64
EMBEDDING_MAX_SEQ_LENGTH = 384
# 发现集诊断：0.35 只有 1 个 >=4 的簇；0.55 得到 31 个，簇中心凝聚度中位数约 0.78–0.80。
CLUSTER_DISTANCE_THRESHOLD = 0.55
MIN_CLUSTER_SIZE = 4
TRAIN_SIMILARITY_THRESHOLD = 0.65
CENTER_EXAMPLES = 2
DIVERSE_EXAMPLES = 3
BOUNDARY_EXAMPLES = 1
HARD_NEGATIVE_EXAMPLES = 3
TRAIN_SUPPORT_EXAMPLES = 3
ACCEPTANCE_ENABLED = True
MIN_NET_CORRECT_GAIN = 1
MAX_NEWLY_WRONG = 2
MAX_REGRESSED = 0
MIN_MACRO_F1_DELTA = 0.0
MAX_COVERED_ACCURACY_DROP = 0.005
MIN_ADD_TRAIN_OUTPUT_GAIN = 1
# C 低、Q 高：仅 add，扩大正确覆盖。
# C 低、Q 低：仅 modify，先修高影响误标。
# C 高、Q 低：仅 modify，减少误标。
# C 高、Q 高：add 或 modify，处理剩余缺口。


def default_config():
    if __package__:
        from .tools.acceptance import AcceptanceConfig
        from .tools.clustering import ClusterConfig
        from .tools.embeddings import EmbeddingConfig
    else:
        from tools.acceptance import AcceptanceConfig
        from tools.clustering import ClusterConfig
        from tools.embeddings import EmbeddingConfig
    return WorkflowConfig(
        max_lf_changes=MAX_LF_CHANGES,
        max_examples_per_group=MAX_EXAMPLES_PER_GROUP,
        feedback_seed=FEEDBACK_SEED,
        low_coverage_threshold=LOW_COVERAGE_THRESHOLD,
        high_covered_accuracy_threshold=HIGH_COVERED_ACCURACY_THRESHOLD,
        embeddings=EmbeddingConfig(EMBEDDING_MODEL, EMBEDDING_REVISION, EMBEDDING_DEVICE,
                                   EMBEDDING_BATCH_SIZE, EMBEDDING_MAX_SEQ_LENGTH, False),
        clusters=ClusterConfig(FEEDBACK_MODE, CLUSTER_DISTANCE_THRESHOLD, MIN_CLUSTER_SIZE,
                               TRAIN_SIMILARITY_THRESHOLD, CENTER_EXAMPLES, DIVERSE_EXAMPLES,
                               BOUNDARY_EXAMPLES, HARD_NEGATIVE_EXAMPLES, TRAIN_SUPPORT_EXAMPLES),
        acceptance=AcceptanceConfig(ACCEPTANCE_ENABLED, MIN_NET_CORRECT_GAIN, MAX_NEWLY_WRONG,
                                     MAX_REGRESSED, MIN_MACRO_F1_DELTA, MAX_COVERED_ACCURACY_DROP,
                                     MIN_ADD_TRAIN_OUTPUT_GAIN),
        candidate_count=CANDIDATE_COUNT, selection_fraction=SELECTION_FRACTION, final_test=FINAL_TEST,
    )


def run_iterations(folder, current_version, rounds, *, config=None, run_dir=None):
    return run_workflow(folder, current_version, rounds, config=config or default_config(), run_dir=run_dir)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="起始版本；不填写时交互询问")
    parser.add_argument("--iterations", type=int, help="追加轮数；0 表示只评价；不填写时交互询问")
    parser.add_argument("--mode", choices=("random", "cluster_random", "cluster_diverse"), default=FEEDBACK_MODE)
    parser.add_argument("--candidates", type=int, choices=(1, 2, 3), default=CANDIDATE_COUNT)
    parser.add_argument("--run-dir", type=Path, help="新的实验输出目录；已存在时拒绝覆盖")
    parser.add_argument("--seed", type=int, default=FEEDBACK_SEED)
    parser.add_argument("--selection-fraction", type=float, default=SELECTION_FRACTION)
    parser.add_argument("--acceptance", choices=("on", "off"), default="on" if ACCEPTANCE_ENABLED else "off")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default=EMBEDDING_DEVICE)
    parser.add_argument("--local-models-only", action="store_true", help="只使用已缓存的固定模型")
    parser.add_argument("--skip-final-test", action="store_true", help="消融过程中暂缓最终测试")
    args = parser.parse_args()
    try:
        current_version = args.version if args.version is not None else input("当前版本（例如 v1）：").strip()
        rounds = args.iterations if args.iterations is not None else int(input("本次追加迭代轮数（0 表示只评价）：").strip())
        defaults = default_config()
        config = replace(defaults, feedback_seed=args.seed, candidate_count=args.candidates,
                         selection_fraction=args.selection_fraction, final_test=not args.skip_final_test,
                         clusters=replace(defaults.clusters, mode=args.mode),
                         embeddings=replace(defaults.embeddings, device=args.device, local_files_only=args.local_models_only),
                         acceptance=replace(defaults.acceptance, enabled=args.acceptance == "on"))
        run_iterations(Path(__file__).resolve().parent, current_version, rounds, config=config, run_dir=args.run_dir)
    except (EOFError, KeyboardInterrupt):
        print("\n已停止。", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\n停止：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
