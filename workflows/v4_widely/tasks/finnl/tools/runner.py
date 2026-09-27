"""规则运行工具：LF 校验、加载、预测聚合及离线脚本评估。"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
    __package__ = "workflows.v4_widely.tasks.finnl.tools"

import ast
import copy
from collections import Counter

from .data import validate_gold
from .common import read_json, write_json
from tqdm import tqdm


def hit(text, words):
    return any(str(word).lower() in str(text).lower() for word in words)


def validate_code(source, names, parameter="record"):
    tree = ast.parse(source)
    if not all(isinstance(n, ast.FunctionDef) for n in tree.body):
        raise ValueError("Only LF function definitions are allowed at module scope")
    actual = [n.name for n in tree.body]
    if actual != list(names):
        raise ValueError(f"LF names/order mismatch: expected {list(names)!r}, received {actual!r}. "
                         "Return only the requested function(s), with their exact names.")
    forbidden = {"open", "eval", "exec", "compile", "globals", "locals", "getattr", "setattr", "delattr",
                 "__import__", "input", "breakpoint", "vars"}
    for node in tree.body:
        args = node.args
        if (len(args.args) != 1 or args.args[0].arg != parameter or args.posonlyargs or args.kwonlyargs
                or args.vararg or args.kwarg or args.defaults or node.decorator_list or node.returns
                or args.args[0].annotation):
            raise ValueError(f"{node.name} must accept only {parameter}, without decorators/defaults/annotations")
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef)):
                raise ValueError("Imports, globals and additional definitions are forbidden")
            if isinstance(child, ast.FunctionDef) and child is not node:
                raise ValueError("Nested functions are forbidden")
            if isinstance(child, ast.Name) and (child.id in forbidden or child.id.startswith("__")):
                raise ValueError(f"Forbidden name: {child.id}")
            if isinstance(child, ast.Attribute) and child.attr.startswith("__"):
                raise ValueError("Dunder access is forbidden")
    return tree


def import_legacy(source, task):
    """Extract only legacy LFs; never execute legacy label_csv on gold data."""
    tree = ast.parse(source)
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    parts = []
    for name in task.lf_names:
        node = copy.deepcopy(nodes[name])
        if len(node.args.args) != 1 or node.args.args[0].arg != "text":
            raise ValueError("Legacy LF must accept text")
        node.args.args[0].arg = "record"
        node.body.insert(0, ast.parse('text = record["text"]').body[0])
        class ConvertReturn(ast.NodeTransformer):
            def visit_Return(self, stmt):
                if isinstance(stmt.value, (ast.Tuple, ast.List)) and len(stmt.value.elts) == 3:
                    category, level, reason = stmt.value.elts
                    stmt.value = ast.Dict(keys=[ast.Constant("labels"), ast.Constant("level"), ast.Constant("reason")],
                                          values=[ast.List(elts=[category], ctx=ast.Load()), level, reason])
                return stmt
        node = ConvertReturn().visit(node)
        parts.append(ast.unparse(ast.fix_missing_locations(node)))
    converted = "\n\n".join(parts) + "\n"
    validate_code(converted, task.lf_names)
    return converted


def legacy_support(source, task):
    """Freeze legacy helpers and aggregation, excluding imports, LF code and all IO."""
    selected = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            if any(t.id == "LFS" for t in node.targets):
                continue
            ast.literal_eval(node.value)
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name not in (*task.lf_names, "label_csv"):
            selected.append(node)
    support = ast.unparse(ast.Module(body=selected, type_ignores=[])) + "\n"
    validate_support(support)
    return support


def validate_support(source):
    """Validate a frozen adapter; never expose it as a patch target."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            if not all(isinstance(t, ast.Name) and not t.id.startswith("__") for t in node.targets):
                raise ValueError("Invalid frozen constant")
            ast.literal_eval(node.value)
        elif isinstance(node, ast.FunctionDef):
            if node.decorator_list or node.args.defaults or node.args.kw_defaults:
                raise ValueError("Invalid frozen helper definition")
            clone = copy.deepcopy(node)
            clone.args = ast.arguments(posonlyargs=[], args=[ast.arg(arg="record")], kwonlyargs=[], kw_defaults=[], defaults=[])
            clone.returns = None
            validate_code(ast.unparse(clone), [clone.name])
        else:
            raise ValueError("Only literal constants and functions allowed in frozen support")


