# Crypto News Risk Gold 1000

## 用途

该数据集用于训练和评估加密货币新闻风险评分、等级分类和主风险类型识别模型。

## 数据规模与结构

- 行数：1000
- 字段数：10
- 风险等级：low=870，medium=80，high=50
- SHA256：`ca21e46563cd6642dc7c9896496b9bf500dc653972ba673bf05bd6e3f7f55e70`

标准字段：

```text
新闻id, 时间, 内容, risk_score, risk_label, risk_types,
primary_risk_type, reason, confidence, summary
```

分数范围为 0–100；0–39 为 low，40–69 为 medium，70–100 为 high。
`risk_types` 使用 JSON 数组字符串；无风险时为 `[]`，主风险类型为 `无明显风险`。

## 标注来源

- 两路高一致自动合并：825
- 轻微冲突 LLM 裁决：56
- 严重冲突 Codex AI 逐条复核：119

重要说明：严重冲突部分是 Codex AI 独立二次复核，不是真人标注，不应描述为人工金标。
逐行来源见 `crypto_news_risk_gold_1000_with_source.csv`，复核审计表见
`crypto_news_risk_gold_1000_codex_review.csv`。

## 训练入口

默认训练/评估文件：

```text
data/gold/crypto_news_risk_gold_1000.csv
```
