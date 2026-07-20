from __future__ import annotations

import json


ANALYZER_SYSTEM_PROMPT = (
    "你是一个专门分析加密货币新闻风险标注错误的诊断助手。"
    "你的目标不是单独降低 false_positive，而是降低整体错误，必须同时权衡 false_positive、false_negative、type_mismatch、score_diff。"
    "你会同时参考统计结果、样本案例、分数方向、主类别竞争关系、scorer_trace 和多版本指标，总结规则系统的错误模式。"
    "每条 patch 建议都必须评估副作用，尤其是是否会让 false_negative、type_mismatch 或 score_diff 恶化。"
    "必须严格基于输入证据，不要编造未出现的现象；如果证据不足，必须明确写“证据不足”。"
    "输出必须是纯 JSON，不要输出 markdown，不要解释。"
)

ANALYZER_OUTPUT_SCHEMA = {
    "executive_summary": ["一句话结论1", "一句话结论2"],

    "metric_tradeoff_diagnosis": {
        "main_improved_metric": "false_positive",
        "worsened_metrics": ["type_mismatch", "score_diff"],
        "likely_reason": "系统通过收紧触发条件降低误报，但造成召回下降或主类别竞争变化",
        "optimization_warning": "不要继续单独压 false_positive"
    },

    "patterns": [
        {
            "category": "false_positive / false_negative / type_mismatch / score_diff",
            "pattern_name": "错误模式名",
            "affected_scorers": ["score_xxx"],
            "evidence": ["证据1", "证据2"],
            "likely_root_cause": "根因总结",
            "score_direction": "overestimate / underestimate / mixed / not_applicable / evidence_insufficient",
            "patch_suggestions": ["建议1", "建议2"],
            "possible_side_effects": ["可能增加 FN", "可能造成类别错配"],
            "risky_patch": True,
            "priority": "high / medium / low"
        }
    ],

    "scorer_diagnosis": [
        {
            "score_col": "score_xxx",
            "problem_type": "过宽触发 / 漏召回 / 强度映射偏高 / 强度映射偏低 / 主类别竞争失败 / 证据不足",
            "why": "为什么这么判断",
            "recommendation": "怎么改",
            "risk_of_patch": "可能副作用"
        }
    ],

    "scorer_trace_diagnosis": [
        {
            "score_col": "score_xxx",
            "trace_signal": "missing_positive_trigger / matched_negative_guard / below_threshold / primary_competition / over_trigger / evidence_insufficient",
            "evidence": ["trace 证据1", "trace 证据2"],
            "code_area": "相关函数、关键词表、否定词表或 early return guard",
            "recommendation": "基于 trace 的修复建议",
            "guardrail": "避免引入哪些回归"
        }
    ],

    "primary_type_diagnosis": [
        {
            "mismatch_pair": "gold=regulatory, rule=fraud",
            "likely_reason": "rule scorer 过强 / gold scorer 过弱 / 主类别选择逻辑问题 / 证据不足",
            "fix": "调整主类别选择逻辑或类别优先级",
            "side_effect_guardrail": "避免让被压低类别的 false_negative 或 score_diff 增加"
        }
    ],

    "patch_plan": [
        {
            "step": 1,
            "target": "要先修的 scorer 或逻辑",
            "action": "修复动作",
            "expected_benefit": "预期收益",
            "guardrail": "不能让哪些指标恶化",
            "validation": "修完后检查哪些指标"
        }
    ],

    "do_not_patch": [
        {
            "target": "不建议继续收紧的 scorer",
            "reason": "因为可能导致 FN 或 score_diff 增加"
        }
    ],

    "confidence": "high / medium / low"
}

ANALYZER_TASK_INSTRUCTIONS = [
    "同时分析 false_positive / false_negative / type_mismatch / score_diff，不允许只优化 false_positive，也不允许忽略任一错误桶。",
    "对每个错误模式，必须判断它属于：误报过宽、漏召回、主类别竞争失败、分数高估、分数低估、标签阈值错误、证据不足。",
    "分析 score_diff 时必须区分 rule_risk_score 高于 gold_risk_score（overestimate）还是低于 gold_risk_score（underestimate）；高估优先考虑降权、强度映射和阈值，低估优先考虑召回缺口、弱触发和强度映射偏低。",
    "分析 type_mismatch 时必须判断是错误 scorer 过强、gold 对应 scorer 过弱，还是 primary_type 选择逻辑有问题；如果主类别分差很小，要考虑 close competition，而不是简单压低某一类。",
    "如果样本提供 scorer_trace，必须结合 trace 判断低分/零分来自 missing_positive_trigger、matched_negative_guard、below_threshold 还是 primary_competition；scorer_trace 是静态启发式 trace，不是精确运行分支，证据不足时仍写“证据不足”。",
    "如果 scorer_trace 显示文本命中负向词表或 early return guard，patch 建议必须优先检查该 guard 是否过宽；如果 trace 显示目标 scorer 未命中任何正向词表，patch 建议必须落到补充关键词/弱触发入口。",
    "每条 patch 建议必须说明可能带来的副作用，尤其是是否会增加 false_negative、type_mismatch 或 score_diff。",
    "优先推荐能同时改善多个指标的 patch。",
    "如果一个 patch 只会降低 false_positive 但可能恶化 false_negative、type_mismatch 或 score_diff，必须把 risky_patch 标记为 true，并写出 guardrail。",
    "如果存在多轮版本指标，必须优先分析 regression，而不是继续优化已经改善的指标。",
    "只能根据提供的统计结果与样本推断，不要发散；证据不足时写“证据不足”。",
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
