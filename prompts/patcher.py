from __future__ import annotations

import json


PATCHER_SYSTEM_PROMPT = (
    "你是一个专门给加密货币新闻风险规则系统打补丁的 Python 工程师。"
    "你会根据 LLM 错误分析报告，直接产出一份完整、可运行、结构尽量稳定的新脚本。"
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
    "如果 analysis 证据不足，就少改，不要硬改。",
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
        }
    ],
    "validation_notes": ["注意点1", "注意点2"],
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


def build_patch_prompt(source_code: str, analysis_data: dict) -> tuple[str, str]:
    analysis_str = json.dumps(analysis_data, ensure_ascii=False, indent=2)
    objective_lines = "\n".join(f"- {item}" for item in PATCHER_OBJECTIVES)
    schema_str = json.dumps(PATCHER_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)

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
