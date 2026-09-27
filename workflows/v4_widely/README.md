# 各数据集独立的 V4 工作流

每个任务都有完整、独立的实现，不导入上层共用程序或其他任务代码。修改一个数据集的工作流不会改变另一个任务。

```text
tasks/<数据集>/
  init.py                  初始化：逐个生成 LF，保存首版脚本
  analyzer.py              分析：依据训练错例提出修改建议
  patcher.py               修改：局部替换指定 LF，输出候选脚本
  orchestrator.py          总控：串联阶段、版本选择、重试与续跑
  data/
    train.csv, test.csv    两个数据集
    README.md              任务类型、样本数与标签分布
  artifacts/
    config.json            标签、指标权重与任务配置
    prompts/               initialize / analyze / patch 提示词
    annotation.md          标注说明
    lf_design.md           LF 设计说明
    data_metadata/         数据清单、源文件哈希与复核记录
  tools/
    runner.py              LF 校验、执行、预测聚合与离线评估
    evaluator.py           分类指标、覆盖率、混淆矩阵与 LF 质量
    slicer.py              错例切片与任务诊断
    comparator.py          版本质量门槛
    data.py                数据字段映射、CSV 读写与校验
    paths.py               任务配置与路径
    model.py               模型调用、提示词契约与 token 记账
    reporter.py            报告、指标表与续跑读取
    common.py              JSON 读写与哈希
    README.md              工具接口与使用说明
  versions/
    v1...vN/               已接受规则、辅助代码与报告
    iterations/            总控各轮候选与诊断
  README.md                任务说明与运行命令
  report.md                当前结果与状态
  iterations.csv           累计迭代指标
  script_metrics.csv       各脚本的训练集和测试集指标
  tokens.csv               累计调用账本
  changes.md               分析与修改记录
  checkpoint.json          自动续跑状态
```

CSV 使用 UTF-8 BOM 编码，标签直接作为列保存：单标签任务为 `label`，FinNL 为 `labels`，Crypto 为 `categories` 和 `level`。公共列为 `id`、`text`、`inputs`、`metadata`。多标签列表以及输入/来源对象使用 JSON 单元格保存，由 `tools/data.py` 还原；保留样本的原始文本、ID、标签和来源信息不变；多数类多余样本从实际 CSV 中删除。

各任务的 `data/README.md` 说明标注任务类型，并分别列出训练集与测试集的标签数量、占比；多标签任务按包含该标签的样本数统计，Crypto 另列风险等级分布。`data/` 内不放 manifest 或复核 JSON：这些文件位于 `artifacts/data_metadata/`。读取时校验数据哈希、记录数、ID 顺序、任务配置与源文件哈希。

| 任务 | train.csv | test.csv |
| --- | ---: | ---: |
| Crypto | 56 | 15 |
| FinCausal | 4,948 | 1,236 |
| FinNL | 280 | 71 |
| FinNSP | 3,586 | 896 |
| PhraseBank | 1,425 | 357 |

## 运行

在 CMD 激活已有环境，然后在项目根目录选择一个任务启动：

```bat
D:\Miniconda\Scripts\activate && conda activate base
python workflows/v4_widely/tasks/crypto/orchestrator.py
```

其他任务的入口：

```text
workflows/v4_widely/tasks/phrasebank/orchestrator.py
workflows/v4_widely/tasks/fincausal/orchestrator.py
workflows/v4_widely/tasks/finnl/orchestrator.py
workflows/v4_widely/tasks/finnsp/orchestrator.py
```

也可进入相应任务目录执行 `python orchestrator.py`。无需 `--task`，无需 `.venv` 或重新安装环境。

默认读取本任务 `data/`。可用 `--data-dir <目录>` 指定另一份 CSV 快照，原 `--prepared-dir` 保留为兼容别名。对应清单位于该目录旁的 `artifacts/<目录名>_metadata/`；例如 `data_seed7/` 对应 `artifacts/data_seed7_metadata/`。不同 seed 需准备独立快照，已有快照不会覆盖；续跑沿用检查点绑定的数据。

五个任务默认显示 tqdm 进度条：外层显示本次追加迭代的完成轮数、当前阶段、版本和候选尝试次数，内层显示 train/test 的逐条评估进度。提前停止时保留实际完成轮数，结束时显示停止原因。使用 `--no-progress` 可关闭进度条，仍保留结果和 token 汇总。

