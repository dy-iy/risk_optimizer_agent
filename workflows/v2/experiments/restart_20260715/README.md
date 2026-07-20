# restart_20260715

这是隔离的新一轮迭代实验。根目录原有的 `versions/` 没有移动或删除，可用于历史对比。

## 数据与种子

- 输入：`../../data/input/raw_1000_news.csv`
- Gold：`../../data/gold/crypto_news_risk_gold_1000.csv`
- 初始脚本：`versions/v1/scripts/risk_labeler_v1.py`
- 历史版本：仍保存在项目根目录 `../../versions/`

## 启动

推荐直接运行 Python 批量启动器（不受 PowerShell execution policy 影响）：

```powershell
py -3 .\experiments\restart_20260715\run.py
```

它会自动识别最新正式脚本。例如当前已有 v2，会提示：

```text
请输入起始版本号（默认 v2）：
请输入迭代次数（默认 1）：
```

直接回车接受默认起点，再输入 `5`，将依次尝试 `v2→v3` 到 `v6→v7`。某轮 pipeline 失败或候选未通过晋级门槛时会立即停止。

也可以用命令行参数跳过交互：

```powershell
py -3 .\experiments\restart_20260715\run.py --start-version 2 --iterations 5
```

当前实验默认使用宽松晋级模式：先执行严格门槛和最多两次补丁修正；若仍未通过，
从本轮候选中优先选择“目标错误不回归、综合质量改善”的最佳候选晋级，并在
`reports/orchestrations/*_relaxed_promotion.json` 中记录 override。若要恢复严格模式：

```powershell
py -3 .\experiments\restart_20260715\run.py --start-version 3 --iterations 5 --promotion-mode strict
```

每次批量运行的汇总保存在 `batch_runs/batch_*.json`。

Python 启动器只在当前进程中设置：

```powershell
RISK_VERSIONS_DIR=<本实验目录>\versions
```

所有 v1、v2……脚本和报告都会写入本实验目录，不会覆盖旧 `versions/`。

如果需要直接运行 Python，也可以：

```powershell
$env:RISK_VERSIONS_DIR = ".\experiments\restart_20260715\versions"
py -3 orchestrator.py
```
