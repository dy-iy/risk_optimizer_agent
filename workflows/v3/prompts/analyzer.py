import json


TYPE_MISMATCH_SYSTEM_PROMPT = (
    "你是 v3 的类型错配修复 analyzer，负责诊断加密新闻风险规则脚本。"
    "当前路由被选中，是因为 type_mismatch 数量高于阈值。"
    "本路由只优化一个目标：减少 primary risk type 选错，不做纯分数校准。"
    "risk_taxonomy 是唯一合法的风险类型集合。"
    "分析每个混淆对时，必须先读 sample_cases 里的新闻事实，再只比较 gold_scorer_context "
    "和 rule_scorer_context。gold_score_col 是本应胜出的正确 scorer；rule_score_col 是当前错误胜出的 scorer。"
    "如果某一侧 score_col 为空，说明该侧是无明显风险或没有 scorer，不要创造新 scorer；"
    "只诊断召回不足、误触发，或 no-risk/primary 选择逻辑。"
    "score value 和 score_diff 在本路由只能作为 scorer 竞争证据，不能作为分数校准 patch 目标。"
    "优先覆盖能解释大多数错配的高置信模式。允许为同一 scorer 系统性扩展精确关键词、上下文组合、"
    "否定 guard、辅助判断函数和窄 primary tie-break；规则应覆盖同类语义，而不是只记住样本原句。"
    "如果证据支持更全面的规则，代码合理增长是预期结果，不要为了保持代码短而省略必要分支。"
    "证据不足就 defer。输出必须是纯 JSON，不要 markdown。"
)


SCORE_CALIBRATION_SYSTEM_PROMPT = (
    "你是 v3 的分数校准 analyzer，负责诊断加密新闻风险规则脚本。"
    "当前路由被选中，是因为 type_mismatch 数量已经小于或等于阈值。"
    "本路由只优化一个目标：在保持 primary risk type 行为稳定的前提下改善 risk_score 校准。"
    "必须以 score_calibration_scope.target_groups 为主要证据，忽略 gold_primary_risk_type "
    "和 rule_primary_risk_type 不一致的 score_diff 样本。"
    "每个 target group 只校准对应风险类型，但要把类型证据与最终风险严重度分离：原 score_xxx 用于类型竞争，"
    "calibrate_score_xxx 用于主类型选定后的 risk_score。"
    "纯分数问题不能修改无关 scorer，也不能改全局 primary-type 选择逻辑。"
    "优先选择校准型本地改动，而不是粗暴扩关键词。证据不足就 defer。输出必须是纯 JSON，不要 markdown。"
)


