"""Summarize test results and render comparison and iteration charts."""

import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

sys.dont_write_bytecode = True

OUTPUT = Path(__file__).resolve().parent
ROOT = OUTPUT.parent
DATASETS = (
    ("SMS", "SMS"),
    ("YouTube", "YouTube"),
    ("Finnsp", "FinnSP"),
    ("financial_phrasebank", "Financial PhraseBank"),
)


def load_results():
    rows = []
    for folder, display_name in DATASETS:
        versions = sorted(
            (p for p in (ROOT / folder / "versions").iterdir()
             if p.is_dir() and re.fullmatch(r"v[1-9]\d*", p.name)
             and (p / "results/metrics.json").is_file()),
            key=lambda p: int(p.name[1:]),
        )
        if not versions:
            raise ValueError(f"No evaluated versions found for {folder}")
        row = {"dataset": display_name}
        for prefix, version in (("first", versions[0]), ("latest", versions[-1])):
            # Only completed evaluations enter the chart; omissions are listed in README.
            path = version / "results" / "metrics.json"
            metrics = json.loads(path.read_text(encoding="utf-8-sig"))
            if metrics["lf_version"] != version.name:
                raise ValueError(f"Version mismatch: {path}")
            test = metrics["test"]
            n, correct = test["n_samples"], test["correct"]
            accuracy = test["all_samples"]["accuracy"]
            if n <= 0 or not 0 <= correct <= n:
                raise ValueError(f"Invalid sample counts: {path}")
            if not math.isclose(accuracy, correct / n, abs_tol=1e-12):
                raise ValueError(f"Global accuracy does not match correct / N: {path}")
            row.update({
                f"{prefix}_version": version.name,
                f"{prefix}_global_acc": accuracy,
                f"{prefix}_correct": correct,
                f"{prefix}_n_samples": n,
                f"{prefix}_source": path.relative_to(ROOT).as_posix(),
            })
        if row["first_n_samples"] != row["latest_n_samples"]:
            raise ValueError(f"Test sample counts changed for {folder}")
        row["improvement_pp"] = (
            row["latest_global_acc"] - row["first_global_acc"]
        ) * 100
        rows.append(row)
    return rows


def load_iterations():
    rows = []
    for folder, display_name in DATASETS:
        versions = sorted(
            (p for p in (ROOT / folder / "versions").iterdir()
             if p.is_dir() and re.fullmatch(r"v[1-9]\d*", p.name)
             and (p / "results/metrics.json").is_file()),
            key=lambda p: int(p.name[1:]),
        )
        sample_count = None
        for version in versions:
            path = version / "results" / "metrics.json"
            metrics = json.loads(path.read_text(encoding="utf-8-sig"))
            test = metrics["test"]
            n, correct = test["n_samples"], test["correct"]
            accuracy = test["all_samples"]["accuracy"]
            if metrics["lf_version"] != version.name:
                raise ValueError(f"Version mismatch: {path}")
            if n <= 0 or not 0 <= correct <= n:
                raise ValueError(f"Invalid sample counts: {path}")
            if not math.isclose(accuracy, correct / n, abs_tol=1e-12):
                raise ValueError(f"Global accuracy does not match correct / N: {path}")
            if sample_count is not None and sample_count != n:
                raise ValueError(f"Test sample counts changed: {path}")
            sample_count = n
            rows.append({
                "dataset": display_name, "version": version.name,
                "iteration": int(version.name[1:]), "global_acc": accuracy,
                "correct": correct, "n_samples": n,
                "source": path.relative_to(ROOT).as_posix(),
            })
    return rows


def render_iterations(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    colors = ("#3769A8", "#0D9488", "#8864B4", "#DA8540")
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.5), sharey=True)
    fig.subplots_adjust(left=.075, right=.97, top=.80, bottom=.13,
                        wspace=.17, hspace=.47)
    fig.text(.055, .947, "Global accuracy through iterations", fontsize=25,
             fontweight="bold", color="#17283E")
    fig.text(.055, .900,
             "Independent test set  /  One panel per dataset  /  Observed version results",
             fontsize=12, color="#63748A")
    for ax, (_, name), color in zip(axes.flat, DATASETS, colors):
        series = [row for row in rows if row["dataset"] == name]
        x = [row["iteration"] for row in series]
        y = [row["global_acc"] * 100 for row in series]
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#E7EDF4", linewidth=.9)
        ax.fill_between(x, y, color=color, alpha=.07, zorder=1)
        ax.plot(x, y, color=color, linewidth=2.4, marker="o",
                markersize=4, markeredgecolor="white", markeredgewidth=.65,
                zorder=3)
        ax.scatter([x[0], x[-1]], [y[0], y[-1]], s=64, color=color,
                   edgecolor="white", linewidth=1.7, zorder=4)
        for index, offset, align in ((0, (7, 27), "left"), (-1, (-6, 12), "right")):
            ax.annotate(f"{y[index]:.2f}%", (x[index], y[index]),
                        xytext=offset, textcoords="offset points", ha=align,
                        fontsize=11, fontweight="bold", color=color,
                        bbox=dict(boxstyle="round,pad=.2", facecolor="white",
                                  edgecolor="none", alpha=.92), zorder=5)
        ax.set_title(f"{name}  |  {series[0]['version']}–{series[-1]['version']}",
                     loc="left", fontsize=14, fontweight="bold", pad=17)
        ax.text(1, 1.06, f"Test n = {series[0]['n_samples']:,}",
                transform=ax.transAxes, ha="right", color="#63748A", fontsize=9)
        ticks = sorted({x[0], x[-1], *range(5, x[-1] + 1, 5)})
        ax.set_xticks(ticks, [f"v{n}" for n in ticks])
        ax.set_xlim(x[0] - .65, x[-1] + .65)
        ax.set_ylim(0, 100)
        ax.set_yticks(range(0, 101, 20))
        ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        ax.tick_params(axis="both", length=0, pad=8, labelleft=True)
        ax.set_xlabel("Version / iteration", labelpad=9, fontsize=10)
        ax.set_ylabel("Global acc (%)", labelpad=10, fontsize=11)
    fig.text(.075, .048,
             "Global acc = correct / all test samples (including abstentions).  "
             "Dots show recorded versions; each curve ends at its latest version.",
             fontsize=10, color="#63748A")
    for extension in ("png", "svg"):
        fig.savefig(OUTPUT / f"global_acc_iterations.{extension}", dpi=300)
    plt.close(fig)


