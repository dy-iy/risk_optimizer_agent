from __future__ import annotations

import json


PATCHER_SYSTEM_PROMPT = (
    "你是一个专门给加密货币新闻风险规则系统打补丁的 Python 工程师。"
    "你会根据 LLM 错误分析报告，直接产出一份完整、可运行、结构尽量稳定的新脚本。"
    "你的目标是降低整体错误，而不是单独压低 false_positive；必须同时考虑 false_positive、false_negative、type_mismatch 和 score_diff。"
    "必须尊重 analysis 中的 metric_tradeoff_diagnosis、scorer_trace_diagnosis、primary_type_diagnosis、risky_patch、guardrail、validation 和 do_not_patch。"
    "必须优先做证据充分的修改，不要随意重构，不要引入不必要的新依赖。"
    "输出必须是纯 JSON，不要输出 markdown，不要解释。"
)

PATCHER_OBJECTIVES = [
    "直接生成“完整的新脚本代码”，而不是只给修改建议。",
    "只根据 analysis_llm.json 中证据充分的部分做修改。",
    "尽量保留原脚本的整体结构、函数名、输入输出接口、列名和主流程。",
    "不要改变 CSV 输入输出协议，除非 analysis 明确要求。",
    "不要依赖除标准库和 pandas 之外的新第三方包。",
    "修改重点应该落在：关键词范围、否定词、金额门槛、阈值、强弱触发、主类别逻辑、英文大小写归一化等。",
    "优先执行能同时改善多个指标的 patch；如果某项修改只降低 false_positive 但 analysis 标记 risky_patch，默认不要做，除非同时加入明确 guardrail。",
    "如果 version_history 或 regression_summary 显示某些指标恶化，必须优先修复 regression，而不是继续优化已经改善的指标。",
    "处理 score_diff 时必须区分 overestimate 和 underestimate：高估应降权或加否定，低估应补召回或提高弱触发强度。",
    "处理 type_mismatch 时必须区分 scorer 过强、gold scorer 过弱、primary_type 选择逻辑问题；close_competition 场景优先改主类别仲裁，不要简单整体降权。",
    "如果 analysis 提供 scorer_trace_diagnosis 或样本级 scorer_trace，优先把修改落到 trace 指出的关键词表、否定词表、early return guard、阈值或 primary 竞争逻辑。",
    "patch_report 的 changes 必须写出 guardrail 和 validation，说明如何避免 false_negative、type_mismatch 或 score_diff 恶化。",
    "禁止修改 analysis 中 do_not_patch 指定且证据不足的 scorer 或逻辑。",
    "如果 analysis 证据不足，就少改，不要硬改。",
    "每个候选原则上只修改一个独立机制，形成可归因的 coordinate-descent 实验；多个互不相关的 scorer 不要在同一轮一起改。",
    "把错误样本视为 counterexamples：修改必须覆盖目标反例，同时保留 analysis 中 guardrail 对应的正确样本行为。",
    "候选只有在 false_positive_rows、false_negative_rows、type_mismatch_rows 均不回归且至少一项实际下降时才会晋级；不得依靠 MAE 或 accuracy 多数票掩盖目标错误数回归。",
    "若主类别反事实诊断表明 low 等级弱类型过度分配是主因，优先让主类别输出与风险等级语义一致，不要改动风险分数或等级阈值。",
    "生成的 full_code 必须是完整、可运行、可保存为 .py 的脚本。",
]

PATCHER_OUTPUT_SCHEMA = {
    "patch_version": "v2.1 或类似版本号",
    "summary": ["一句话总结1", "一句话总结2"],
    "changes": [
        {
            "target": "score_whale / score_liquidation / shared logic 等",
            "reason": "为什么改",
            "action": "做了什么改动",
            "risk": "low / medium / high",
            "guardrail": "如何避免其它指标恶化",
            "validation": "修完后重点检查哪些指标和错误桶",
        }
    ],
    "validation_notes": ["注意点1", "注意点2"],
    "contract_acknowledgement": {
        "single_hypothesis": "the one mechanism implemented",
        "changed_code_regions": ["function_or_constant_name"],
        "target_news_ids": ["expected improved rows"],
        "must_preserve_news_ids": ["control rows checked conceptually"],
        "forbidden_transitions": ["transitions the patch must not create"],
    },
    "full_code": "完整 Python 代码字符串",
}

REPAIR_SYSTEM_PROMPT = (
    "你是一个 Python 修复助手。"
    "你会在不改变业务逻辑的前提下，最小化修复代码中的语法/结构问题。"
    "输出必须是纯 JSON，不要输出 markdown。"
)

REPAIR_OUTPUT_SCHEMA = {
    "repair_summary": ["修了什么"],
    "full_code": "修复后的完整 Python 代码字符串",
}


def build_patch_prompt(
    source_code: str,
    analysis_data: dict,
    revision_feedback: dict | None = None,
) -> tuple[str, str]:
    analysis_str = json.dumps(analysis_data, ensure_ascii=False, indent=2)
    objective_lines = "\n".join(f"- {item}" for item in PATCHER_OBJECTIVES)
    schema_str = json.dumps(PATCHER_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    feedback_str = json.dumps(revision_feedback or {}, ensure_ascii=False, indent=2)

    user_prompt = f"""
你将收到两份输入：
1. 当前版本的完整 Python 规则脚本
2. 基于误报/漏报/类型错配做出的 analysis_llm.json

你的目标：
{objective_lines}

输出 JSON schema：
{schema_str}

当前脚本：
```python
{source_code}
```

analysis_llm.json：
```json
{analysis_str}
```

Revision feedback from a previously rejected candidate (empty on the first attempt):
```json
{feedback_str}
```

Obey analysis_llm.patch_contract as a hard contract. Implement only its single
hypothesis and allowed code region. Do not repeat a mechanism listed in revision
feedback. The revised patch must directly address every reported target metric
regression and forbidden transition.
""".strip()

    return PATCHER_SYSTEM_PROMPT, user_prompt


def build_repair_prompt(bad_code: str, compile_error: str) -> tuple[str, str]:
    schema_str = json.dumps(REPAIR_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)

    user_prompt = f"""
下面这份由 LLM 生成的 Python 代码编译失败了。
请只修复导致编译失败的问题，尽量不要改变业务逻辑。

编译错误：
{compile_error}

坏代码：
```python
{bad_code}
```

输出 JSON schema：
{schema_str}
""".strip()
    return REPAIR_SYSTEM_PROMPT, user_prompt


def build_messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
