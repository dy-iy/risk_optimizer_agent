"""分析开发反馈，计算优化目标并提出统一类型的 LF 操作。"""

import json
import keyword


if __package__:
    from .task import DATASET, LABEL_DESCRIPTION, TASK_DESCRIPTION
else:
    from task import DATASET, LABEL_DESCRIPTION, TASK_DESCRIPTION


class AnalyzerResponseError(ValueError):
    """回复是合法 JSON，但不满足 Analyzer 结构；保留原文用于诊断。"""

    def __init__(self, message, raw_response, parsed_response):
        super().__init__(message)
        self.raw_response = raw_response
        self.parsed_response = parsed_response


def analyze(feedback, max_changes, ask, low_coverage_threshold=0.5, on_response=None, *,
            previous_response=None, error=None):
    """单次调用后直接解析和校验；纠正时附上原回复及错误，保留原始上下文。"""
    if max_changes < 1:
        raise ValueError("LF 改动上限至少为 1")
    if not 0 <= low_coverage_threshold <= 1:
        raise ValueError("低覆盖率阈值必须在 0 到 1 之间")
    train_coverage = feedback["train_statistics"]["lf_coverage"]
    valid_coverage = feedback["valid_metrics"]["lf_coverage"]
    is_low_coverage = min(train_coverage, valid_coverage) < low_coverage_threshold
    context = {
        "train_lf_coverage": train_coverage,
        "valid_lf_coverage": valid_coverage,
        "low_coverage_threshold": low_coverage_threshold,
        "is_low_coverage": is_low_coverage,
        "optimization_goal": "increase_coverage" if is_low_coverage else "improve_lf_quality",
        "max_changes": max_changes,
    }
    instructions = f"""你负责根据开发反馈优化 {DATASET} 弱监督 LF，提出方案，不直接写代码。
任务：{TASK_DESCRIPTION}
验证指标、样例预测及错例分组来自简单投票（simple_vote）；并列最多票或全部弃权时预测为 -1。
标签：{LABEL_DESCRIPTION}。完整固定验证集用于开发反馈；训练样例没有金标，不得把猜测当作已确认标签，也不得使用测试信息。
lf_names 是完整 LF 名单；样例 votes 仅列非弃权票，未列出的 LF 均视为 -1，全部弃权时 votes 为 {{}}。
coverage_context 已由程序计算，包含本轮覆盖目标、阈值和操作上限；按该目标分析，不要在输出中重复生成这些字段。
从 add（新增独立模式）、modify（修正或扩展已有模式）、delete（移除有害或冗余规则）中选择本轮唯一 action。
summary 概括本轮优先解决的缺口、选择该操作的原因及主要误标风险。
低覆盖时优先增加正确覆盖，不预设必须新增；若应先删除有害规则，在 summary 说明优先安排，删除本身不会增加覆盖。
如果 feedback 含 rejected_candidates，说明这些修改未通过弱标签净收益门槛，未应用到当前 LF；请勿原样重复，改用不同思路。
operations 只列本轮实际执行的 1 至 {max_changes} 项操作，全部采用顶层 action，不在每项内重复 action；删除后至少保留 3 个 LF。
每项给出 target 函数名、reason、非空字符串数组 evidence，以及 plan。evidence 可引用给定样例、统计或代码，不强制引用金标样例。
plan 描述具体匹配思路、应保留的正确行为和弃权边界；新增模式应避免重复已有规则，不用默认判为某一类凑覆盖率。
每个 LF 只读取文本正文，未命中时弃权；规则须可复用，不按样本编号或整条文本查表。
每个 LF 的增删改各算一项，不能通过修改共享常量、模块导入或辅助函数绕过上限。
只返回以下结构的 JSON，不添加其他字段：
{{"action":"modify", "summary":"本轮优先解决的缺口及主要风险", "operations":[{{
  "target":"lf_example", "reason":"为什么修改", "evidence":["给定样例、统计或代码依据"],
  "plan":"具体匹配思路、需要保留的行为和弃权边界"
}}]}}
"""
    instructions += (
        "JSON 字符串中的反斜杠必须双写，尤其是正则表达式："
        r"\d、\s、\b 在 JSON 源文本中应写成 \\d、\\s、\\b。不要输出 Markdown 代码围栏。"
    )
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps({"coverage_context": context, "feedback": feedback}, ensure_ascii=False)},
    ]
    if error is not None:
        messages.extend([
            {"role": "assistant", "content": previous_response},
            {"role": "user", "content": (
                f"上次回复未通过校验：\n{error}\n"
                "请依据原始开发反馈修正，保留已合法的内容，返回完整 JSON。"
                "仍须遵守原操作预算、覆盖目标和输出契约，不要输出修复说明。"
            )},
        ])
    reply = ask(messages, json_mode=True)
    if on_response is not None:
        on_response(reply)
    analysis = json.loads(reply)
    try:
        return validate_analysis(analysis, feedback, max_changes)
    except ValueError as validation_error:
        raise AnalyzerResponseError(str(validation_error), reply, analysis) from validation_error


