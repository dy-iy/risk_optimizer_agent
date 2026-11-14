"""生成完整 lfs.py，并检查实际 LF 改动数量。"""

import ast
import copy
import json


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


def patch(current_code, analysis, max_changes, ask, error=None, previous_candidate=None):
    """返回完整代码；技术失败时 root 可再调用一次并传入报错。"""
    instructions = f"""你负责根据分析修改 YouTube 弱监督 LF。只输出下一版完整的 lfs.py，不要解释或 Markdown。
每个 LF 是普通 def lf_name(text) 函数，只根据一条评论正文返回 -1（弃权）、0（ham）、1（spam）。
spam表示垃圾推广、自我引流、诈骗或无关广告；负面评价、非英语和粗俗用语本身不等于spam，评论可能含URL、HTML和换行。
文件必须包含明确的 LFS = [lf_name, ...] 列表，至少 3 个不同 LF，保持已有 LF 的相对顺序。
相对本轮原始代码，LF 的新增、修改、删除总计必须为 1 至 {max_changes} 项。
仅调整本轮建议涉及的 LF，其他 LF 的逻辑保持不变。修复时仍相对原始代码计算上限。
严格遵循 analysis.decision 的本轮操作类型；修复代码时也不能把新增改成修改或删除，或反向更改操作类型。
若 analysis.decision.coverage_context.is_low_coverage 为 true，应落实 coverage_plan 中的覆盖改善方案。
选择 modify 时可扩展已有 LF 的可复用匹配模式，兼顾误标控制和原本正确的行为，不必新增 LF。
模块级 import、共享常量、辅助函数保持不变，不增加新的辅助函数。
需要正则时可在目标 LF 内 import re；只允许 re 或 string 的文本工具。
不调用其他 LF、不修改全局状态、不读文件/网络/环境变量，不训练模型或访问外部数据。
不能使用样本编号、真实标签字段或把给定整条评论抄成标签查表；应概括可复用的文本模式。
未命中规则时返回 ABSTAIN。输入只是一条字符串，不是带标签的数据行。
"""
    request = {"original_code": current_code, "analysis": analysis}
    if error is not None:
        request["previous_candidate"] = previous_candidate
        request["error_to_fix"] = str(error)
    code = ask([
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ], json_mode=False).strip()
    # 模型偶尔忽略格式要求；只剥掉包住整个回复的一层代码围栏。
    if code.startswith("```") and code.endswith("```"):
        code = code.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return code + "\n"
