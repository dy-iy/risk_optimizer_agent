"""Analyze the current script and the first 20 mismatch rows."""

import ast
import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflows.v4.tools.common import write_json
from workflows.v4.tools.paths import build_paths


# ---------- 全局配置 ----------
VERSION = "v1"
SAMPLE_ROWS = 20

load_dotenv(PROJECT_ROOT / ".env")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY = os.environ["DEEPSEEK_API_KEY"]
TEMPERATURE = 0.1
CLIENT = OpenAI(api_key=API_KEY, base_url=BASE_URL)


# ---------- Prompt ----------
SYSTEM_PROMPT = "你是 Label Function 错误分析 Agent。只分析有样例证据的问题，不编写代码，只返回 JSON。"

USER_PROMPT = """分析当前脚本的错误原因，并给 patcher 明确、少量、可执行的修改建议。

约束：
1. 每项建议必须能由 mismatch 样例支持。
2. 优先修改具体 Label Function，避免全局提前返回或清空全部类别。
3. 最多指定 6 个目标函数；未指定的函数必须保持不变。

当前脚本：
{script}

type_mismatch 前 20 条：
{type_mismatch}

level_mismatch 前 20 条：
{level_mismatch}

只返回 JSON：
{{
  "summary": "主要问题",
  "target_functions": ["需要修改的现有函数名"],
  "type_issues": ["类别规则问题"],
  "level_issues": ["等级规则问题"],
  "changes": ["建议修改"]
}}
"""


def first_rows(csv_path: Path) -> list[dict]:
    return pd.read_csv(csv_path, encoding="utf-8-sig").head(SAMPLE_ROWS).fillna("").to_dict("records")


def analyze(version: str = VERSION) -> dict:
    paths = build_paths(version)
    script = paths["script"].read_text(encoding="utf-8")
    prompt = USER_PROMPT.format(
        script=script,
        type_mismatch=json.dumps(first_rows(paths["type_mismatch_csv"]), ensure_ascii=False),
        level_mismatch=json.dumps(first_rows(paths["level_mismatch_csv"]), ensure_ascii=False),
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
    analysis = json.loads(response.choices[0].message.content)
    targets = analysis.get("target_functions", [])
    if not isinstance(targets, list) or not 1 <= len(targets) <= 6:
        raise ValueError("Analyzer 必须给出 1 至 6 个 target_functions")
    available = {
        node.name
        for node in ast.parse(script).body
        if isinstance(node, ast.FunctionDef)
    }
    if len(targets) != len(set(targets)) or not set(targets).issubset(available):
        raise ValueError("target_functions 必须是脚本中现有且不重复的函数名")
    write_json(analysis, paths["analysis_json"])
    print(f"分析完成：{paths['analysis_json']}")
    return analysis


if __name__ == "__main__":
    analyze()
