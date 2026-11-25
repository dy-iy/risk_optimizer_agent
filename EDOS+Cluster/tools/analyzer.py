"""根据程序确定的目标与允许操作，分析 LF 修改方案。"""

import json
import keyword
import re

if __package__:
    from .task import DATASET, TASK_DESCRIPTION
else:
    from task import DATASET, TASK_DESCRIPTION


class AnalyzerResponseError(ValueError):
    """保留未通过业务校验的模型原回复，供工作流纠正一次。"""

    def __init__(self, message, raw_response, parsed_response):
        super().__init__(message)
        self.raw_response = raw_response
        self.parsed_response = parsed_response


def analyze(feedback, ask, on_response=None, *, previous_response=None, error=None):
    context = feedback["strategy_context"]
    action = context["selected_action"]
    if action == "add":
        operation_instructions = """本轮提供 feedback.cluster 中的一组 discovery_gold 案例、异标签相似反例及无标签训练支持。
center_examples/diverse_examples/boundary_examples 或 random_examples 都是目标标签金标案例；hard_negative_examples 是另一标签的真实反例。
本轮新增 LF 以 cluster.target_label 为命中标签，其他情形弃权；相似反例用来限制触发条件，不能据此另造另一标签规则。
evidence 引用必须写成 [gold:真实ID]、[negative:真实ID]、[train:真实ID]，不得引用未提供的编号。
每项 evidence 合计至少引用两条不同的目标金标案例；提供反例时至少引用一条反例，并说明判别差异。
新增 LF 的核心要求是泛化，禁止针对一条样例生成一条 LF，也禁止按样例顺序逐条补丁：
1. 先横向比较多条样例，归纳共享的语言结构、语义关系或表达模式族，再选择少量互补的模式。同一模式的词形、同义表达、标点或语序变体应合并为一条 LF，不拆成多个近似规则。
2. 结合 train_support_examples 及训练相似支持量说明潜在分布支持。相似数量不等于 LF 实际命中数，更不等于正确覆盖；训练例不能证明标签正确。不能把自己编造的变体算作观测证据。
3. 每项 plan 必须说明：共同匹配结构和输出标签；如何覆盖未见文本的合理词形、同义表达、距离或语序变化；对应的训练支持；不应触发的相似文本及弃权边界。泛化不能靠无限扩大词表或放宽到无关语义。
4. 不使用样例专有的人名、ID、偶然数字、罕见短语组合、长句片段或整句正则作为判断条件；不拼接若干单例特征伪装成通用规则。未命中应弃权，禁止无依据地把剩余样本全判为某一类。
5. 考虑局部否定、引用、转述、反讽及说话者立场；不要因为整段任意位置出现 not、said 或引号就一律弃权。给出同义表达仍触发、相似反例应弃权的机制，不能只写“扩大覆盖”或“具有泛化能力”。
6. 不为凑满操作预算生成规则；证据薄弱时减少操作数、合并重复模式，不提交只支持单个例子的方案。不能将单个命中或未经执行的方案宣称为已验证的高准确率或高覆盖率。"""
    else:
        operation_instructions = """本轮只提供 valid_wrong、valid_correct 和 train_conflict 三组，每组最多 n 条；不足时取全部，不补齐、不重复。
结合 valid_wrong 定位误标条件，用 valid_correct 说明必须保留的正确行为；train_conflict 只说明 LF 投票矛盾，不能当作训练金标。
修改应针对共享的错误机制和上下文边界，避免按单个错例添加特判；plan 写明纠错方式、保留的正确模式和应弃权的相似表达。"""
    instructions = f"""你负责分析 {DATASET} 弱监督反馈，给出 LF 修改方案，不写代码。
任务：{TASK_DESCRIPTION}
本轮目标：{context['optimization_goal']}
当前状态允许操作：{'、'.join(context['allowed_actions'])}；程序已在抽样前选定本轮操作 {action}，回复 action 必须为 {action}，执行 1 至 {context['max_changes']} 项。
根据给定代码、统计和样例概括可复用文本规律；训练样例无金标，不能当作已确认标签证据。
plan 写清匹配条件、输出标签、保留的正确行为和应弃权的相似文本；新增规则避免重复已有 LF，不用默认标签凑覆盖率。
每条 LF 只读正文，未命中返回 -1，不按样本 ID 或整条文本查表。
指标和预测来自 simple_vote，平票或全部弃权均为 -1；votes 中未列出的 LF 均弃权。
n 仅用于 modify 的 example_sampling.base_n。add 的目标金标案例全部来自所有 LF 弃权的发现集，平票不混入；train_conflict 指原始 LF 至少投出两个不同有效类别。
程序的 candidate_context 若列出本轮先前方案，应生成判别机制不同的独立候选；不能声称已知选择集表现。
{operation_instructions}
只返回 JSON：顶层为 action、operations；每项只含 target（函数名）、evidence（非空字符串数组）、plan（非空字符串）。"""
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps({"feedback": feedback}, ensure_ascii=False)},
    ]
    if error is not None:
        messages.extend([
            {"role": "assistant", "content": previous_response},
            {"role": "user", "content": f"校验错误：{error}\n请按原反馈与要求纠正，返回完整 JSON。"},
        ])
    reply = ask(messages, json_mode=True)
    if on_response is not None:
        on_response(reply)
    analysis = json.loads(reply)
    try:
        return validate_analysis(analysis, feedback)
    except ValueError as validation_error:
        raise AnalyzerResponseError(str(validation_error), reply, analysis) from validation_error


