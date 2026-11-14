"""组织开发反馈，先决策 LF 操作类型，再分析具体规则。"""

import json
import random

import numpy as np


class AnalyzerResponseError(ValueError):
    """回复是合法 JSON，但不满足 Analyzer 结构；保留原文用于诊断。"""

    def __init__(self, message, raw_response, parsed_response):
        super().__init__(message)
        self.raw_response = raw_response
        self.parsed_response = parsed_response


def _json_value(value):
    """把 numpy 数值转成可直接保存、发送的 JSON 值。"""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"无法序列化 {type(value).__name__}")


def parse_analysis_json(reply):
    """只补齐 JSON 中不合法的反斜杠转义，其余格式错误仍然报错。"""
    text, original_error = reply, None
    # 每次只修正解析器指出的一处，避免改坏已有的合法转义。
    for _ in range(reply.count("\\") + 1):
        try:
            result = json.loads(text)
        except json.JSONDecodeError as error:
            if original_error is None:
                original_error = error
            if error.msg != "Invalid \\escape":
                # 保留原始回复和原始位置，供错误记录使用。
                raise original_error from None
            text = text[:error.pos] + "\\" + text[error.pos:]
        else:
            if text != reply:
                print("Analyzer JSON 中不合法的反斜杠已自动转义。", flush=True)
            return result
    raise original_error


def build_feedback(result, current_code, version_name, max_examples=10, seed=42):
    """五组样例各抽最多 max_examples 条；不读取任何测试集信息。"""
    if max_examples < 0:
        raise ValueError("每组反馈样例数不能为负数")
    names = result["lf_names"]
    valid = result["valid"]
    train = result["train"]
    valid_L = np.asarray(valid["L"])
    train_L = np.asarray(train["L"])
    predictions = np.asarray(valid["predictions"])
    gold = np.asarray([
        {"ham": 0, "spam": 1}.get(row["label"], row["label"])
        for row in valid["rows"]
    ], dtype=int)
    # Random 对字符串种子的处理稳定，不依赖每次进程随机化的 hash()。
    rng = random.Random(f"{seed}:{version_name}")

    def examples(rows, matrix, mask, with_gold=False):
        indices = np.flatnonzero(mask).tolist()
        chosen = rng.sample(indices, min(max_examples, len(indices)))
        selected = []
        for index in chosen:
            row = rows[index]
            item = {
                "id": row["id"],
                "text": row["text"],
                "votes": dict(zip(names, matrix[index].tolist())),
            }
            if with_gold:
                item["gold"] = int(gold[index])
                item["prediction"] = int(predictions[index])
            selected.append(item)
        return selected

    conflict = np.any(train_L == 0, axis=1) & np.any(train_L == 1, axis=1)
    groups = {
        "valid_wrong": examples(
            valid["rows"], valid_L,
            (predictions != -1) & (predictions != gold), True,
        ),
        "valid_abstained": examples(
            valid["rows"], valid_L, predictions == -1, True,
        ),
        "valid_correct": examples(
            valid["rows"], valid_L, predictions == gold, True,
        ),
        "train_uncovered": examples(
            train["rows"], train_L, np.all(train_L == -1, axis=1),
        ),
        "train_conflicting": examples(
            train["rows"], train_L, conflict,
        ),
    }
    # 只白名单提取开发反馈；不把整个 metrics 或 result 发给模型。
    feedback = {
        "version": version_name,
        "label_values": {"-1": "abstain", "0": "ham", "1": "spam"},
        "lf_code": current_code,
        "lf_names": names,
        "valid_metrics": result["metrics"]["valid"],
        "valid_class_coverage": {
            label: {
                "n_samples": int((gold == value).sum()),
                "n_covered": int(((gold == value) & (valid_L != -1).any(axis=1)).sum()),
                "n_uncovered": int(((gold == value) & (valid_L == -1).all(axis=1)).sum()),
            }
            for label, value in (("ham", 0), ("spam", 1))
        },
        "train_statistics": result["metrics"]["train"],
        "lf_quality": result["lf_quality"],
        "examples": groups,
    }
    return json.loads(json.dumps(feedback, ensure_ascii=False, default=_json_value))


