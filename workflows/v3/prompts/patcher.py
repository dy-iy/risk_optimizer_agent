import json


TYPE_MISMATCH_PATCHER_SYSTEM_PROMPT = (
    "你是 v3 类型错配修复阶段专用的 Python 规则脚本 patcher。"
    "当前唯一目标是减少 primary risk type 错配。"
    "只执行 type_mismatch_pair_diagnosis 和 type_mismatch_repair 的 patch_plan_by_scorer。"
    "禁止执行 score_calibration_after_type_fix，禁止为了拟合 gold_risk_score 调整纯分数。"
    "允许围绕相关 scorer 增加全面的关键词组、上下文组合、否定 guard、文本 helper 和窄 tie-break。"
    "所有改动必须能追溯到具体 gold_score_col -> rule_score_col 混淆对。"
    "输出必须是纯 JSON，不要 markdown。"
)


SCORE_CALIBRATION_PATCHER_SYSTEM_PROMPT = (
    "你是 v3 风险分数校准阶段专用的 Python 规则脚本 patcher。"
    "当前唯一目标是在 primary type 已经正确的样本上降低风险分数 MAE/RMSE。"
    "只执行 score_calibration_scope.target_groups 和 score_calibration_after_type_fix。"
    "禁止修改原 score_xxx 类型证据、raw_scores、命中类型集合和 primary-type 选择。"
    "必须在主类型确定后，通过对应 calibrate_score_xxx(raw_score, text) 校准最终 risk_score。"
    "如果同类样本同时存在高估和低估，必须按可解释上下文分支校准，禁止统一常数平移。"
    "输出必须是纯 JSON，不要 markdown。"
)


SHARED_PATCHER_OBJECTIVES = [
    "输出 ordered_patches 精确替换操作；每项提供在当前脚本中唯一出现的 old_code 和完整 new_code。",
    "禁止输出 old_code 与 new_code 相同的空操作；如果分析建议已经被当前脚本实现，直接放入 deferred_items 并注明 already_implemented。",
    "每轮必须至少产生一个尚未实现、由证据支持、会改变风险类型或风险分数执行逻辑的补丁；纯注释、格式、类型标注或空替换不算有效版本。",
    "改代码前先读取 diagnostic_strategy.active_stage 和 score_calibration_locked。",
    "每轮最多应用 12 个高置信 P0/P1/P2 改动，优先覆盖累计至少 85% 的主要错误模式。",
    "只应用同时具备 concrete_patch_instruction、evidence_case_ids 或样本证据、代码目标、expected_effect、guardrail 的建议。",
    "不要重写 CSV 协议、输入输出列或入口行为。",
    "保持原有返回 schema、环境变量、CSV_PATH/OUT_PATH 行为，以及只依赖标准库和 pandas 的策略。",
    "不要修改 evidence_insufficient、evidence_confidence=low、watchlist 或 defer 标记的区域。",
    "如果 analyzer 建议互相冲突，选择高收益且安全的子集；仍不明确就 defer。",
    "patch_report 必须把每个代码改动映射到 analyzer 证据，并说明应该复测什么。",
    "如果存在 candidate_validation_feedback，必须针对上一候选的回归原因重新设计补丁，不能重复被拒方案。",
]


TYPE_MISMATCH_PATCHER_OBJECTIVES = [
    "断言 active_stage=type_mismatch_repair 且 score_calibration_locked=true；否则拒绝生成补丁。",
    "读取 type_mismatch_rows 和 type_mismatch_score_stage_threshold，确认类型修复阶段仍处于启用状态。",
    "type_mismatch_repair 阶段中，每个 patch 必须能追溯到具体 gold_score_col -> rule_score_col 混淆对。",
    "type_mismatch_repair 阶段中，围绕相关 scorer、本地关键词/否定 guard、可复用上下文 helper，"
    "或必要的窄 primary-type tie 逻辑修改。",
    "如果混淆对的 gold_score_col 或 rule_score_col 为空，不要创造新 scorer；只能修已有 scorer 行为或 no-risk/primary 决策逻辑。",
    "错误 scorer 过宽时优先增加上下文否定 guard；正确 scorer 召回不足时增加有直接证据支持的语义组合。",
    "禁止新增或修改 calibrate_score_xxx，禁止把 score_diff 校准建议混入本阶段。",
]