def render_chart(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import PercentFormatter
    import numpy as np

    ink, muted, blue, orange = "#17283E", "#63748A", "#3769A8", "#DA8540"
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "text.color": ink, "axes.labelcolor": ink,
        "xtick.color": ink, "ytick.color": muted,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.edgecolor": "#CBD5E1",
        "figure.facecolor": "white", "axes.facecolor": "white",
        "savefig.facecolor": "white", "svg.fonttype": "path",
    })
    fig, ax = plt.subplots(figsize=(12, 7.2))
    fig.subplots_adjust(left=.095, right=.965, top=.76, bottom=.20)
    fig.text(.06, .947, "Global accuracy across datasets", fontsize=23,
             fontweight="bold", ha="left")
    fig.text(.06, .893, "Independent test set  /  First-to-latest version comparison",
             color=muted, fontsize=11.5)
    fig.legend(handles=[
        Patch(facecolor=blue, label="Latest version"),
        Line2D([0], [0], color=orange, linewidth=2.5, linestyle=(0, (5, 3)),
               marker="o", markersize=7, markeredgecolor="white",
               label="First version (v1)"),
    ], loc="upper left", bbox_to_anchor=(.06, .855), frameon=False,
       ncol=2, columnspacing=2.4, handlelength=2.4)

    x = np.arange(len(rows))
    first = np.array([row["first_global_acc"] for row in rows]) * 100
    latest = np.array([row["latest_global_acc"] for row in rows]) * 100
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#E7EDF4", linewidth=.9)
    ax.bar(x, latest, width=.52, color=blue, zorder=2)
    ax.plot(x, first, color=orange, linewidth=2.5, linestyle=(0, (5, 3)),
            marker="o", markersize=8.5, markeredgecolor="white",
            markeredgewidth=1.8, zorder=4)
    for i, row in enumerate(rows):
        ax.annotate(f"{latest[i]:.2f}%", (i, latest[i]), xytext=(0, 8),
                    textcoords="offset points", ha="center", color=ink,
                    fontsize=13, fontweight="bold", zorder=5)
        ax.annotate(f"{row['improvement_pp']:+.2f} pp", (i, latest[i]),
                    xytext=(0, 31), textcoords="offset points", ha="center",
                    fontsize=10, color=blue,
                    bbox=dict(boxstyle="round,pad=.35", facecolor="#EEF3FA",
                              edgecolor="none"), zorder=5)
        ax.annotate(f"{first[i]:.2f}%", (i, first[i]),
                    xytext=(0, 10 if first[i] < 7 else -20),
                    textcoords="offset points", ha="center", color="#A15A25",
                    fontsize=11, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=.2", facecolor="white",
                              edgecolor="none", alpha=.95), zorder=5)
    names = [row["dataset"].replace("Financial PhraseBank", "Financial\nPhraseBank")
             for row in rows]
    ax.set_xticks(x, names, fontsize=12, fontweight="normal")
    ax.tick_params(axis="x", length=0, pad=14)
    ax.tick_params(axis="y", length=0, pad=9)
    ax.set_xlim(-.65, len(rows) - .35)
    ax.set_ylim(0, 100)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
    ax.set_ylabel("Global acc (%)", fontsize=12, labelpad=12)
    fig.text(.095, .090, "Latest versions:  " + "   |   ".join(
        f"{row['dataset']} {row['latest_version']}" for row in rows),
        color=muted, fontsize=9)
    fig.text(.095, .048,
             "Global acc = correct / all test samples (including abstentions).   "
             "pp = percentage-point improvement over v1.",
             color=muted, fontsize=9)
    for extension in ("png", "svg"):
        fig.savefig(OUTPUT / f"global_acc_comparison.{extension}", dpi=300)
    plt.close(fig)


