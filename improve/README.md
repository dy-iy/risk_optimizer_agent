# Improve｜按覆盖率和准确率选择 LF 操作

这些任务用简单投票的覆盖率和已覆盖样本准确率决定本轮新增或修改 LF，再按操作构造 Analyzer 的反馈样例。

| 任务 | 分类目标 |
| --- | --- |
| [AGnews_improve](AGnews_improve/README.md) | 英文新闻主题四分类 |
| [CHECKED_improve](CHECKED_improve/README.md) | 中文新闻真实性二分类 |
| [EDOS_improve](EDOS_improve/README.md) | 英文性别歧视二分类 |
| [IMDb_improve](IMDb_improve/README.md) | 英文影评情感二分类 |
| [SMS_improve](SMS_improve/README.md) | 短信垃圾信息二分类 |
| [Yelp_improve](Yelp_improve/README.md) | 英文商户评论情感二分类 |
| [YouTube_improve](YouTube_improve/README.md) | YouTube 单条评论垃圾信息二分类 |

六个任务共用带运行计时的工作流，`EDOS_improve` 使用同一核心策略的早期版本。相关实现还有采用同一策略的 [YouTube 评论串协同诈骗检测](../tasks/Youtube_Collusion_Scam/README.md) 和加入聚类反馈的 [EDOS+Cluster](../EDOS+Cluster/README.md)。