SCORE_CALIBRATION_PATCHER_OBJECTIVES = [
    "断言 active_stage=score_calibration 且 score_calibration_locked=false；否则拒绝生成补丁。",
    "只使用 gold_primary_risk_type == rule_primary_risk_type 且分差达到阈值的 target_groups。",
    "score_calibration 阶段中，保留 score_xxx 作为类型证据；新增或修改 calibrate_score_xxx(raw_score, text)，"
    "并在 score_all_risks 选定 primary type 后通过 calibration dispatch 得到最终 risk_score。",
    "如果脚本尚无校准层，本轮先建立结构：未命中的类型使用 identity calibration，目标类型使用独立 "
    "calibrate_score_xxx；risk、rule_label 使用校准后严重度，rule_types、rule_primary_type 和各 score_xxx "
    "输出继续使用原始类型证据分值。",
    "纯分数校准不得改变 raw_scores、命中类型集合、primary-type 选择、CSV 字段或入口行为。",
    "使用 direction_summary、distribution 及高估/低估样本设计分段严重度映射。",
    "禁止修改类型关键词、类型 guard、TYPE_THRESHOLD 和 primary tie-break。",
]


COMMON_PATCHER_OUTPUT_SCHEMA = {
    "patch_version": "v3-candidate",
    "summary": ["一句话总结本轮 patch 重点"],
    "patch_budget": {
        "max_patch_count": 12,
        "applied_patch_count": 0,
        "budget_strategy": "按错误覆盖率批量应用高置信规则扩展，以减少迭代次数",
    },
    "deferred_items": [
        {
            "target": "score_xxx / confusion pair / keyword list",
            "reason": "阶段锁定、证据弱、范围过宽、冲突或低置信",
        }
    ],
    "changes": [
        {
            "target": "函数、关键词表、guard 或校准函数",
            "reason": "为什么改",
            "action": "具体改了什么",
            "risk": "low / medium / high",
            "guardrail": "如何限制回归",
            "validation": "改完后需要复测哪些指标",
        }
    ],
    "validation_notes": ["改完后需要重点复测的内容"],
    "ordered_patches": [
        {
            "target": "函数、常量或代码块",
            "old_code": "从当前脚本逐字复制、且只出现一次的完整旧代码",
            "new_code": "替换后的完整代码；可显著增长，但必须保持可运行",
        }
    ],
    "full_code": "仅兼容回退；正常情况留空，避免每轮重输完整脚本",
}


TYPE_MISMATCH_PATCHER_OUTPUT_SCHEMA = {
    **COMMON_PATCHER_OUTPUT_SCHEMA,
    "stage_gate": {
        "active_stage": "type_mismatch_repair",
        "score_calibration_locked": True,
        "type_mismatch_rows": 0,
        "type_mismatch_score_stage_threshold": 80,
        "score_calibration_action": "deferred",
    },
    "applied_type_mismatch_fixes": [
        {
            "gold_score_col": "score_regulatory",
            "rule_score_col": "score_fraud",
            "evidence_case_ids": ["news_id"],
            "root_cause": "rule_over_trigger / gold_under_trigger / close_competition / primary_selection_logic",
            "code_targets": ["score_fraud", "NEG_FRAUD", "score_all_risks primary selection"],
            "change": "具体做了什么修改",
            "guardrail": "如何避免新的 FN/type_mismatch/score_diff 回归",
        }
    ],
    "deferred_score_fixes": [
        {
            "score_col": "score_volatility",
            "reason": "类型阶段严格锁定评分校准",
        }
    ],
}


SCORE_CALIBRATION_PATCHER_OUTPUT_SCHEMA = {
    **COMMON_PATCHER_OUTPUT_SCHEMA,
    "stage_gate": {
        "active_stage": "score_calibration",
        "score_calibration_locked": False,
        "score_calibration_action": "applied",
    },
    "applied_score_calibration_fixes": [
        {
            "score_col": "score_volatility",
            "target_function": "calibrate_score_volatility",
            "evidence_case_ids": ["news_id"],
            "direction": "overestimate / underestimate / mixed",
            "change": "只有 unlocked 时才做的具体评分校准修改",
            "guardrail": "如何保持 primary type 和无关 scorer 不受影响",
        }
    ],
}