def write_summary(rows):
    with (OUTPUT / "global_acc_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# 四个数据集的 Global Acc 汇总", "",
        "Yelp v10、IMDb v10、CHECKED v15、AGnews v10 的下游模型见 "
        "[逻辑回归汇总](downstream_lr_summary.md)（[CSV](downstream_lr_summary.csv)）。", "",
        "![首版与最新版 Global Acc 对比](global_acc_comparison.png)", "",
        "![四个数据集的迭代曲线（2×2 子图）](global_acc_iterations.png)", "",
        "## 统计口径", "",
        "- 使用独立测试集 `test.all_samples.accuracy`，即正确预测数 / 测试集总样本数；弃权计入分母。",
        "- 仅统计含 `results/metrics.json` 的已评估版本，首版、最新版按版本号数字排序；最新版不是历史最优版本。",
        "- 蓝色柱表示各数据集最新版；橙色虚线连接各数据集首版（均为 v1）的结果。横轴是数据集，不是迭代时间。",
        "- 提升以百分点（pp）表示，由未舍入的准确率相减得到。图表和下表保留两位小数，CSV 保留原始精度。", "",
        "- 迭代图采用 2×2 子图，每个数据集独立展示已评估版本的测试集 Global Acc；纵轴统一为 0%–100%，横轴按实际版本范围绘制。",
        "- 曲线保留原始波动，不做平滑；标注首版与最新版的准确率，不对较早结束的数据集补值。", "",
        "## 结果", "",
        "| 数据集 | 首版 | 首版 Global Acc | 最新版 | 最新版 Global Acc | 提升 (pp) | 测试样本数 | 正确数（首版 → 最新版） |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['dataset']} | {row['first_version']} | {row['first_global_acc']:.2%} "
            f"| {row['latest_version']} | {row['latest_global_acc']:.2%} "
            f"| {row['improvement_pp']:+.2f} | {row['latest_n_samples']:,} "
            f"| {row['first_correct']} → {row['latest_correct']} |"
        )
    pending = []
    for folder, display_name in DATASETS:
        for path in sorted((ROOT / folder / "versions").iterdir(), key=lambda p: p.name):
            if (path.is_dir() and re.fullmatch(r"v[1-9]\d*", path.name)
                    and not (path / "results/metrics.json").is_file()):
                reason = "存在失败记录" if (path / "results/errors.json").is_file() else "尚无评估结果"
                pending.append(f"- {display_name} {path.name}：{reason}，未计入图表。")
                print(f"Excluded {display_name} {path.name}: {reason}")
    if pending:
        lines.extend(["", "## 未完成评估的版本", "", *pending])
    lines.extend(["", "## 数据来源", ""])
    for row in rows:
        lines.append(f"- {row['dataset']}：" + "；".join(
            f"[{row[prefix + '_version']}](../{row[prefix + '_source']})"
            for prefix in ("first", "latest")
        ))
    lines.extend([
        "", "## 文件与复现", "",
        "- `global_acc_comparison.png`：3600 × 2160，300 dpi。",
        "- `global_acc_comparison.svg`：矢量图，可用于论文或幻灯片排版。",
        "- `global_acc_summary.csv`：原始准确率（0–1）、版本、样本数、正确数、提升百分点及来源路径。",
        "- `global_acc_iterations.png` / `.svg`：四个数据集的迭代折线组合图（2×2），PNG 为 4200 × 2850，300 dpi。",
        "- `global_acc_iterations.csv`：各数据集已评估版本的原始准确率、样本数、正确数及来源路径。",
        "- `plot_global_acc.py`：自动读取四个数据集的已评估版本，重新生成上述图表和本说明，并列出未完成版本。", "",
        "在项目根目录运行（需要 Matplotlib、NumPy）：", "",
        "```powershell", 'python -B tasks/report/plot_global_acc.py', "```", "",
    ])
    (OUTPUT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    rows = load_results()
    iterations = load_iterations()
    # Keep plotting caches inside the workspace and remove them after rendering.
    previous_cache = os.environ.get("MPLCONFIGDIR")
    cache = OUTPUT / f".mpl-{uuid4().hex}"
    cache.mkdir()
    try:
        os.environ["MPLCONFIGDIR"] = str(cache)
        render_chart(rows)
        render_iterations(iterations)
    finally:
        if previous_cache is None:
            os.environ.pop("MPLCONFIGDIR", None)
        else:
            os.environ["MPLCONFIGDIR"] = previous_cache
        if cache.resolve().parent != OUTPUT:
            raise ValueError(f"Unexpected cache directory: {cache}")
        shutil.rmtree(cache)
    write_summary(rows)
    with (OUTPUT / "global_acc_iterations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(iterations[0]))
        writer.writeheader()
        writer.writerows(iterations)
    for row in rows:
        print(f"{row['dataset']}: {row['first_version']} {row['first_global_acc']:.2%} -> "
              f"{row['latest_version']} {row['latest_global_acc']:.2%} "
              f"({row['improvement_pp']:+.2f} pp)")


if __name__ == "__main__":
    main()