class RuleSet:
    def __init__(self, task, source, train, support=None):
        validate_code(source, task.lf_names)
        import builtins
        safe_names = ("str", "int", "float", "bool", "list", "dict", "set", "tuple", "len", "any", "all",
                      "sum", "min", "max", "sorted", "enumerate", "zip", "range", "abs", "isinstance")
        namespace = {"__builtins__": {name: getattr(builtins, name) for name in safe_names}, "hit": hit}
        self.support_namespace = None
        if support:
            if task.kind != "joint":
                raise ValueError("Frozen legacy support is only valid for Crypto")
            import re
            validate_support(support)
            namespace["re"] = re
            exec(compile(support, "<frozen-legacy-support>", "exec"), namespace)
            self.support_namespace = namespace
        exec(compile(source, "<label-functions>", "exec"), namespace)
        self.functions = [(name, namespace[name]) for name in task.lf_names]
        self.task = task
        counts = Counter(r["gold"][task.field] for r in train) if task.kind == "single" else Counter()
        self.priority = sorted(task.labels, key=lambda label: (-counts[label], task.labels.index(label)))

    def predict(self, record):
        # Explicit allowlist. Never pass gold, IDs, source metadata or other rows.
        payload = {"text": record["text"], "inputs": copy.deepcopy(record["inputs"])}
        votes = []
        for name, function in self.functions:
            vote = function(copy.deepcopy(payload))
            if vote is None:
                continue
            if not isinstance(vote, dict) or not isinstance(vote.get("reason"), str):
                raise ValueError(f"{name}: vote requires labels and a string reason")
            labels = vote.get("labels")
            if (not isinstance(labels, list) or not labels or len(set(labels)) != len(labels)
                    or any(label not in self.task.labels for label in labels)):
                raise ValueError(f"{name}: invalid vote labels")
            if self.task.kind in ("single", "joint") and len(labels) != 1:
                raise ValueError("Single-class and joint-task LFs must vote for one label")
            if self.task.kind == "joint" and vote.get("level") not in ("low", "medium", "high"):
                raise ValueError("Joint-task LF requires a valid level")
            votes.append({**vote, "lf": name})
        labels = list(dict.fromkeys(label for vote in votes for label in vote["labels"]))
        if self.task.kind == "single":
            counts = Counter(label for vote in votes for label in vote["labels"])
            selected = min(self.priority, key=lambda label: (-counts[label], self.priority.index(label)))
            prediction = {self.task.field: selected}
        else:
            prediction = {self.task.field: labels}
            if self.task.kind == "joint":
                prediction["level"] = max((v["level"] for v in votes),
                                          key=("low", "medium", "high").index, default="low")
                if self.support_namespace is not None:
                    by_name = {v["lf"]: (v["labels"][0], v["level"], v["reason"]) for v in votes}
                    self.support_namespace["LFS"] = [lambda text, value=by_name.get(name): value
                                                     for name in self.task.lf_names]
                    level, categories, _ = self.support_namespace["predict"](record["text"])
                    prediction = {"categories": categories, "level": level}
        validate_gold(self.task, prediction)
        return {"id": record["id"], "prediction": prediction, "votes": votes,
                "fallback": not votes, "conflict": self.task.kind == "single" and len(labels) > 1}

def read_script(path):
    """Load rule source plus its frozen legacy helper, if present."""
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    if (path.parent / "support.py").exists():
        support = (path.parent / "support.py").read_text(encoding="utf-8")
    elif (path.parent / "support.json").exists():
        support = read_json(path.parent / "support.json")["source"]
    else:
        support = None
    return source, support


def run_records(task, source, train, records, support=None, show_progress=True, description=None):
    """Execute LFs with train-only fallback priorities; no model calls."""
    rules = RuleSet(task, source, train, support)
    with tqdm(records, desc=description or f"{task.name} predict", unit="record",
              leave=False, dynamic_ncols=True, disable=not show_progress) as rows:
        return [rules.predict(record) for record in rows]


def main(argv=None):
    import argparse
    from .data import load_prepared
    from .evaluator import evaluate, quality_report
    from .slicer import sample_errors
    from .paths import get_task, build_paths, current_version, TASK_DIR
    parser = argparse.ArgumentParser(description="运行规则脚本并保存预测、指标和错例；不调用模型")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--version", help="版本号；省略时使用当前已接受版本")
    choice.add_argument("--script", type=Path, help="待评估的候选 rules.py")
    parser.add_argument("--split", choices=("train", "test"), default="train")
    parser.add_argument("--data-dir", type=Path, default=TASK_DIR / "data")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, help="输出目录；默认脚本同目录 reports/<split>/")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)
    task = get_task(seed=args.seed)
    parts, _ = load_prepared(task, args.data_dir)
    script = args.script or build_paths(args.version or current_version())["script"]
    source, support = read_script(script)
    predictions = run_records(task, source, parts["train"], parts[args.split], support, not args.no_progress)
    out = args.out or script.parent / "reports" / args.split
    write_json(out / "predictions.json", predictions)
    write_json(out / "metrics.json", evaluate(task, parts[args.split], predictions))
    write_json(out / "lf_quality.json", quality_report(task, parts[args.split], predictions))
    write_json(out / "errors.json", sample_errors(task, parts[args.split], predictions))
    print(out)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; recorded usage has been saved.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