REPAIR_SYSTEM_PROMPT = (
    "你是 Python 语法修复助手。"
    "只修导致编译失败的问题，不改变业务逻辑。"
    "输出必须是纯 JSON。"
)


REPAIR_OUTPUT_SCHEMA = {
    "repair_summary": ["修复了什么"],
    "full_code": "修复后的完整 Python 代码字符串",
}


def selected_patcher_route(analysis_data: dict) -> str:
    strategy = analysis_data.get("diagnostic_strategy")
    if not isinstance(strategy, dict):
        raise ValueError("analysis_data.diagnostic_strategy must be an object")

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


def build_patch_prompt(source_code: str, analysis_data: dict) -> tuple[str, str]:
    route = selected_patcher_route(analysis_data)
    if route == "score_calibration":
        system_prompt = SCORE_CALIBRATION_PATCHER_SYSTEM_PROMPT
        objectives = [
            *SHARED_PATCHER_OBJECTIVES,
            *SCORE_CALIBRATION_PATCHER_OBJECTIVES,
        ]
        output_schema = SCORE_CALIBRATION_PATCHER_OUTPUT_SCHEMA
        route_contract = (
            "只实现 score_calibration_scope.target_groups 与 "
            "score_calibration_after_type_fix 中的校准计划；"
            "不得修改类型证据、原始 scorer、命中类型集合或 primary-type 选择。"
        )
    else:
        system_prompt = TYPE_MISMATCH_PATCHER_SYSTEM_PROMPT
        objectives = [
            *SHARED_PATCHER_OBJECTIVES,
            *TYPE_MISMATCH_PATCHER_OBJECTIVES,
        ]
        output_schema = TYPE_MISMATCH_PATCHER_OUTPUT_SCHEMA
        route_contract = (
            "只实现 type_mismatch_pair_diagnosis 与 "
            "type_mismatch_repair 阶段的 patch_plan_by_scorer；"
            "不得实现任何风险分数校准建议。"
        )

    analysis_str = json.dumps(analysis_data, ensure_ascii=False, indent=2)
    objective_lines = "\n".join(f"- {item}" for item in objectives)
    schema_str = json.dumps(output_schema, ensure_ascii=False, indent=2)

    user_prompt = f"""
你会收到两份输入：
1. 当前版本的完整 Python 规则脚本
2. v3 analyzer 输出的 analysis_llm.json

当前唯一 patch 路由：{route}
路由契约：{route_contract}

Patch 流程：
- 路由已经由程序硬选择，不得切换、混合或越过当前阶段。
- 只选择符合当前唯一路由、且 analyzer 支持的 P0/P1/P2 项。
- 当目标不冲突且证据充分时，最多应用 12 个改动，并优先覆盖主要错误簇。
- 不支持、越界、冲突或证据不足的建议不要改代码，写入 deferred_items。

目标：
{objective_lines}

输出契约：
- 只返回一个 JSON 对象，不要 markdown。
- ordered_patches 是首选输出；old_code 必须从当前脚本逐字复制并且唯一匹配。
- new_code 应包含该目标替换后的完整代码块，允许增加全面规则和 helper。
- old_code 与 new_code 必须存在实际差异；已实现或无需修改的建议不得伪装成替换操作。
- full_code 正常留空，仅在无法用唯一替换表达时作为兼容回退。
- 不要在代码里加入描述 prompt 或流程的注释。
- 除非 analyzer 给出具体证据说明某段行为导致错误，否则不要删除既有行为。

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
""".strip()

    return system_prompt, user_prompt


def build_repair_prompt(bad_code: str, compile_error: str) -> tuple[str, str]:
    schema_str = json.dumps(REPAIR_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)

    user_prompt = f"""
下面这份 LLM 生成的 Python 代码编译失败。
请只修复语法或编译错误，尽量不要改变业务逻辑。

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