ANALYZER_OUTPUT_SCHEMA = {
    "executive_summary": ["一句话总结本轮最推荐修什么"],
    "route": {
        "selected_prompt": "type_mismatch_repair / score_calibration",
        "active_stage": "复制 diagnostic_strategy.active_stage",
        "type_mismatch_rows": 0,
        "type_mismatch_score_stage_threshold": 80,
        "why_selected": "基于阈值闸门说明为什么选择这套路由",
    },
    "decision": {
        "primary_metric_to_improve": "primary_type_accuracy / score_diff_rmse / score_diff_mean / label_accuracy",
        "max_patch_count": 12,
        "coverage_target": "优先覆盖累计至少 85% 的高置信错误模式",
        "patch_budget_reason": "为什么这一批改动能在较少迭代内取得高收益",
        "stop_conditions": ["evidence_insufficient", "would_touch_unrelated_scorer", "would_increase_false_negative_risk"],
    },
    "type_mismatch_first_summary": {
        "active_stage": "type_mismatch_repair / score_calibration",
        "score_calibration_locked": True,
        "type_mismatch_rows": 0,
        "type_mismatch_score_stage_threshold": 80,
        "top_priority": "优先修的混淆对；如果当前是评分路由，则写 watchlist only",
        "why_first": "说明为什么类型修复优先，或为什么现在应该主修评分",
        "expected_metric_impact": ["primary_type_accuracy", "false_positive", "false_negative", "score_diff"],
    },
    "type_mismatch_pair_diagnosis": [
        {
            "gold_score_col": "score_regulatory",
            "gold_risk_type": "来自 risk_taxonomy 的风险类型名",
            "rule_score_col": "score_fraud",
            "rule_risk_type": "来自 risk_taxonomy 的风险类型名；为空时表示无明显风险",
            "count": 0,
            "evidence_case_ids": ["来自 sample_cases 的 news_id"],
            "sample_news_evidence": ["只写 sample_cases 中能看到的新闻事实，不要编造"],
            "gold_scorer_findings": {
                "likely_issue": "under_trigger / below_threshold / blocked_by_guard / ok / evidence_insufficient",
                "specific_code_area": "函数、关键词表、否定 guard、early-return guard 或阈值",
                "reason": "为什么正确类型没有胜出",
            },
            "rule_scorer_findings": {
                "likely_issue": "over_trigger / overly_broad_keyword / missing_guard / too_high_weight / ok / evidence_insufficient",
                "specific_code_area": "函数、关键词表、否定 guard、early-return guard 或阈值",
                "reason": "为什么错误类型胜出",
            },
            "competition_diagnosis": {
                "issue": "gold_too_weak / rule_too_strong / close_competition / primary_selection_logic / evidence_insufficient",
                "score_gap_evidence": "结合 avg_primary_vs_gold_gap 和 sample scorer_trace",
            },
            "recommended_code_change": {
                "action": "tighten_wrong_scorer / broaden_gold_scorer / adjust_primary_selection / adjust_threshold / keep",
                "target_functions": ["score_regulatory", "score_fraud"],
                "target_keyword_lists": ["KW_xxx", "NEG_xxx"],
                "implementation_detail": "给 patcher 的具体代码修改说明",
                "guardrail": "如何避免新的 FP/FN/type_mismatch/score_diff 回归",
                "validation": "改完后重点复测哪些混淆对和指标",
            },
            "patch_priority": "P0 / P1 / P2 / watchlist / defer",
            "evidence_confidence": "high / medium / low",
        }
    ],
    "score_calibration_after_type_fix": [
        {
            "stage_gate": "locked / unlocked",
            "target_score_col": "score_volatility",
            "target_function": "calibrate_score_volatility",
            "allowed_code_scope": "对应类型的 score_xxx 类型证据函数、calibrate_score_xxx 严重度函数及校准 dispatch",
            "direction": "overestimate / underestimate / mixed",
            "evidence_case_ids": ["来自 score_calibration_scope sample_cases 的 news_id"],
            "when_to_fix": "defer_until_type_mismatch_threshold / only_when_primary_type_already_correct",
            "recommendation": "具体分数校准建议；如果 locked 则写 defer 原因",
            "guardrail": "需要保持不回归的 primary type 和真实风险行为",
            "do_not_change": "原始 score_xxx、命中类型集合、primary-type 选择和无关 scorer",
            "priority": "P0 / P1 / P2 / P3 / defer",
        }
    ],
    "patch_plan_by_scorer": [
        {
            "priority": "P0 / P1 / P2 / defer",
            "stage": "type_mismatch_repair / score_calibration",
            "score_col": "score_fraud",
            "risk_type": "来自 risk_taxonomy 的风险类型名",
            "evidence_case_ids": ["news_id"],
            "reason_from_errors": "由哪个混淆对或评分校准分组触发",
            "allowed_change_type": "add_negative_guard / narrow_keyword / add_positive_trigger / adjust_weight / add_cap / adjust_threshold / adjust_primary_selection",
            "concrete_patch_instruction": "精确到函数、列表、分支、guard、权重、cap 或阈值",
            "expected_effect": "预期改善什么以及为什么",
            "guardrail": "不能回归的行为",
            "do_not_change": "明确不能碰的函数、列表或逻辑",
        }
    ],
    "metric_tradeoff_diagnosis": {
        "main_improved_metric": "primary_type_accuracy / score_diff_rmse / score_diff_mean / label_accuracy",
        "worsened_metrics_to_watch": ["false_negative", "false_positive", "primary_type_accuracy", "score_diff"],
        "optimization_warning": "不要为了优化单一指标而压掉真实风险或破坏 primary type",
    },
    "patterns": [],
    "scorer_diagnosis": [],
    "scorer_trace_diagnosis": [],
    "primary_type_diagnosis": [],
    "patch_plan": [],
    "do_not_patch": [
        {
            "target": "证据不足的 scorer 或规则区域",
            "reason": "为什么不建议改",
        }
    ],
    "confidence": "high / medium / low",
}


