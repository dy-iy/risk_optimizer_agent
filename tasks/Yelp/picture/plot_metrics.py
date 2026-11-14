"""读取各版本原始结果，用 Matplotlib 直接绘图并保存 PNG、SVG 到本目录。"""

import csv
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, PercentFormatter
import numpy as np


FOLDER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FOLDER))
from tools.data import split_identity
from tools.task import CLASS_NAMES, CLASS_TITLES, DATASET
COLORS = {"valid": "#2563EB", "test": "#0D9488", "train": "#D97706"}
NAMES = {"valid": "验证集（开发反馈）", "test": "独立测试集", "train": "训练集"}


def setup_style():
    fonts = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        if name in fonts:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams.update({
        "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.labelcolor": "#475569", "text.color": "#0F172A",
        "xtick.color": "#64748B", "ytick.color": "#64748B",
        "axes.edgecolor": "#CBD5E1", "axes.spines.top": False,
        "axes.spines.right": False, "axes.unicode_minus": False,
        "figure.facecolor": "white", "axes.facecolor": "white",
        "savefig.facecolor": "white", "svg.fonttype": "path",
        "legend.frameon": False, "lines.linewidth": 2.2,
    })


def load_versions():
    manifest = FOLDER / "data/split.json"
    if not manifest.is_file():
        raise ValueError("尚未导入官方数据划分，不能绘制新实验指标")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    expected = split_identity(metadata)
    if expected["dataset"] != DATASET:
        raise ValueError("数据清单与当前任务不一致")
    versions = []
    for path in (FOLDER / "versions").iterdir():
        if not re.fullmatch(r"v[1-9]\d*", path.name) or not (path / "results/metrics.json").is_file():
            continue
        metrics = json.loads((path / "results/metrics.json").read_text(encoding="utf-8"))
        if metrics.get("data_split") != expected:
            continue
        if metrics["lf_version"] != path.name:
            raise ValueError(f"{path.name} 的 metrics.json 版本名不一致")
        versions.append((path, metrics))
    versions.sort(key=lambda item: int(item[0].name[1:]))
    if not versions:
        raise ValueError("没有找到可绘图的版本结果")
    return versions


def values(versions, *keys):
    result = []
    for _, metrics in versions:
        value = metrics
        for key in keys:
            value = value[key]
        result.append(np.nan if value is None else value)
    return np.asarray(result)


def version_axis(ax, versions, percent=True):
    numbers = [int(path.name[1:]) for path, _ in versions]
    ax.set_xticks(numbers, [path.name for path, _ in versions])
    ax.set_xlim(min(numbers) - .45, max(numbers) + .65)
    ax.grid(axis="y", color="#E2E8F0", linewidth=.8)
    ax.set_axisbelow(True)
    if percent:
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.set_ylim(0, 1.04)
        ax.set_yticks(np.arange(0, 1.01, .2))


def heading(fig, title, subtitle):
    fig.suptitle(title, x=.06, y=.98, ha="left", fontsize=20, fontweight="bold")
    fig.text(.06, .93, subtitle, fontsize=10.5, color="#64748B")


def sample_note(versions):
    return " ｜ ".join(
        f"{NAMES[split]} {versions[0][1][split]['n_samples']:,} 条"
        for split in ("valid", "test", "train")
    )


def save(fig, output, name, note):
    fig.text(.06, .025, note, fontsize=9, color="#64748B")
    for extension in ("png", "svg"):
        fig.savefig(output / f"{name}.{extension}", dpi=180)
    plt.close(fig)
    print(output / f"{name}.png")


