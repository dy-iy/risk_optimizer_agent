"""初始化主程序：根据训练样例逐个生成、校验 LF，再保存首版规则。"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    __package__ = "workflows.v4_widely.tasks.fincausal"

import ast
import random
from tqdm import tqdm
from .tools.common import write_json
from .tools.runner import RuleSet, validate_code


def normalize_initial_function(source, expected_name):
    """Repair only an unreferenced top-level function name; preserve every other constraint."""
    tree = ast.parse(source)
    repair = None
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef):
        node = tree.body[0]
        actual_name = node.name
        if actual_name != expected_name:
            # Do not rewrite recursive calls, local references, helpers or multiple definitions.
            if not any(isinstance(n, ast.Name) and n.id == actual_name for n in ast.walk(node)):
                node.name = expected_name
                source = ast.unparse(tree) + "\n"
                repair = {"kind": "rename_single_function", "from": actual_name, "to": expected_name}
    validate_code(source, [expected_name])
    return source, repair


def validate_initial_function(task, code, name, train):
    """Exercise the generated LF on train before accepting it into v1."""
    source = "\n\n".join(code if lf == name else f"def {lf}(record):\n    return None"
                         for lf in task.lf_names)
    rules = RuleSet(task, source, train)
    for record in train:
        try:
            rules.predict(record)
        except Exception as error:
            raise ValueError(
                f"{name}: training input runtime validation failed: {error}. "
                'Return None or {"labels": ["allowed label"], "reason": "evidence"}; '
                "joint tasks also require level."
            ) from error


def examples(task, train):
    rng = random.Random(task.seed)
    rows = list(train)
    rng.shuffle(rows)
    selected = {}
    for label in task.labels:
        matching = [r for r in rows if (r["gold"][task.field] == label if task.kind == "single"
                                      else label in r["gold"][task.field])]
        selected[label] = [{"id": r["id"], "text": r["text"], "inputs": r["inputs"], "gold": r["gold"]}
                           for r in matching[:task.examples_per_label]]
    return selected


def initialize(client, train):
    with tqdm(total=len(client.task.lf_names), desc="Initialize LFs", unit="LF",
              leave=False, dynamic_ncols=True, disable=not client.show_progress) as progress:
        return _initialize(client, train, progress)


def _initialize(client, train, progress):
    selected = examples(client.task, train)
    parts = []
    for name, label in zip(client.task.lf_names, client.task.labels):
        feedback = None
        previous_code = None
        for attempt in (1, 2):
            progress.set_description(f"Initialize {name} (try {attempt}/2)")
            try:
                result = client.request("initialize", {"lf_name": name, "assigned_label": label,
                    "required_signature": f"def {name}(record):",
                    "required_vote_example": {"labels": [label], "reason": "text evidence",
                                              **({"level": "low"} if client.task.kind == "joint" else {})},
                    "abstain_value": None,
                    "examples": selected, "response_schema": {"code": "complete LF function"},
                    "previous_code": previous_code,
                    "validation_feedback": feedback}, stage="initialize", iteration=0,
                    generation_attempt=attempt, version="v1", lf_name=name)
                previous_code = result["code"]
                code, repair = normalize_initial_function(previous_code, name)
                progress.set_description(f"Validate {name} on train")
                validate_initial_function(client.task, code, name, train)
                parts.append(code.strip())
                ledger = getattr(client.caller, "ledger", None)
                if ledger is not None and not ledger.debug:
                    (ledger.directory / "initialization.py").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
                _save_initialization(client, name, attempt, previous_code, code=code, repair=repair)
                progress.update(1)
                break
            except (ValueError, KeyError, TypeError, SyntaxError) as error:
                feedback = str(error)
                _save_initialization(client, name, attempt, previous_code, error=feedback)
                if attempt == 2:
                    raise ValueError(f"Initialization failed for {name}: {feedback}") from error
    return "\n\n".join(parts) + "\n"


def _save_initialization(client, name, attempt, original_code, **result):
    ledger = getattr(client.caller, "ledger", None)
    if ledger is None:
        return
    if not ledger.debug:
        if result.get("error"):
            with (ledger.directory / "changes.md").open("a", encoding="utf-8") as stream:
                stream.write(f"\n### 初始化 {name}，尝试 {attempt} 失败\n\n{result['error']}\n")
                if original_code:
                    stream.write(f"\n```python\n{original_code}\n```\n")
        return
    directory = ledger.directory / "initialization" / name
    write_json(directory / f"attempt_{attempt}.json", {"lf_name": name, "attempt": attempt,
               "original_code": original_code, **result})
    if result.get("code") is not None:
        (directory / "accepted.py").write_text(result["code"], encoding="utf-8")


def main(argv=None):
    from .orchestrator import main as orchestrate
    orchestrate(["--init-only", "--new", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; recorded usage has been saved.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