TYPE_MISMATCH_TASK_INSTRUCTIONS = [
    "确认 selected_prompt=type_mismatch_repair，且 score_calibration_locked=true。",
    "按 count、证据清晰度、预期收益、低回归风险排序；优先选择累计覆盖至少 85% 错配的高置信模式，最多 12 项。",
    "每个选中的混淆对，必须先引用新闻事实和 news_id，再提出代码改动。",
    "每个混淆对只检查 gold scorer、错误 rule scorer，以及必要的 primary-type tie 逻辑。",
    "根因必须归为：gold_under_trigger、rule_over_trigger、close_competition、primary_selection_logic、thresholding、evidence_insufficient 之一。",
    "如果错误 rule scorer 过宽，优先建议添加否定/上下文 guard 或收窄触发词，不要整体压低 scorer。",
    "如果 gold scorer 召回不足，优先建议精确正向证据或放松已证明过严的 guard，不要粗暴扩关键词。",
    "如果是 close competition，只有样本证据支持时才建议窄 tie-break 规则。",
    "不要输出任何纯 score_diff 校准计划；这类工作留给后续独立的 score_calibration 路由。",
    "每个 patch_plan_by_scorer 都必须包含一个目标 score_col、具体代码目标、evidence_case_ids、expected_effect 和 guardrail。",
    "多个混淆对能归纳为同一语义模式时，合并成可复用 helper、关键词组或 guard，并在同一轮批量修复。",
    "需要为单个混淆对触碰超过两个 scorer、重写框架逻辑，或缺少直接样本证据的改动必须 defer。",
]


SCORE_CALIBRATION_TASK_INSTRUCTIONS = [
    "确认 selected_prompt=score_calibration，且 score_calibration_locked=false。",
    "以 score_calibration_scope.target_groups 为主证据，不要从 type_mismatch 混淆对开始。",
    "按 row_count、方向一致性、误差幅度、低回归风险排序，最多选择 12 个 P0/P1/P2 分数组。",
    "只使用 gold_primary_risk_type == rule_primary_risk_type 的 score_diff 样本。",
    "每个分数组必须优先设计或修改 calibrate_score_xxx(raw_score, text)，保持原 score_xxx 和 primary 竞争分值不变。",
    "如果稳定高估，在 calibrate_score_xxx 中按证据强度、金额、百分比、事件状态或上下文分层降分/cap。",
    "如果稳定低估，在 calibrate_score_xxx 中按同类证据分层升分；不要提高用于类型竞争的原 scorer。",
    "如果同一类型同时有高估和低估，必须按可解释的子模式分支校准，禁止用一个全局常数平移。",
    "纯分数校准不得修改无关 scorer、全局阈值、primary-type 逻辑。",
    "每个 score_calibration_after_type_fix 都必须包含 target_score_col、target_function、direction、evidence_case_ids、recommendation、guardrail。",
    "每个 score_calibration_after_type_fix 都必须包含 target_score_col、target_function、direction、evidence_case_ids、recommendation、guardrail 和 do_not_change。",
    "不同 target_function 的 guardrail 不冲突时，优先在同一轮批量修复。",
]


def selected_route(payload: dict) -> str:
    strategy = payload.get("diagnostic_strategy")
    if not isinstance(strategy, dict):
        raise ValueError("payload.diagnostic_strategy must be an object")

    active_stage = strategy.get("active_stage")
    score_calibration_locked = strategy.get("score_calibration_locked")
    if not isinstance(active_stage, str):
        raise ValueError("diagnostic_strategy.active_stage must be a string")
    if not isinstance(score_calibration_locked, bool):
        raise ValueError("diagnostic_strategy.score_calibration_locked must be a boolean")

    route = active_stage.strip()
    if route == "type_mismatch_repair":
        if not score_calibration_locked:
            raise ValueError(
                "type_mismatch_repair requires score_calibration_locked=true"
            )
        return route
    if route == "score_calibration":
        if score_calibration_locked:
            raise ValueError(
                "score_calibration requires score_calibration_locked=false"
            )
        return route
    raise ValueError(f"unsupported diagnostic_strategy.active_stage: {route!r}")


