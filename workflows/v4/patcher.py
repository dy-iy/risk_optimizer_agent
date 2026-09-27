"""Replace only Analyzer-selected functions in the next version."""

import ast
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflows.v4.tools.common import write_json
from workflows.v4.tools.paths import build_paths


# ---------- 全局配置 ----------
SOURCE_VERSION = "v1"
NEXT_VERSION = "v2"
MAX_FUNCTION_CHANGES = 6
GENERATION_ATTEMPTS = 2
LF_NAMES = {f"lf_{index:02d}" for index in range(1, 13)}

load_dotenv(PROJECT_ROOT / ".env")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY = os.environ["DEEPSEEK_API_KEY"]
TEMPERATURE = 0.1
CLIENT = OpenAI(api_key=API_KEY, base_url=BASE_URL)


# ---------- Prompt ----------
SYSTEM_PROMPT = "你是 Label Function 脚本修改 Agent。只修改指定函数，只返回 JSON。"

USER_PROMPT = """根据分析修改当前脚本。

要求：
1. 只能修改 analysis.target_functions 中列出的现有函数。
2. 未列出的函数和全局代码必须保持原样。
3. 每个 code 必须是一个完整函数，不能新增 import、全局变量或新函数。
4. 保留函数接口；风险等级只能是 low、medium、high；不要风险分数。
5. 最多修改 {max_changes} 个函数。
6. 只返回 JSON：
{{
  "functions": [{{"name": "lf_01", "code": "完整函数代码"}}],
  "changes": ["修改摘要"]
}}

当前脚本：
{script}

分析结果：
{analysis}

上次校验或版本对比反馈：
{feedback}
"""


def function_nodes(source: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def apply_replacements(source: str, replacements: list[dict], allowed: set[str]) -> tuple[str, list[str]]:
    if not 1 <= len(replacements) <= MAX_FUNCTION_CHANGES:
        raise ValueError(f"必须修改 1 至 {MAX_FUNCTION_CHANGES} 个函数")

    source_nodes = function_nodes(source)
    names = [str(item.get("name", "")).strip() for item in replacements]
    if len(names) != len(set(names)):
        raise ValueError("不能重复修改同一函数")
    if not set(names).issubset(allowed):
        raise ValueError(f"修改了 Analyzer 未指定的函数：{sorted(set(names) - allowed)}")
    if not set(names).issubset(source_nodes):
        raise ValueError(f"脚本中不存在函数：{sorted(set(names) - set(source_nodes))}")

    edits = []
    for item, name in zip(replacements, names):
        code = str(item.get("code", "")).strip()
        tree = ast.parse(code)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(tree.body) != 1 or len(functions) != 1 or functions[0].name != name:
            raise ValueError(f"{name} 的 code 必须只包含同名完整函数")
        node = source_nodes[name]
        edits.append((node.lineno - 1, node.end_lineno, code + "\n"))

    lines = source.splitlines(keepends=True)
    for start, end, code in sorted(edits, reverse=True):
        lines[start:end] = [code]
    updated = "".join(lines)
    if updated == source:
        raise ValueError("新脚本没有实际变化")
    return updated, names


def validate_script(script: str) -> None:
    tree = ast.parse(script)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    if not (LF_NAMES | {"predict", "label_csv"}).issubset(functions):
        raise ValueError("新脚本缺少 Label Function、predict 或 label_csv")
    if "risk_score" in script or "风险分数" in script:
        raise ValueError("v4 不允许风险分数")


def patch(
    source_version: str = SOURCE_VERSION,
    next_version: str = NEXT_VERSION,
    version_feedback: dict | None = None,
    version_attempt: int = 1,
) -> Path:
    source_paths = build_paths(source_version)
    target_paths = build_paths(next_version)
    source = source_paths["script"].read_text(encoding="utf-8")
    analysis = json.loads(source_paths["analysis_json"].read_text(encoding="utf-8"))
    allowed = {str(name).strip() for name in analysis["target_functions"]}

    validation_error = "无"
    for generation_attempt in range(1, GENERATION_ATTEMPTS + 1):
        feedback = {
            "版本对比反馈": version_feedback or "无",
            "本次生成校验错误": validation_error,
        }
        prompt = USER_PROMPT.format(
            max_changes=MAX_FUNCTION_CHANGES,
            script=source,
            analysis=json.dumps(analysis, ensure_ascii=False),
            feedback=json.dumps(feedback, ensure_ascii=False),
        )
        response = CLIENT.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        try:
            result = json.loads(response.choices[0].message.content)
            script, modified = apply_replacements(source, result["functions"], allowed)
            validate_script(script)
            break
        except Exception as error:
            validation_error = str(error)
            if generation_attempt == GENERATION_ATTEMPTS:
                raise

    target_paths["script"].write_text(script, encoding="utf-8")
    write_json(
        {
            "source_version": source_version,
            "next_version": next_version,
            "version_attempt": version_attempt,
            "modified_functions": modified,
            "changes": result.get("changes", []),
            "version_feedback_used": bool(version_feedback),
        },
        target_paths["patch_report_json"],
    )
    print(f"新版本完成：{target_paths['script']}")
    return target_paths["script"]


if __name__ == "__main__":
    patch()
