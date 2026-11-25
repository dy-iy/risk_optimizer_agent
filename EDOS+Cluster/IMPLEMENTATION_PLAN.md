# EDOS+Cluster 实施记录

本目录已接入语义聚类反馈、开发角色隔离和独立候选择优。当前配置与运行命令以 [README.md](README.md) 和 [orchestrator.py](orchestrator.py) 为准。

## 已实现的数据流

```text
冻结 train + valid
    → 固定发现/选择角色
    → 起始 LF 的两种聚合器评估
    → 仅用 train + discovery 指标确定 add / modify
        add: 当前全弃权 → 标签内语义聚类 → 训练支持检索 → 代表案例与相似反例
        modify: discovery 错例/正确例 + train 冲突例
    → 独立候选 Analyzer → Patcher → AST 校验 → 实际 LF 执行
    → 本地选择集验收 → 最多接受一个候选，否则保留基线
    → 下一轮重新计算当前未覆盖池，复用文本向量
    → 预算结束后独立评估最终测试集
```

## 文件职责

| 文件 | 职责 |
| --- | --- |
| orchestrator.py | 所有初始阈值、预算和模型参数；CLI |
| workflow.py | LangGraph 节点、独立运行/候选输出、接受版本推进、中断记录 |
| tools/data.py | 冻结文件校验、相同正文分组的分层开发角色清单 |
| tools/embeddings.py | 固定模型快照、CUDA 推理、向量缓存身份与截断统计 |
| tools/clustering.py | 全弃权/平票区分、标签内聚类、案例选择和训练支持检索 |
| tools/feedback.py | 只提取发现角色和训练无金标信息 |
| tools/analyzer.py | 规则归纳、操作契约、案例 ID/来源校验 |
| tools/patcher.py | 同组案例约束下生成函数；局部合并及 AST 校验 |
| tools/runner.py | 复用 LF 投票评估 Snorkel/simple_vote；选择集单列；测试可选 |
| tools/acceptance.py | 使用 simple_vote 选择集真实收益验收并提供排序分数 |
| tools/impact.py | 投票/输出变化、全弃权转正确/错误、旧正确退化、平票变化 |
| tools/records.py / llm.py | 版本记录、实际 API 用量、错误记录和非敏感模型身份 |
| picture/plot_metrics.py | 按消耗轮次绘图、三种反馈模式的同协议对照检查 |
| tools/train_logistic.py | 已接受弱标签训练；按选择角色选参；最终测试报告 |

## 运行与版本约定

原 data、raw、历史 LF 和结果保持保留。每次实验创建新的 runs 目录，只复制起始 LF。候选均从当轮相同基线产生；接受前不会进入正式 versions。拒绝候选、解析失败和修复原文完整保留在该轮目录中。

开发角色默认 1500/500，随机种子 42；manifest 绑定原始 CSV 哈希和全部 ID。不同角色/协议不允许混合比较。测试仅在最终选择结束后运行，最终报告独立于开发评估结果保存。

## 实验开关

`random / cluster_random / cluster_diverse` 控制正例选择；反例与训练支持的算法及数量预算相同。默认一簇一条 LF、每轮一个独立候选，最多可配置三个。关闭验收仅允许单候选，作为共享基础设施下的工程对照。

三组应固定起始源码、数据角色、seed、LLM、轮数、候选数及验收配置。比较入口会检查配置身份；实际调用数和 Token 需结合各自日志报告。候选验收仍会逐渐自适应使用选择集，不能替代独立测试。

当前不实现自动恢复/重放中断请求，运行目录已存在时直接拒绝覆盖；run_state.json 标记中断和最后完成的轮次。所有阈值是初始配置，应通过相同协议的开发实验校准。