def analyzer_output_schema(route: str) -> dict:
    schema = dict(ANALYZER_OUTPUT_SCHEMA)
    route_schema = dict(schema["route"])
    decision_schema = dict(schema["decision"])

    if route == "type_mismatch_repair":
        route_schema["selected_prompt"] = "type_mismatch_repair"
        decision_schema["primary_metric_to_improve"] = "primary_type_accuracy"
        schema.pop("score_calibration_after_type_fix", None)
        schema.pop("metric_tradeoff_diagnosis", None)
        type_summary = dict(schema["type_mismatch_first_summary"])
        type_summary["expected_metric_impact"] = [
            "type_mismatch_rows",
            "primary_type_accuracy",
            "false_positive",
            "false_negative",
        ]
        schema["type_mismatch_first_summary"] = type_summary
    elif route == "score_calibration":
        route_schema["selected_prompt"] = "score_calibration"
        route_schema.pop("type_mismatch_rows", None)
        route_schema.pop("type_mismatch_score_stage_threshold", None)
        decision_schema["primary_metric_to_improve"] = (
            "score_diff_rmse / score_diff_mean / label_accuracy"
        )
        decision_schema["stop_conditions"] = [
            "evidence_insufficient",
            "would_touch_raw_type_scorer",
            "would_touch_unrelated_calibrator",
        ]
        schema.pop("type_mismatch_first_summary", None)
        schema.pop("type_mismatch_pair_diagnosis", None)
        schema.pop("patch_plan_by_scorer", None)
        schema["metric_tradeoff_diagnosis"] = {
            "main_improved_metric": (
                "score_diff_rmse / score_diff_mean / label_accuracy"
            ),
            "worsened_metrics_to_watch": [
                "score_diff_rmse",
                "score_diff_mean",
                "label_accuracy",
            ],
            "optimization_warning": (
                "只评估最终风险分数校准效果，不修改原始类型证据。"
            ),
        }
    else:
        raise ValueError(f"unsupported analyzer route: {route!r}")

    schema["route"] = route_schema
    schema["decision"] = decision_schema
    return schema


def route_quality_bar(route: str) -> list[str]:
    shared = [
        "不要编造事实：所有判断必须来自 sample_cases、scorer_context、statistics 或 risk_taxonomy。",
        "优先做一批高覆盖、可解释、可回归验证的规则扩展。",
        "没有代码目标、证据和 guardrail 的建议必须降级为 defer。",
        "不要为了单一指标而隐藏真实风险或删除既有风险触发能力。",
    ]
    if route == "score_calibration":
        return [
            "评分校准必须通过主类型选定后的 calibrate_score_xxx 层实现，不得改变原始 scorer 竞争结果。",
            "每个分数组只能新增或修改匹配类型的 calibrator 和校准 dispatch。",
            "同时使用 direction_summary、distribution 和正反方向样本设计分段校准。",
            *shared,
        ]
    return [
        "类型错配 patch 必须能追溯到一个 gold_score_col -> rule_score_col 混淆对。",
        "本路由禁止纯分数修改，但允许为全面类型规则增加必要代码。",
        "每个混淆模式围绕相关 scorer 修改；允许提取被多个 scorer 复用的纯文本上下文 helper。",
        *shared,
    ]


def build_analyzer_user_prompt(payload: dict, route: str | None = None) -> str:
    selected = selected_route(payload)
    if route is not None and route != selected:
        raise ValueError(
            f"requested analyzer route {route!r} conflicts with selected route {selected!r}"
        )
    route = selected
    instructions = (
        SCORE_CALIBRATION_TASK_INSTRUCTIONS
        if route == "score_calibration"
        else TYPE_MISMATCH_TASK_INSTRUCTIONS
    )
    task_lines = "\n".join(f"{i}. {text}" for i, text in enumerate(instructions, start=1))
    quality_lines = "\n".join(f"- {text}" for text in route_quality_bar(route))
    schema_str = json.dumps(analyzer_output_schema(route), ensure_ascii=False, indent=2)
    payload_str = json.dumps(payload, ensure_ascii=False, indent=2)

    return f"""
请使用 v3 analyzer 路由进行诊断：{route}

任务顺序：
{task_lines}

质量要求：
{quality_lines}

输出契约：
- 只返回一个 JSON 对象，不要输出 markdown。
- 兼容字段即使为空也要保留为空数组。
- 证据不足时使用 priority=defer。
- 如果有足够高置信证据，尽量给出覆盖主要错误簇的具体 patch_plan_by_scorer，最多 12 项。
- patch_plan_by_scorer 必须按预期收益和安全性排序。

输出 JSON schema：
{schema_str}

诊断材料：
{payload_str}
""".strip()


def build_analyzer_messages(payload: dict) -> list[dict[str, str]]:
    route = selected_route(payload)
    system_prompt = (
        SCORE_CALIBRATION_SYSTEM_PROMPT
        if route == "score_calibration"
        else TYPE_MISMATCH_SYSTEM_PROMPT
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_analyzer_user_prompt(payload, route)},
    ]
