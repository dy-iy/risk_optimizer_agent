# 金融文本标注数据集

整理日期：2026-09-17。已删除 macOS 元数据、Python 缓存和已解压的 PhraseBank ZIP；保留全部数据划分、许可证、标注说明和来源记录。FinNSP、FinNL、FewFC 共 16 个 JSON 数据文件统一为 UTF-8、两空格缩进的 JSON 数组，记录顺序、字段值及标签均保持不变。FewFC 本地文件请用 `json.load()` 整体读取，不再逐行解析；上游 README 描述的仍是上游格式。

下载日期：2026-09-16。此目录独立于 `workflows/v4/data/`；没有修改现有规则、训练数据或启动 LLM 调用。

已下载 6 个数据集的公开数据；DuEE-Fin 尚未下载。所有计数以当前文件为准，不沿用论文或网页中的不同版本统计。

## 已下载内容与实际规模

| 数据集 | 本地目录 | 实际下载内容 |
| --- | --- | --- |
| FinNSP | `finnsp/raw/` | train_list 4000 条带标签；eval_list 500 条带标签；problem_list 500 条无标签 |
| FinNL | `finnl/raw/` | train_list 7071 条带标签；eval_list 884 条带标签；problem_list 884 条无标签 |
| FewFC | `fewfc/` | original 与 rearranged 两套上游划分、事件定义 PDF、README、许可证；rearranged 四文件共 8982 条记录 |
| Financial PhraseBank | `financial_phrasebank/` | 解压后的数据与说明；50/66/75/100% 一致子集分别为 4846/4217/3453/2264 条 |
| FinCausal 2020 | `fincausal2020/` | 官方 trial、practice、blind evaluation 两项任务 CSV，以及标注指南 PDF |
| EDT | `edt/Event_detection/` | 官方事件检测 train.txt 与 dev.txt；按空行分隔的文档块统计分别为 7770、1948 |

FewFC rearranged 的详细划分：train_base=2732，test_base=1821，train_trans=820，test_trans=3609。这是原项目的源域/目标域划分，不能直接当成常规随机 train/dev/test。未下载 `partial/` 中用于其他论元抽取实验的派生版本。

FinCausal Task 1：trial=8580，practice=13478，blind evaluation=7386。Task 2：trial=641，practice=1109，blind evaluation=638。trial/practice 不应未经重叠检查就拼接；blind evaluation 只有输入，没有本地可评分的金标。

EDT 仅下载与本任务相关的事件标注部分，未下载大规模行情回测或领域预训练语料。上游 README 记载的 9721 篇与本地按空行统计的 9718 个文档块有差异，后续需结合正式加载器检查，不能擅自补齐。

## 来源与版本

- FinNSP、FinNL：[作者仓库](https://github.com/supersymmetry-technologies/BBT-FinCUGE-Applications)，固定提交 `3b686ca79e2184b36498ba15fd1e9609d9eda3cb`。来自 `FinCUGE_Publish/finnsp/` 与 `FinCUGE_Publish/finnl/`。
- FewFC：[作者仓库](https://github.com/TimeBurningFish/FewFC)，固定提交 `ec6cac90238c04b45d4512a695ee6375fc72af2f`。
- Financial PhraseBank：[数据发布页面](https://huggingface.co/datasets/takala/financial_phrasebank)，固定 revision 见 `phrasebank_revision.txt`。ZIP SHA256 与发布方 LFS 元数据核对一致。
- FinCausal 2020：[主办方仓库](https://github.com/yseop/YseopLab/tree/develop/FNP_2020_FinCausal)，固定提交 `33a933f1bebd3317ab67edcf447c1a333fe879f5`。
- EDT：[论文作者数据说明](https://github.com/Zhihan1996/TradeTheEvent/tree/main/data)，文件来自其链接的公开 Google Drive 文件夹。Drive 文件无提交版本，记录文件 ID、字节数与 SHA256。

`manifest.json`、`manifest_extra.json` 记录每个文件的来源 URL、字节数和 SHA256；`audit.json` 记录解析、计数与精确文本重叠检查结果。

清单中的 `bytes`、`sha256` 对应当前本地文件；格式化前的校验值保存在 `upstream_bytes`、`upstream_sha256`。已删除的下载附属文件记录在 `removed_download_artifacts`，不参与本地文件校验。

## 使用前必须了解的问题

1. **FinNSP/FinNL 当前公开文件规模与上游 README 不一致。** 前期讨论的 FinNSP 4800/600/600、FinNL 8000/1000/1000 是介绍中的数字，不是此次下载文件的实际规模。
2. **problem_list 无标签。** 这两个数据集的 problem_list 均只有 id/input，不能用于本地 F1 评估；也未将其冒充有金标测试集。检查未发现其正文与 eval_list 的精确重叠，不能把它们视为同一批样本的双版本。
3. **FinNL 有标签质量问题。** 原始训练标签按空白切分后可见 `虚拟火币`、`外中国`、`中国公司` 等疑似错字/粘连，以及 `修改`、`无法`、`zc` 等异常标签内容。完整频次见 audit.json。这是待核实项，没有自动纠正，也没有假定空白切分就是最终标签解析规范。
4. **精确重复文本存在。** FinNL 训练集有 6939 个不同正文，eval 有 880 个，二者交集为 8 个；FinNSP 训练/eval 有 1 个正文交集。正式评测应制定去重/分组协议。这只是精确匹配检查，未做近重复或同事件去重。
5. **数据内容保持不变。** 仅调整 JSON 排版和 FewFC 的外层容器，没有做标签清洗、重新划分、训练或推断，不代表已经可以直接作为论文最终评测数据。
6. **FewFC 本地 `.json` 已从 JSON Lines 转为缩进的 JSON 数组**，每个对象的字段有 id/content/events。original 和 rearranged 有重叠，不能拼接当作独立样本。
7. **PhraseBank 使用 cp1252 编码**，每行 `文本@标签`；四个一致性子集有重叠，没有官方固定 train/dev/test。macOS 元数据与重复压缩包已清理，加载时只选择 `Sentences*.txt`。
8. **EDT 是 token 级标注格式。** 若派生文档级事件分类，须明确派生方法，不直接比较原任务分数。
9. **许可证与论文引用。** FewFC 附 CC BY-SA 4.0；PhraseBank 附 CC BY-NC-SA 3.0。其余保留作者说明，未推断未明确声明的数据再分发授权。

## 尚未下载：DuEE-Fin

[官方竞赛入口](https://aistudio.baidu.com/aistudio/competition/detail/65)，[官方基线](https://github.com/PaddlePaddle/PaddleNLP/tree/develop/slm/examples/information_extraction/DuEE)。

本次 HTTP 请求可访问竞赛页面，但返回页面没有数据附件直链；当前没有可用的交互浏览器，因此未能继续取得动态页面中的附件。没有使用来历不明的镜像替代原始发布版本。后续从官方页面取得原始包后，可放到 `duee_fin/` 并补充清单。

## 复现下载与核验

在项目根目录运行，均只依赖 Python 标准库：

```powershell
python data/benchmarks/download_core.py
python data/benchmarks/download_extra.py
python data/benchmarks/audit_downloads.py
```

下载脚本复用已存在的文件；如要检查本地文件是否被修改，应先运行 audit_downloads.py 与既有清单比对。下载脚本重新运行会重建清单，因此不要用重下载代替已有清单的完整性检查。