默认从本任务 `versions/` 中当前已接受版本继续，`--iterations N` 表示追加 N 轮。没有版本时生成 v1；`--new` 仅适用于尚无版本的任务，不覆盖已有历史。版本编号与 token 持续累计，不再创建 runs 或日期目录。

当前五个任务均已清空生成脚本和运行历史。从头生成 Crypto v1 并迭代一轮：

```powershell
python workflows/v4_widely/tasks/crypto/orchestrator.py --new --iterations 1
```

也可在任务目录单独执行阶段：

```powershell
python init.py                         # 尚无版本时生成并评估 v1
python analyzer.py --version v1         # 分析训练错例
python patcher.py --version v1          # 根据分析生成候选
python tools/runner.py --version v1 --split train  # 离线评估
```

`init.py` 不覆盖历史版本。独立 Analyzer 的分析绑定脚本和数据哈希，独立 Patcher 会校验匹配关系，候选写入 `versions/vN/reports/patch/attempt_N/`，不会自动推进版本。自动迭代与版本接受统一由 `orchestrator.py` 完成。模型调用、提示词与 token 记账仍使用原有约定。

## 保留 V4 迭代思路

逐个生成 LF → 评估 → 根据各字段前 20 条错例分析 → 局部替换指定函数 → 比较质量分及指标下降幅度。质量未通过时从同一已接受版本重新修改一次；两次未通过则保留原版本并停止。运行异常直接记录并停止。

不同任务的输入、标签、prompt 和评测指标分别维护。只划分 train/test：train 用于生成 LF、错例反馈及版本选择，test 在本次迭代决策结束后才评估，不参与修改、重试或版本选择。每个保存的脚本（含候选）同目录下的 report.md 同时列出训练集和测试集指标；任务 report.md 与 script_metrics.csv 汇总全部脚本。已有规则曾见过旧训练样本，重分配后的测试集对历史规则仅作兼容性对照。

当前五个任务都使用按标签配额分配的 CSV。旧规则、候选、报告、检查点、调用账本和历史归档已删除；下一次运行从 v1 和第 1 轮开始，重新记录指标与 token。

## 数据状态

五个任务均可直接使用已有 train/test CSV 快照。FinNL、FinNSP、FinCausal 已完成保守自动复核：仅修复能唯一解析的标签格式，排除无法确定的标签及同文本冲突，去除完全重复记录；原始数据保持不变。各任务 artifacts/data_metadata/ 仅保留运行校验所需的 manifest.json，以及重新准备数据所需的 automated_review.json（适用任务）。标签修复和排除依据见 automated_review.json，当前数量与标签分布见 data/README.md。这是清理后的实验子集，不等同官方完整评测。FinNL 的“其它”类无样本，固定 17 类 Macro-F1 仍将该类计为 0。

正常运行直接使用本任务 `data/` 中的 CSV，读取时自动校验数据清单。需要重新准备数据时，可使用任务入口的 `--prepare-only --data-dir <新目录> --review <automated_review.json路径>`；已存在的数据快照不会覆盖。

`versions/` 当前仅有目录占位文件。数据、任务配置、提示词和四个主程序均保留；首轮完成后，去掉 `--new` 即可从新生成的版本继续迭代。

## 按标签均衡数据

五个任务均由 `tools/data.py` 同时完成多数类下采样与标签分层分配，目标约 80% train / 20% test；每侧同一输出字段内，所有有样本类别的最大/最小数量不超过 **3:1**。多标签按出现次数约束，Crypto 还约束风险等级与空/非空类别。相同文本整组分配，标签保持原样，保留尽可能多的样本；seed 仅用于同标签组合内的稳定选择。

FinCausal 保留所有 1,546 条正类和 4,638 条负类；FinNSP 无需下采样。Crypto 稳定币风险只有 2 条，严格均衡后共 71 条；FinNL 共 351 条且“其它”仍无样本。这些小样本标签的测试指标波动较大。测试集也经过均衡化，指标不代表原始自然分布。

准备数据需要 SciPy，已列入根目录 requirements.txt；当前环境已安装。数据清单记录配额、删除 ID、来源哈希；读取时验证比例、类别覆盖、ID 和文本隔离。每次准备新快照都会生成对应 data/README.md。