def overview(versions, output):
    fig, axes = plt.subplots(2, 2, figsize=(14.5, 9.5))
    heading(fig, f"{DATASET} · {versions[0][0].name}—{versions[-1][0].name} 指标总览", sample_note(versions))
    fig.subplots_adjust(left=.07, right=.975, top=.85, bottom=.11, hspace=.43, wspace=.19)
    x = [int(path.name[1:]) for path, _ in versions]
    panels = [
        ("Macro-F1（全量，含弃权）", ("all_samples", "macro_f1")),
        ("LF 覆盖率（至少一个 LF 投票）", ("lf_coverage",)),
        ("准确率（全量，含弃权）", ("all_samples", "accuracy")),
        ("已覆盖准确率（纵轴放大）", ("covered_only", "accuracy")),
    ]
    for ax, (title, keys) in zip(axes.flat, panels):
        version_axis(ax, versions)
        ax.set_title(title, loc="left", pad=12)
        splits = ("valid", "test", "train") if keys == ("lf_coverage",) else ("valid", "test")
        for split in splits:
            y = values(versions, split, *keys)
            ax.plot(x, y, marker="o", markersize=5, color=COLORS[split], label=NAMES[split])
        ax.legend(loc="lower right" if keys == ("covered_only", "accuracy") else "upper left", fontsize=9)
    # 已覆盖准确率的变化较小，明确放大纵轴以显示变化。
    low = max(0, np.floor(min(np.nanmin(values(versions, split, "covered_only", "accuracy"))
                              for split in ("valid", "test")) * 10) / 10)
    axes[1, 1].set_ylim(low, 1.015)
    axes[1, 1].set_yticks(np.linspace(low, 1, 6))
    save(fig, output, "01_overview",
         "全量指标包含弃权；已覆盖准确率仅统计非弃权样本。沿用冻结的本地来源划分；测试集只用于报告。")


def class_metrics(versions, output):
    fig, axes = plt.subplots(2, 3, figsize=(17, 9.5))
    heading(fig, f"{DATASET} · 分类别指标", "上排：验证集（开发反馈） ｜ 下排：独立测试集 ｜ 全量口径，含弃权")
    fig.subplots_adjust(left=.06, right=.98, top=.83, bottom=.11, hspace=.43, wspace=.2)
    x = [int(path.name[1:]) for path, _ in versions]
    for row, split in enumerate(("valid", "test")):
        for col, (metric, title) in enumerate((("precision", "Precision · 精确率"),
                                              ("recall", "Recall · 召回率"), ("f1", "F1"))):
            ax = axes[row, col]
            version_axis(ax, versions)
            ax.set_title(f"{NAMES[split]} · {title}", loc="left", pad=12)
            for label, name, color in zip(CLASS_NAMES, CLASS_TITLES, ("#2563EB", "#E11D48", "#D97706", "#7C3AED")):
                ax.plot(x, values(versions, split, "all_samples", label, metric), marker="o",
                        markersize=4.5, color=color, label=name)
            ax.legend(fontsize=9, loc="lower right" if metric == "precision" else "upper left")
    save(fig, output, "02_class_metrics",
         "弃权计入真实类别的漏判；Precision 无预测样本时按现有评价代码记为 0。各图纵轴均为 0%—100%。")


def prediction_outcomes(versions, output):
    fig, axes = plt.subplots(2, 1, figsize=(14.5, 10))
    heading(fig, "预测结果构成 · 正确 / 错误 / 弃权", "每根柱子代表该版本的全部样本；红色文字标出错误条数")
    fig.subplots_adjust(left=.07, right=.975, top=.84, bottom=.1, hspace=.4)
    x = [int(path.name[1:]) for path, _ in versions]
    for ax, split in zip(axes, ("valid", "test")):
        correct = values(versions, split, "correct")
        wrong = values(versions, split, "incorrect_labeled")
        abstained = values(versions, split, "n_abstained")
        total = correct + wrong + abstained
        version_axis(ax, versions, percent=False)
        ax.bar(x, correct, width=.65, color="#0D9488", label="正确")
        ax.bar(x, wrong, width=.65, bottom=correct, color="#E11D48", label="错误")
        ax.bar(x, abstained, width=.65, bottom=correct + wrong, color="#CBD5E1", label="弃权")
        ax.set_title(f"{NAMES[split]} · {int(total[0]):,} 条", loc="left", pad=12)
        ax.set_ylabel("样本数（条）")
        ax.set_ylim(0, max(total) * 1.1)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.legend(ncol=3, loc="upper right", bbox_to_anchor=(1, 1.13))
        for i, good, bad, missing in zip(x, correct, wrong, abstained):
            ax.text(i, good / 2, f"{int(good)}", color="white", ha="center", va="center", fontsize=9)
            ax.text(i, good + bad + missing / 2, f"{int(missing)}", color="#475569", ha="center", va="center", fontsize=9)
            if bad:
                ax.text(i, good + bad + max(total) * .025, f"错 {int(bad)}", color="#BE123C",
                        ha="center", va="bottom", fontsize=9, fontweight="bold")
    save(fig, output, "03_prediction_outcomes", "正确 + 错误 + 弃权 = 样本总数。上下两图样本规模不同，纵轴分别按条数显示。")


