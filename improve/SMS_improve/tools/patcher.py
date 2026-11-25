"""生成目标 LF 函数，在本地合并源码并检查实际改动。"""

import ast
import copy
import json
import symtable


if __package__:
    from .task import DATASET, LABEL_DESCRIPTION, TASK_DESCRIPTION
else:
    from task import DATASET, LABEL_DESCRIPTION, TASK_DESCRIPTION


def _without_docstrings(node):
    node = copy.deepcopy(node)
    for part in ast.walk(node):
        if isinstance(part, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if part.body and isinstance(part.body[0], ast.Expr):
                value = part.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    part.body.pop(0)
    return ast.dump(node, include_attributes=False)


def _read_lfs(code):
    """只接受明确的普通函数列表，不执行候选代码。"""
    tree = ast.parse(code)
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "LFS" for target in node.targets)
    ]
    if len(assignments) != 1:
        raise ValueError("lfs.py 必须且只能有一条 LFS = [...] 赋值")
    assignment = assignments[0]
    if len(assignment.targets) != 1 or not isinstance(assignment.value, ast.List):
        raise ValueError("LFS 必须直接写成普通函数名列表")
    if not all(isinstance(item, ast.Name) for item in assignment.value.elts):
        raise ValueError("LFS 中只能放函数名")
    names = [item.id for item in assignment.value.elts]
    if len(names) != len(set(names)) or len(names) < 3:
        raise ValueError("LFS 至少包含 3 个不同的 LF")
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    functions = {node.name: node for node in definitions}
    if len(functions) != len(definitions):
        raise ValueError("不允许重复定义同名函数")
    if any(name not in functions for name in names):
        raise ValueError("LFS 中每个名称都必须对应本文件里的普通函数")
    # 模块级 import、常量、辅助函数等必须保持，防止一次修改影响多个 LF。
    shared = ast.Module(body=[
        node for node in tree.body
        if node is not assignment and not (isinstance(node, ast.FunctionDef) and node.name in names)
    ], type_ignores=[])
    return names, functions, _without_docstrings(shared)


def _check_function(function, lf_names):
    arguments = function.args
    if (
        function.decorator_list or function.returns is not None
        or arguments.posonlyargs or len(arguments.args) != 1
        or arguments.args[0].arg != "text" or arguments.args[0].annotation is not None
        or arguments.vararg or arguments.kwarg or arguments.kwonlyargs or arguments.defaults
    ):
        raise ValueError(f"{function.name} 必须是普通的 def {function.name}(text) 函数")
    banned_names = {
        "open", "exec", "eval", "compile", "__import__", "input", "globals", "locals",
        "vars", "getattr", "setattr", "delattr", "breakpoint", "LFS",
    }
    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.ClassDef)):
            raise ValueError(f"{function.name} 只允许局部的文本规则")
        if isinstance(node, ast.FunctionDef) and node is not function:
            raise ValueError(f"{function.name} 不使用嵌套函数，请直接写文本规则")
        if isinstance(node, ast.Name) and (node.id in banned_names or node.id in lf_names):
            raise ValueError(f"{function.name} 不得访问 {node.id}，LF 必须独立判断正文")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"{function.name} 不得访问私有属性")
        if isinstance(node, ast.Import):
            if any(alias.name not in {"re", "string"} for alias in node.names):
                raise ValueError("LF 内只允许导入 re 或 string 进行文本处理")
        if isinstance(node, ast.ImportFrom) and (node.level or node.module not in {"re", "string"}):
            raise ValueError("LF 内只允许导入 re 或 string 进行文本处理")


def validate_changes(old_code, new_code, max_changes):
    """按函数 AST 统计增删改；格式和说明文字不计入操作。"""
    if max_changes < 1:
        raise ValueError("LF 改动上限至少为 1")
    old_names, old_functions, old_shared = _read_lfs(old_code)
    new_names, new_functions, new_shared = _read_lfs(new_code)
    if old_shared != new_shared:
        raise ValueError("不能修改模块级 import、常量或辅助函数；请把新规则写在目标 LF 内")
    remaining_old = [name for name in old_names if name in new_names]
    remaining_new = [name for name in new_names if name in old_names]
    if remaining_old != remaining_new:
        raise ValueError("请保持已有 LF 在 LFS 中的相对顺序")
    for name in new_names:
        _check_function(new_functions[name], set(new_names))
    changes = []
    for name in old_names:
        if name not in new_names:
            changes.append({"action": "delete", "target": name})
        elif _without_docstrings(old_functions[name]) != _without_docstrings(new_functions[name]):
            changes.append({"action": "modify", "target": name})
    changes.extend({"action": "add", "target": name} for name in new_names if name not in old_names)
    if not 1 <= len(changes) <= max_changes:
        raise ValueError(f"实际 LF 操作数为 {len(changes)}，必须在 1 至 {max_changes} 之间")
    return changes