def validate_analysis(analysis, feedback, max_changes):
    """集中校验输出契约和 LF 操作，不补写或改动模型回复。"""
    if not isinstance(analysis, dict):
        raise ValueError(f"$: 顶层必须是对象，实际为 {type(analysis).__name__}")
    if set(analysis) != {"action", "summary", "operations"}:
        fields = {"action", "summary", "operations"}
        raise ValueError(f"$: 缺少字段 {sorted(fields - set(analysis))}；多余字段 {sorted(set(analysis) - fields)}")
    action = analysis["action"]
    if not isinstance(action, str) or action not in {"add", "modify", "delete"}:
        raise ValueError(f"action={action!r}: 必须为 add、modify 或 delete")
    if not isinstance(analysis["summary"], str) or not analysis["summary"].strip():
        raise ValueError(f"summary={analysis['summary']!r}: 必须是非空字符串")
    operations = analysis.get("operations")
    if not isinstance(operations, list):
        raise ValueError(f"operations: 必须是数组，实际为 {type(operations).__name__}")
    if not 1 <= len(operations) <= max_changes:
        raise ValueError(f"operations: 提出了 {len(operations)} 项操作，必须为 1 至 {max_changes} 项")
    existing = set(feedback["lf_names"])
    targets = set()
    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(operation, dict):
            raise ValueError(f"{path}: 必须是对象，实际为 {type(operation).__name__}")
        fields = {"target", "reason", "evidence", "plan"}
        if set(operation) != fields:
            raise ValueError(f"{path}: 缺少字段 {sorted(fields - set(operation))}；多余字段 {sorted(set(operation) - fields)}")
        target = operation["target"]
        if (not isinstance(target, str) or not target.isidentifier()
                or keyword.iskeyword(target)):
            raise ValueError(f"{path}.target={target!r}: 必须是有效的 Python 函数名，不能使用关键字")
        if target in targets:
            raise ValueError(f"{path}.target={target!r}: 与前面的操作目标重复")
        targets.add(target)
        if action == "add" and target in existing:
            raise ValueError(f"{path}.target={target!r}: 已在当前 LF 列表中，add 必须使用新函数名")
        if action != "add" and target not in existing:
            raise ValueError(f"{path}.target={target!r}: 不在当前 LF 列表中，{action} 必须指向已有 LF")
        for field in ("reason", "plan"):
            if not isinstance(operation.get(field), str) or not operation[field].strip():
                raise ValueError(f"{path}.{field}={operation[field]!r}: 必须是非空字符串")
        evidence = operation.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{path}.evidence={evidence!r}: 必须是非空字符串数组")
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{path}.evidence[{evidence_index}]={item!r}: 必须是非空字符串")
    if action == "delete" and len(existing) - len(operations) < 3:
        raise ValueError(f"operations: 当前有 {len(existing)} 个 LF，删除 {len(operations)} 个后不足 3 个")
    return analysis