def analyze(feedback, max_changes, ask, low_coverage_threshold=0.5, on_response=None):
    """同一次 LLM 调用完成操作决策和具体规则分析，返回统一 JSON。"""
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
    instructions = f"""你负责分析 YouTube 弱监督 LF，而不是直接写代码。
在同一次回复中按顺序完成两步：先在 decision 中决定本轮操作类型和原因，
再在 overall_analysis 和 operations 中分析具体问题、给出该类型的规则操作建议。
根据当前 LF、验证集完整指标、按类别的未覆盖数量、样例和训练集覆盖/冲突统计，
从 add（新增 LF）、modify（修改 LF）、delete（删除 LF）中选择本轮唯一操作类型。
训练集或验证集的 LF 覆盖率低于给定阈值时，本轮优先目标是提高 LF 集合的整体覆盖率，
即让更多文本至少获得一条 LF 的非弃权投票；结合证据选择操作，不预设必须新增 LF。
可以通过 modify 扩展已有 LF 的同义表达、句式或合理匹配边界，也可以通过 add 补充独立模式。
重点关注未覆盖数量多的类别，兼顾正确覆盖与误标风险，尽量保留原本正确的匹配行为。
新增 LF 应带来新的覆盖，避免与现有规则重复；不能用默认全部判为某一类等方式凑覆盖率。
modify 用于修正有证据的系统性错误，或现有 LF 能清晰扩展同一模式的情况。
delete 用于移除有证据的有害或冗余 LF；删除后至少保留 3 个 LF。
低覆盖时无论选择何种操作，都必须在 coverage_plan 中说明覆盖不足的原因、
所选操作准备如何改善覆盖，以及如何控制误标；选择 modify 不需要解释为何没有新增。
删除 LF 本身不会提高集合的 LF 覆盖率；若有证据表明应先删除有害规则，
必须在 coverage_plan 中解释这一优先安排，并说明后续扩展覆盖的方向，不能声称删除本身增加覆盖。
只使用给定开发反馈和无金标训练统计；不得使用测试信息，也不能把训练样例猜测当成金标。
operations 中每项 action 必须与本次回复中 decision.action 一致，同一轮不得混用操作类型。
若本轮是 add，优先覆盖尚未命中的独立模式，并关注验证集中未覆盖数量多的类别；避免重复已有 LF。
同时考虑新增正确覆盖和误标风险，不以已覆盖准确率 100% 为唯一目标。
任务：根据给定 LF、固定验证集指标和样例、训练集覆盖/冲突统计，提出小步优化建议。
标签为 -1=弃权、0=ham、1=spam。验证集为 WRENCH 官方固定的 120 条（ham 64、spam 56），用于开发反馈。
任务是识别YouTube评论中的垃圾推广、自我引流、诈骗和无关广告，ham是正常评论。
负面评价、粗俗措辞、非英语或表情符号本身不等于spam；区分讨论视频和推广自己的频道。
正文保留原始多语言、URL、HTML片段和换行，不假设只有英文。
训练样例没有真实标签，不能把预测或你的猜测当金标，不能称其为已确认错例。
这里只提供开发反馈，不得要求或使用测试集信息。
每个 LF 只能根据一条评论正文进行规则判断，未命中时弃权。
规则应概括可复用的文本模式，不能抄录整条评论建立标签查表，也不能按样本编号判断。
只允许执行本轮选定类型的操作，保留至少 3 个 LF；一次建议执行 1 至 {max_changes} 项操作。
每个 LF 的新增、修改、删除各算一项，不能修改共享常量、模块导入或辅助函数来绕过上限。
整体分析可以讨论多个问题，但 operations 只放本轮实际建议执行的操作。
只返回 JSON：
{{"decision":{{"action":"add 或 modify 或 delete", "reason":"本轮选择的原因",
  "evidence":["给定统计、样例或代码依据"], "coverage_plan":"低覆盖时的改善覆盖方案及误标控制，其他情况可留空"}},
  "overall_analysis":"整体问题分析", "operations":[{{
  "action":"add 或 modify 或 delete", "target":"LF 函数名",
  "reason":"为什么需要此项改动", "evidence":["已给出的样例编号、统计或代码依据"],
  "idea":"具体的规则修改思路"
}}]}}
evidence 可引用统计或代码分析，不要求每项都有金标样例编号。
JSON 顶层必须同时包含 decision、overall_analysis、operations 三个字段。
overall_analysis 必须是整体分析的字符串，不是对象或列表，也不能放在 decision 内部。
decision 为对象，operations 为数组；JSON 模式不会代替这些字段要求，请逐项检查后返回。
"""
    instructions += (
        "JSON 字符串中的反斜杠必须双写，尤其是正则表达式："
        r"\d、\s、\b 在 JSON 源文本中应写成 \\d、\\s、\\b。不要输出 Markdown 代码围栏。"
    )
    reply = ask([
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps({"coverage_context": context, "feedback": feedback}, ensure_ascii=False)},
    ], json_mode=True)
    if on_response is not None:
        on_response(reply)
    analysis = parse_analysis_json(reply)
    try:
        return validate_analysis(analysis, feedback, max_changes, context)
    except ValueError as error:
        raise AnalyzerResponseError(str(error), reply, analysis) from error


