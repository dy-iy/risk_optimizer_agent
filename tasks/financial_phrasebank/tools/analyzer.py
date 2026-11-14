"""组织开发反馈，先决策 LF 操作类型，再分析具体规则。"""

import json
import random

import numpy as np

if __package__:
    from .task import CARDINALITY, LABEL_NAMES
    from .feedback import coverage_details, group_uncovered, load_recent_history, previously_covered_examples
    from .format_checker import check_analysis_format
else:
    from task import CARDINALITY, LABEL_NAMES
    from feedback import coverage_details, group_uncovered, load_recent_history, previously_covered_examples
    from format_checker import check_analysis_format


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


def build_feedback(result, current_code, version_name, max_examples=10, seed=42, *,
                   folder=None, max_uncovered_groups=20, representatives_per_group=2,
                   history_rounds=5):
    """完整标注反馈、按规模排序的未覆盖组、冲突样例、覆盖统计及近期开发历史。"""
    for value, name in ((max_examples, "冲突样例数"), (max_uncovered_groups, "未覆盖组数"),
                        (representatives_per_group, "每组代表句数"), (history_rounds, "历史轮数")):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name}必须是非负整数")
    names = result["lf_names"]
    labeled = result["labeled"]
    unlabeled = result["unlabeled"]
    labeled_L = np.asarray(labeled["L"])
    unlabeled_L = np.asarray(unlabeled["L"])
    predictions = np.asarray(labeled["predictions"])
    gold = np.asarray([
        int(row["label"])
        for row in labeled["rows"]
    ], dtype=int)
    # Random 对字符串种子的处理稳定，不依赖每次进程随机化的 hash()。
    rng = random.Random(f"{seed}:{version_name}")

    def examples(rows, matrix, mask, with_gold=False):
        indices = np.flatnonzero(mask).tolist()
        # 标注预算内的所有样本都展示；只对未标注冲突样例抽样。
        chosen = indices if with_gold else rng.sample(indices, min(max_examples, len(indices)))
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

    conflict = sum(np.any(unlabeled_L == label, axis=1).astype(int) for label in range(CARDINALITY)) > 1
    groups = {
        "labeled_wrong": examples(
            labeled["rows"], labeled_L,
            (predictions != -1) & (predictions != gold), True,
        ),
        "labeled_abstained": examples(
            labeled["rows"], labeled_L, predictions == -1, True,
        ),
        "labeled_correct": examples(
            labeled["rows"], labeled_L, predictions == gold, True,
        ),
        "unlabeled_conflicting": examples(
            unlabeled["rows"], unlabeled_L, conflict,
        ),
    }
    # 只白名单提取开发反馈；不把整个 metrics 或 result 发给模型。
    feedback = {
        "feedback_schema_version": 3,
        "version": version_name,
        "label_values": {str(value): name for value, name in LABEL_NAMES.items()},
        "lf_code": current_code,
        "lf_names": names,
        "labeled_metrics": result["metrics"]["labeled"],
        "labeled_class_coverage": {
            label: {
                "n_samples": int((gold == value).sum()),
                "n_covered": int(((gold == value) & (labeled_L != -1).any(axis=1)).sum()),
                "n_uncovered": int(((gold == value) & (labeled_L == -1).all(axis=1)).sum()),
            }
            for value, label in LABEL_NAMES.items() if value != -1
        },
        "unlabeled_statistics": result["metrics"]["unlabeled"],
        "lf_quality": result["lf_quality"],
        "lf_coverage_details": coverage_details(names, {"labeled": labeled_L, "unlabeled": unlabeled_L}),
        "examples": groups,
        "unlabeled_uncovered_groups": group_uncovered(
            unlabeled["rows"], unlabeled_L, names, max_uncovered_groups,
            representatives_per_group, seed,
        ),
        "recent_development_history": load_recent_history(folder, version_name, history_rounds),
        "unlabeled_previously_covered": previously_covered_examples(
            folder, version_name, unlabeled["rows"], unlabeled_L, names, history_rounds,
        ),
    }
    return json.loads(json.dumps(feedback, ensure_ascii=False, default=_json_value))