def iteration_deltas(versions, output):
    if len(versions) < 2:
        return
    fig, axes = plt.subplots(2, 1, figsize=(14.5, 9))
    heading(fig, "每轮变化 · 相对上一版本", "柱子高于零表示增加，低于零表示减少；单位为百分点（pp）")
    fig.subplots_adjust(left=.07, right=.975, top=.83, bottom=.12, hspace=.5)
    x = np.arange(len(versions) - 1)
    labels = [f"{versions[i-1][0].name}→{versions[i][0].name}" for i in range(1, len(versions))]
    for ax, split in zip(axes, ("valid", "test")):
        f1_delta = np.diff(values(versions, split, "all_samples", "macro_f1")) * 100
        coverage_delta = np.diff(values(versions, split, "coverage")) * 100
        ax.bar(x - .19, f1_delta, width=.36, color="#2563EB", label="全量 Macro-F1 变化")
        ax.bar(x + .19, coverage_delta, width=.36, color="#D97706", label="输出覆盖率变化")
        ax.axhline(0, color="#475569", linewidth=1)
        ax.set_xticks(x, labels, rotation=20, ha="right")
        ax.grid(axis="y", color="#E2E8F0", linewidth=.8)
        ax.set_axisbelow(True)
        ax.set_ylabel("变化（百分点）")
        ax.set_title(NAMES[split], loc="left", pad=12)
        ax.legend(loc="upper right", fontsize=9)
        for offset, series in ((-.19, f1_delta), (.19, coverage_delta)):
            for i, value in zip(x, series):
                ax.annotate(f"{value:+.1f}", (i + offset, value), xytext=(0, 4 if value >= 0 else -4),
                            textcoords="offset points", ha="center", va="bottom" if value >= 0 else "top", fontsize=8.5)
        ax.margins(y=.22)
    save(fig, output, "04_iteration_deltas", "例如 +5 pp 表示从 30% 增至 35%，不是相对增长 5%。")


def aggregation_direction(versions, output):
    """统计所有类别的一致票与聚合结果的分歧，不读取金标。"""
    counts = {split: [] for split in NAMES}
    denominators = {split: [] for split in NAMES}
    for path, _ in versions:
        with (path / "results/weak_labels.csv").open(encoding="utf-8-sig", newline="") as stream:
            predictions = {(row["split"], row["id"]): row["weak_label"] for row in csv.DictReader(stream)}
        current = {split: [0, 0] for split in NAMES}
        with (path / "results/lf_votes.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                votes = {int(value) for key, value in row.items() if key not in ("split", "id")} - {-1}
                prediction = predictions[(row["split"], row["id"])]
                if len(votes) == 1:
                    current[row["split"]][1] += 1
                    current[row["split"]][0] += prediction != "abstain" and prediction != str(next(iter(votes)))
        for split in NAMES:
            counts[split].append(current[split][0])
            denominators[split].append(current[split][1])
    fig, axes = plt.subplots(3, 1, figsize=(14.5, 11))
    heading(fig, "投票与聚合方向不一致", "非弃权票一致，但最终输出另一类别；统计全部任务类别，不将聚合弃权计作反向输出")
    fig.subplots_adjust(left=.07, right=.975, top=.85, bottom=.1, hspace=.48)
    x = [int(path.name[1:]) for path, _ in versions]
    for ax, split in zip(axes, ("valid", "test", "train")):
        version_axis(ax, versions, percent=False)
        ax.bar(x, counts[split], width=.62, color=COLORS[split])
        ax.set_title(f"{NAMES[split]} · 共 {versions[0][1][split]['n_samples']:,} 条", loc="left", pad=12)
        ax.set_ylabel("不一致条数")
        top = max(counts[split])
        ax.set_ylim(0, max(1, top) * 1.35)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        for i, count, denominator in zip(x, counts[split], denominators[split]):
            ax.text(i, count + max(1, top) * .045, f"{count}/{denominator}", ha="center", fontsize=9)
    save(fig, output, "05_aggregation_direction", "分子为不一致数，分母为一致投票样本数。方向分歧不等于已知错误；训练集无金标。")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    setup_style()
    versions = load_versions()
    output = Path(__file__).resolve().parent
    output.mkdir(exist_ok=True)
    for draw in (overview, class_metrics, prediction_outcomes, iteration_deltas, aggregation_direction):
        draw(versions, output)
    print(f"已绘制 {len(versions)} 个版本；PNG 用于查看，SVG 用于无损放大。")


if __name__ == "__main__":
    main()