def merge_functions(current_code, analysis, functions_code):
    """按源码位置替换目标函数；其余源码保持原文，LFS 在本地更新。"""
    names, functions, _ = _read_lfs(current_code)
    action = analysis["action"]
    targets = [item["target"] for item in analysis["operations"]]
    if len(targets) != len(set(targets)):
        raise ValueError("目标 LF 函数名不能重复")
    if action == "add":
        symbols = symtable.symtable(current_code, "lfs.py", "exec")
        bound = {symbol.get_name() for symbol in symbols.get_symbols()
                 if symbol.is_assigned() or symbol.is_imported()}
        collisions = sorted(set(targets) & bound)
        if collisions:
            raise ValueError(f"新增 LF 与已有模块名称冲突：{collisions}")
        new_names = names + targets
    elif action in {"modify", "delete"}:
        missing = sorted(set(targets) - set(names))
        if missing:
            raise ValueError(f"目标 LF 不存在：{missing}")
        new_names = [name for name in names if name not in targets] if action == "delete" else names
    else:
        raise ValueError(f"不支持的 LF 操作：{action}")
    if len(new_names) < 3:
        raise ValueError("删除后必须至少保留 3 个 LF")

    replacements = {}
    if action != "delete":
        tree = ast.parse(functions_code)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                raise ValueError(f"Patcher 只能输出目标函数定义，不能包含模块级 {type(node).__name__}")
            if node.name in replacements:
                raise ValueError(f"Patcher 重复定义目标函数：{node.name}")
            replacements[node.name] = node
        expected, actual = set(targets), set(replacements)
        if actual != expected:
            raise ValueError(f"Patcher 函数与计划不符：缺少 {sorted(expected - actual)}；多余 {sorted(actual - expected)}")
        for node in replacements.values():
            _check_function(node, set(new_names))
        if action == "modify":
            unchanged = [target for target in targets
                         if _without_docstrings(functions[target]) == _without_docstrings(replacements[target])]
            if unchanged:
                raise ValueError(f"以下目标 LF 没有逻辑改动：{unchanged}")

    original = current_code.encode("utf-8")
    offsets = [0] + [index + 1 for index, byte in enumerate(original) if byte == 10]
    edits = []

    def replace(node, text):
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        edits.append((start, end, text.encode("utf-8")))

    if action == "modify":
        for target in targets:
            replace(functions[target], ast.get_source_segment(functions_code, replacements[target]))
    elif action == "delete":
        for target in targets:
            replace(functions[target], "")

    if action in {"add", "delete"}:
        assignment = next(node for node in ast.parse(current_code).body
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "LFS" for target in node.targets))
        replace(assignment.value, "[" + ", ".join(new_names) + "]")
        if action == "add":
            added = "\n\n".join(ast.get_source_segment(functions_code, replacements[target]) for target in targets)
            position = offsets[assignment.lineno - 1]
            edits.append((position, position, ("\n\n" + added + "\n\n").encode("utf-8")))

    for start, end, replacement in sorted(edits, reverse=True):
        original = original[:start] + replacement + original[end:]
    return original.decode("utf-8")


def patch(current_code, analysis, max_changes, ask, error=None, previous_functions=None, on_response=None):
    """一次生成本轮全部目标函数；删除只返回空串，不调用模型。"""
    if analysis["action"] == "delete":
        return ""
    generalization = ""
    if analysis["action"] == "add":
        generalization = """新增 LF 必须实现 plan 中概括的共享模式和泛化机制，不能把 evidence 中的某条原句、专有人名或长片段直接改写成正则。
同一模式的合理词形、同义表达、标点和语序变体应在同一函数内处理，保留计划规定的局部上下文和弃权边界；不把多个单例条件拼成伪通用规则，不无依据地返回默认类别。
"""
    instructions = f"""你负责根据分析生成 {DATASET} 弱监督 LF。只输出 analysis.operations 指定的目标函数定义，按操作顺序连写，不要解释或 Markdown。
任务：{TASK_DESCRIPTION}
每个 LF 是普通 def lf_name(text) 函数，只根据一条完整文本返回整数标签：{LABEL_DESCRIPTION}。
本轮最多涉及 {max_changes} 个 LF，一次输出所有目标函数；函数名必须与目标完全一致，不能遗漏、重复或增加其他函数。
只输出目标 LF 定义。程序会在本地合并函数并维护 LFS。LF 函数体直接实现规则，禁止使用嵌套函数。
严格遵循 analysis.action 的本轮统一操作类型；修复时也不能更改操作类型。
逐项实现 operations 中的 plan，结合 evidence 理解依据，保留计划要求的正确行为和弃权边界。
{generalization}可以复用 original_code 中已有的共享常量和文本解析辅助函数。需要正则时在目标 LF 内 import re，只允许 re 或 string 的文本工具。
不调用其他 LF、不修改全局状态、不读文件/网络/环境变量，不训练模型或访问外部数据。
不能使用样本编号、真实标签字段或把样例原文抄成标签查表；应概括可复用的文本模式。
区分主动推送的领奖、付费号码、促销和退订指令，与亲友对话、确认、约见和其他真实通知。单个网址、号码或 free、call 等词需要结合上下文。
未命中规则时返回 ABSTAIN。输入只是一条字符串，不是带标签的数据行。
"""
    request = {"original_code": current_code, "analysis": analysis}
    if error is not None:
        request["previous_functions"] = previous_functions
        request["error_to_fix"] = str(error)
    reply = ask([
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ], json_mode=False)
    if on_response is not None:
        on_response(reply)
    code = reply.strip()
    # 模型偶尔忽略格式要求；只剥掉包住整个回复的一层代码围栏。
    if code.startswith("```") and code.endswith("```"):
        code = code.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return code + "\n"
