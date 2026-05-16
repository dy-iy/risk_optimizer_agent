from __future__ import annotations

import json


ANALYZER_SYSTEM_PROMPT = (
    "你是一个专门分析加密货币新闻风险标注错误的诊断助手。"
    "你会同时参考统计结果和样本案例，总结规则系统的错误模式。"
    "必须严格基于输入证据，不要编造未出现的现象。"
    "输出必须是纯 JSON，不要输出 markdown，不要解释。"
)

ANALYZER_OUTPUT_SCHEMA = {
    "executive_summary": ["一句话结论1", "一句话结论2"],
    "patterns": [
        {
            "category": "false_positive 或 false_negative 或 type_mismatch 或 score_diff",
            "pattern_name": "错误模式名",
            "affected_scorers": ["score_whale", "score_liquidation"],
            "evidence": ["证据1", "证据2"],
            "likely_root_cause": "根因总结",
            "patch_suggestions": ["建议1", "建议2"],
            "priority": "high 或 medium 或 low",
        }
    ],
    "scorer_diagnosis": [
        {
            "score_col": "score_xxx",
            "problem_type": "过宽触发 / 漏召回 / 强度映射偏高 / 主类别竞争失败 / 证据不足",
            "why": "为什么这么判断",
            "recommendation": "怎么改",
        }
    ],
    "patch_plan": [
        {
            "step": 1,
            "target": "要先修的 scorer 或逻辑",
            "action": "修复动作",
            "expected_benefit": "预期收益",
        }
    ],
    "confidence": "high 或 medium 或 low",
}

ANALYZER_TASK_INSTRUCTIONS = [
    "识别 false_positive / false_negative / type_mismatch / score_diff 四类错误中最重要的错误模式。",
    "总结哪些 scorer 最可能有问题、问题是什么、应该怎么修。",
    "patch 建议必须尽量具体，最好能落到收紧关键词、增加否定词、增加金额门槛、调整阈值、修正主类别选择逻辑、区分强弱触发这类层面。",
    "只能根据提供的统计结果与样本来推断，不要发散。",
    "如果证据不足，就明确写“证据不足”。",
]


def build_analyzer_user_prompt(payload: dict) -> str:
    task_lines = "\n".join(f"{i}. {text}" for i, text in enumerate(ANALYZER_TASK_INSTRUCTIONS, start=1))
    schema_str = json.dumps(ANALYZER_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    payload_str = json.dumps(payload, ensure_ascii=False, indent=2)

    return f"""
请分析下面这份规则系统误差诊断材料，输出结构化 JSON。

你的任务：
{task_lines}

输出 JSON schema：
{schema_str}

下面是材料：
{payload_str}
""".strip()


def build_analyzer_messages(payload: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ANALYZER_SYSTEM_PROMPT},
        {"role": "user", "content": build_analyzer_user_prompt(payload)},
    ]