def analyze(feedback, max_changes, ask, low_coverage_threshold=0.5, on_response=None, *,
            format_ask, on_format_response=None):
    """Analyzer 决策分析后交给格式 Agent，再执行本地业务结构校验。"""
    if max_changes < 1:
        raise ValueError("LF 改动上限至少为 1")
    if not 0 <= low_coverage_threshold <= 1:
        raise ValueError("低覆盖率阈值必须在 0 到 1 之间")
    unlabeled_coverage = feedback["unlabeled_statistics"]["lf_coverage"]
    labeled_coverage = feedback["labeled_metrics"]["lf_coverage"]
    is_low_coverage = min(unlabeled_coverage, labeled_coverage) < low_coverage_threshold
    context = {
        "unlabeled_lf_coverage": unlabeled_coverage,
        "labeled_lf_coverage": labeled_coverage,
        "low_coverage_threshold": low_coverage_threshold,
        "is_low_coverage": is_low_coverage,
        "optimization_goal": "increase_coverage" if is_low_coverage else "improve_lf_quality",
        "max_changes": max_changes,
    }
    instructions = f"""你负责分析 Financial PhraseBank 英文金融情感弱监督 LF，而不是直接写代码。
在同一次回复中按顺序完成两步：先在 decision 中决定本轮操作类型和原因，
再在 overall_analysis 和 operations 中分析具体问题、给出该类型的规则操作建议。
根据当前 LF、标注集完整指标、按类别的未覆盖数量、样例和未标注集覆盖/冲突统计，
从 add（新增 LF）、modify（修改 LF）、delete（删除 LF）中选择本轮唯一操作类型。
未标注集或标注集的 LF 覆盖率低于给定阈值时，本轮优先目标是提高 LF 集合的整体覆盖率，
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
只使用给定开发反馈和无金标未标注统计；不得使用测试信息，也不能把未标注样例猜测当成金标。
operations 中每项 action 必须与本次回复中 decision.action 一致，同一轮不得混用操作类型。
若本轮是 add，优先覆盖尚未命中的独立模式，并关注标注集中未覆盖数量多的类别；避免重复已有 LF。
同时考虑新增正确覆盖和误标风险，不以已覆盖准确率 100% 为唯一目标。
任务：根据完整固定标注集、未覆盖文本分组、规则覆盖统计和近期开发历史，识别缺失的通用模式，提出小步优化建议。
标签为 -1=弃权、0=negative（负面）、1=neutral（中性）、2=positive（正面）。
固定标注预算总计100条：负面34、中性33、正面33条，属于开发反馈。
examples中的三个labeled分组提供全部固定标注样本，不是每类抽10条；检查各类剩余的覆盖缺口。
unlabeled_uncovered_groups按组规模降序提供数量、关键词、中心句和差异句；kind=lexical_empty表示去掉停用词后没有特征，不代表文本没有价值或金标是中性。
同组只表示词汇相似，不代表句式一致或情感一致；mean_cosine_similarity较低的组尤其混杂。
组规模不是某条新规则的预期命中数，不能从两个代表句推断组内所有文本都符合一个模式。
优先寻找较大未覆盖组中的可复用模式，但不要把某组所有文本自动赋予同一标签。
lf_coverage_details提供每个LF的独有覆盖和共同投票情况；独有覆盖为0不等于该LF无价值，重叠也可帮助聚合。
recent_development_history只含近期实际LF改动、开发指标和未标注统计；delta是比例差，不是相对增长率。
结合历史识别反复修补但收益有限的模式。若旧LF难以清晰表达新模式，考虑新增独立LF；已有模式不完整时修改，重复或有害时考虑删除。
unlabeled_previously_covered给出近期曾有票但当前已弃权的未标注文本，优先检查负收益改动是否引入小数截断、跨度缩短或过宽排除；旧票也不是金标，不能未经分析直接恢复。
当开发集预测错误为0或很少时，把扩大未标注文本的可复用覆盖作为主要目标；不以持续保持开发集已覆盖准确率100%为目标。
不要仅根据数条开发样本就新增只命中极少未标注文本的长短语组合。比较开发集投票数与未标注投票数，识别稀有模式和开发样本贴合的风险。
在action_rationale中说明：为何本轮选择此模式而非近期丢失覆盖或其他更大缺口；扩覆盖提案应引用所提供未标注代表句或回归线索，不能只列开发样本。
从投资者视角判断句子明确表达的金融影响，描述公司业务或例行信息通常为中性。
区分营收/利润/成本的增减、前后比较、预期与实际、否定、转折，不能将所有增长一律判为正面。
未标注样例没有真实标签，不能把预测或你的猜测当金标，不能称其为已确认错例。
这里只提供开发反馈，不得要求或使用测试集信息。
每个 LF 只能根据一条英文句子正文进行规则判断，未命中时弃权。
规则应概括可复用的文本模式，不能抄录整条句子建立标签查表，也不能按样本编号判断。
不得读取源CSV、agreement_level、source_line或其他标注来源信息。
只允许执行本轮选定类型的操作，保留至少 3 个 LF；一次建议执行 1 至 {max_changes} 项操作。
每个 LF 的新增、修改、删除各算一项，不能修改共享常量、模块导入或辅助函数来绕过上限。
整体分析可以讨论多个问题，但 operations 只放本轮实际建议执行的操作。
每项操作都必须说明缺失模式、为何选择该操作、预期改变的已标注样本、应继续弃权的边界、以及对应的未覆盖组。
围绕一个现象设计可复用的规则，统一考虑词形、语序和限制条件；不要仅为引用句子逐词打补丁。
expected_labeled_changes表示目标LF预期投票，不是保证聚合预测改变，也不是本轮自动验收门槛。
若无对应已标注样本，可给空数组并在reason中说明；不能编造样本或为未标注样例编造金标。
unlabeled_group_ids只能引用已给出的group_id，修错或删除操作无对应组时可为空。
只返回 JSON：
{{"decision":{{"action":"add 或 modify 或 delete", "reason":"本轮选择的原因",
  "evidence":["给定统计、样例或代码依据"], "coverage_plan":"低覆盖时的改善覆盖方案及误标控制，其他情况可留空"}},
  "overall_analysis":"整体问题分析", "operations":[{{
  "action":"add 或 modify 或 delete", "target":"LF 函数名",
  "missing_pattern":"本轮要解决的通用缺失模式",
  "action_rationale":"为什么选择此操作和模式，结合未标注具体线索与近期覆盖回归比较优先级",
  "reason":"为什么需要此项改动", "evidence":["已给出的样例编号、统计或代码依据"],
  "idea":"具体的规则修改思路",
  "expected_labeled_changes":[{{"id":"已给出的标注样本编号", "expected_vote":2}}],
  "abstain_conditions":["相近但不应由这条规则判断的情况；删除时说明应移除的投票边界"],
  "unlabeled_group_ids":["uncovered_1"]
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
    analysis, formatted_reply = check_analysis_format(reply, format_ask, on_format_response)
    try:
        return validate_analysis(analysis, feedback, max_changes, context)
    except ValueError as error:
        raise AnalyzerResponseError(str(error), formatted_reply, analysis) from error


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
    labeled_ids = {
        row["id"] for name, rows in feedback["examples"].items()
        if name.startswith("labeled_") for row in rows
    }
    group_ids = {group["group_id"] for group in feedback["unlabeled_uncovered_groups"]}
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
        for field in ("reason", "idea", "missing_pattern", "action_rationale"):
            if not isinstance(operation.get(field), str) or not operation[field].strip():
                raise ValueError(f"操作 {target} 缺少 {field}")
        evidence = operation.get("evidence")
        if not isinstance(evidence, (str, list)) or not evidence:
            raise ValueError(f"操作 {target} 缺少支持依据 evidence")
        boundaries = operation.get("abstain_conditions")
        if not isinstance(boundaries, list) or not boundaries or any(not isinstance(s, str) or not s.strip() for s in boundaries):
            raise ValueError(f"操作 {target} 必须给出非空的 abstain_conditions")
        expectations = operation.get("expected_labeled_changes")
        if not isinstance(expectations, list):
            raise ValueError(f"操作 {target} 必须提供 expected_labeled_changes 数组")
        seen_ids = set()
        for item in expectations:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in labeled_ids:
                raise ValueError(f"操作 {target} 的预期样本必须来自已提供的固定标注集")
            if item["id"] in seen_ids or type(item.get("expected_vote")) is not int or item["expected_vote"] not in LABEL_NAMES:
                raise ValueError(f"操作 {target} 的预期投票必须是 -1/0/1/2 且样本编号不能重复")
            seen_ids.add(item["id"])
        references = operation.get("unlabeled_group_ids")
        if not isinstance(references, list) or any(not isinstance(item, str) or item not in group_ids for item in references):
            raise ValueError(f"操作 {target} 引用了未提供的未覆盖组")
    if decision["action"] == "delete" and len(existing) - len(operations) < 3:
        raise ValueError("删除后必须至少保留 3 个 LF")
    analysis["decision"] = {**decision, "coverage_context": context}
    return analysis
