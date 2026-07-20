from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import validate_dataset_pipeline


REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESS_OUTPUT = Path(__file__).resolve().parent / "output"
GOLD_DIR = REPO_ROOT / "data" / "gold"

SOURCE_DATASET = PROCESS_OUTPUT / "final_gold_news_1000.csv"
SOURCE_WITH_PROVENANCE = PROCESS_OUTPUT / "final_gold_news_1000_with_source.csv"
SOURCE_MANIFEST = PROCESS_OUTPUT / "final_gold_news_1000_manifest.json"
SOURCE_REVIEW = PROCESS_OUTPUT / "codex_review_priority_119.csv"

TARGET_DATASET = GOLD_DIR / "crypto_news_risk_gold_1000.csv"
TARGET_WITH_PROVENANCE = GOLD_DIR / "crypto_news_risk_gold_1000_with_source.csv"
TARGET_MANIFEST = GOLD_DIR / "crypto_news_risk_gold_1000_manifest.json"
TARGET_REVIEW = GOLD_DIR / "crypto_news_risk_gold_1000_codex_review.csv"
TARGET_CARD = GOLD_DIR / "DATASET_CARD.md"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def build_dataset_card(manifest: dict) -> str:
    label_counts = manifest["risk_label_counts"]
    source_counts = manifest["source_counts"]
    return f"""# Crypto News Risk Gold 1000

## 用途

该数据集用于训练和评估加密货币新闻风险评分、等级分类和主风险类型识别模型。

## 数据规模与结构

- 行数：{manifest['rows']}
- 字段数：{len(manifest['columns'])}
- 风险等级：low={label_counts.get('low', 0)}，medium={label_counts.get('medium', 0)}，high={label_counts.get('high', 0)}
- SHA256：`{manifest['outputs']['dataset']['sha256']}`

标准字段：

```text
新闻id, 时间, 内容, risk_score, risk_label, risk_types,
primary_risk_type, reason, confidence, summary
```

分数范围为 0–100；0–39 为 low，40–69 为 medium，70–100 为 high。
`risk_types` 使用 JSON 数组字符串；无风险时为 `[]`，主风险类型为 `无明显风险`。

## 标注来源

- 两路高一致自动合并：{source_counts.get('agreement_candidate', 0)}
- 轻微冲突 LLM 裁决：{source_counts.get('minor_conflict_llm_adjudicated', 0)}
- 严重冲突 Codex AI 逐条复核：{source_counts.get('codex_ai_reviewed', 0)}

重要说明：严重冲突部分是 Codex AI 独立二次复核，不是真人标注，不应描述为人工金标。
逐行来源见 `crypto_news_risk_gold_1000_with_source.csv`，复核审计表见
`crypto_news_risk_gold_1000_codex_review.csv`。

## 训练入口

默认训练/评估文件：

```text
data/gold/crypto_news_risk_gold_1000.csv
```
"""


def main() -> None:
    # 晋升前复用完整回放与结构校验。
    validate_dataset_pipeline.validate_consistency_replay()
    validate_dataset_pipeline.validate_final_outputs()

    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    allowed_status = {"training_ready_ai_reviewed", "training_ready_human_reviewed"}
    if manifest.get("dataset_status") not in allowed_status:
        raise ValueError(f"数据集尚不可晋升：{manifest.get('dataset_status')}")
    if int(manifest.get("pending_human_review_rows", -1)) != 0:
        raise ValueError("仍有未完成复核的严重冲突样本")

    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_DATASET, TARGET_DATASET)
    shutil.copy2(SOURCE_WITH_PROVENANCE, TARGET_WITH_PROVENANCE)
    if SOURCE_REVIEW.exists():
        shutil.copy2(SOURCE_REVIEW, TARGET_REVIEW)

    final_manifest = dict(manifest)
    final_manifest["artifact_status"] = "final_training_dataset"
    final_manifest["promoted_at_utc"] = datetime.now(timezone.utc).isoformat()
    final_manifest["outputs"] = {
        "dataset": {
            "path": relative(TARGET_DATASET),
            "sha256": sha256_file(TARGET_DATASET),
        },
        "dataset_with_source": {
            "path": relative(TARGET_WITH_PROVENANCE),
            "sha256": sha256_file(TARGET_WITH_PROVENANCE),
        },
        "review_audit": {
            "path": relative(TARGET_REVIEW),
            "sha256": sha256_file(TARGET_REVIEW),
        },
    }
    TARGET_MANIFEST.write_text(
        json.dumps(final_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    TARGET_CARD.write_text(build_dataset_card(final_manifest), encoding="utf-8")

    print(f"最终训练集：{TARGET_DATASET}")
    print(f"来源版本：{TARGET_WITH_PROVENANCE}")
    print(f"复核审计：{TARGET_REVIEW}")
    print(f"数据清单：{TARGET_MANIFEST}")


if __name__ == "__main__":
    main()