def validate_analysis(analysis, feedback, max_changes, context):
    """只校验回复结构和 LF 操作，不伪造或补齐缺失的分析。"""
    if not isinstance(analysis, dict):
        raise ValueError(f"Analyzer 的 JSON 顶层必须是对象，实际为 {type(analysis).__name__}")
    if "overall_analysis" not in analysis:
        keys = ", ".join(list(analysis)[:12]) or "（空对象）"
        raise ValueError(f"Analyzer 缺少顶层 overall_analysis 字段；实际顶层字段：{keys}")
    if not isinstance(analysis["overall_analysis"], str):
        raise ValueError("Analyzer 的 overall_analysis 必须是字符串，实际为 "
                         + type(analysis["overall_analysis"]).__name__)
    decision = analysis.get("decision")
    if not isinstance(decision, dict) or decision.get("action") not in {"add", "modify", "delete"}:
        raise ValueError("Analyzer 必须在 decision 中选择 add、modify 或 delete")
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise ValueError("Analyzer 必须说明本轮操作的选择原因")
    if not isinstance(decision.get("evidence"), (str, list)) or not decision["evidence"]:
        raise ValueError("Analyzer 缺少决策依据 evidence")
    if decision["action"] == "delete" and len(feedback["lf_names"]) <= 3:
        raise ValueError("当前只有 3 个 LF，不能选择删除")
    if context["is_low_coverage"]:
        if not isinstance(decision.get("coverage_plan"), str) or not decision["coverage_plan"].strip():
            raise ValueError("低覆盖率时必须在 coverage_plan 中说明如何改善覆盖并控制误标")
    operations = analysis.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= max_changes:
        raise ValueError(f"Analyzer 必须提出 1 至 {max_changes} 项 LF 操作")
    existing = set(feedback["lf_names"])
    targets = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("Analyzer 的每项操作必须为 JSON 对象")
        action, target = operation.get("action"), operation.get("target")
        if action not in {"add", "modify", "delete"}:
            raise ValueError("LF 操作只能为 add、modify 或 delete")
        if action != decision["action"]:
            raise ValueError("operations 的操作类型必须与 decision.action 一致")
        if not isinstance(target, str) or not target.isidentifier() or target in targets:
            raise ValueError("LF 目标必须是有效且不重复的函数名")
        targets.add(target)
        if (action == "add" and target in existing) or (action != "add" and target not in existing):
            raise ValueError(f"操作 {action} 的目标函数不符合当前 LF 列表：{target}")
        for field in ("reason", "idea"):
            if not isinstance(operation.get(field), str) or not operation[field].strip():
                raise ValueError(f"操作 {target} 缺少 {field}")
        evidence = operation.get("evidence")
        if not isinstance(evidence, (str, list)) or not evidence:
            raise ValueError(f"操作 {target} 缺少支持依据 evidence")
    if decision["action"] == "delete" and len(existing) - len(operations) < 3:
        raise ValueError("删除后必须至少保留 3 个 LF")
    analysis["decision"] = {**decision, "coverage_context": context}
    return analysis