def validate_analysis(analysis, feedback):
    context = feedback["strategy_context"]
    if not isinstance(analysis, dict) or set(analysis) != {"action", "operations"}:
        raise ValueError("顶层必须且只能包含 action、operations")
    action = analysis["action"]
    if not isinstance(action, str) or action not in {"add", "modify"}:
        raise ValueError("action 必须为 add 或 modify")
    if action not in context["allowed_actions"]:
        raise ValueError(f"当前状态 {context['state']} 只允许 {context['allowed_actions']}，收到 {action}")
    if action != context["selected_action"]:
        raise ValueError(f"反馈已按 {context['selected_action']} 抽样，本轮 action 必须为 {context['selected_action']}")
    operations = analysis["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= context["max_changes"]:
        raise ValueError(f"operations 必须包含 1 至 {context['max_changes']} 项")
    existing, targets = set(feedback["lf_names"]), set()
    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(operation, dict) or set(operation) != {"target", "evidence", "plan"}:
            raise ValueError(f"{path} 必须且只能包含 target、evidence、plan")
        target = operation["target"]
        if not isinstance(target, str) or not target.isidentifier() or keyword.iskeyword(target):
            raise ValueError(f"{path}.target 必须是有效的 Python 函数名")
        if target in targets:
            raise ValueError(f"{path}.target 重复：{target}")
        targets.add(target)
        if action == "add" and target in existing:
            raise ValueError(f"{path}.target 已存在，add 必须使用新函数名：{target}")
        if action == "modify" and target not in existing:
            raise ValueError(f"{path}.target 不存在，modify 必须使用已有 LF 名：{target}")
        if not isinstance(operation["plan"], str) or not operation["plan"].strip():
            raise ValueError(f"{path}.plan 必须是非空字符串")
        evidence = operation["evidence"]
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise ValueError(f"{path}.evidence 必须是非空字符串数组")
        if action == "add":
            cluster = feedback["cluster"]
            positives = {item["id"] for key in ("center_examples", "diverse_examples", "boundary_examples", "random_examples")
                         for item in cluster.get(key, [])}
            negatives = {item["id"] for item in cluster.get("hard_negative_examples", [])}
            training = {item["id"] for item in cluster.get("train_support_examples", [])}
            allowed = {"gold": positives, "negative": negatives, "train": training}
            refs = re.findall(r"\[(gold|negative|train):([^\]]+)\]", "\n".join(evidence))
            if any(key not in allowed[role] for role, key in refs):
                raise ValueError(f"{path}.evidence 引用了未提供或来源不符的案例 ID")
            if len({key for role, key in refs if role == "gold"}) < 2:
                raise ValueError(f"{path}.evidence 必须引用至少两条不同的 [gold:真实ID]")
            if negatives and not any(role == "negative" for role, _ in refs):
                raise ValueError(f"{path}.evidence 必须比较至少一条 [negative:真实ID]")
    return analysis
