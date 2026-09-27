"""分别生成 12 个 LF，再合并为首版脚本。"""

import ast
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm


# ---------- 全局配置 ----------
ROOT = Path(__file__).resolve().parents[2]
V4_DIR = Path(__file__).resolve().parent
VOCAB_JSON = V4_DIR / "artifacts" / "vocabulary.json"
EXAMPLES_JSON = V4_DIR / "artifacts" / "examples.json"
OUTPUT_SCRIPT = V4_DIR / "versions" / "v1" / "script_v1.py"

load_dotenv(ROOT / ".env")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY = os.environ["DEEPSEEK_API_KEY"]
TEMPERATURE = 0.1
CLIENT = OpenAI(api_key=API_KEY, base_url=BASE_URL)

RISK_TYPES = json.loads(VOCAB_JSON.read_text(encoding="utf-8"))
EXAMPLES = json.loads(EXAMPLES_JSON.read_text(encoding="utf-8"))
LF_NAMES = [f"lf_{index:02d}" for index in range(1, 13)]


# ---------- 单个 LF Prompt ----------
SYSTEM_PROMPT = "你是加密新闻 Label Function 工程师。每次只编写一个 Label Function，只返回 JSON。"

USER_PROMPT = """构建第 {index}/12 个 Label Function。

函数名：{lf_name}
风险类别：{risk_type}

本类标注样例：
{positive_examples}

无明显风险反例：
{negative_examples}

要求：
1. 参考样例的内容、风险等级和标注原因。
2. 函数接收 text；未命中返回 None，命中返回 ("{risk_type}", level, reason)。
3. level 只能是 low、medium 或 high，不要风险分数。
4. 使用 hit(text, words) 判断关键词，不要 import，不要定义其他函数。
5. 返回 JSON，code 字段存放完整的 {lf_name} 函数代码。
"""


# ---------- 输出脚本固定结构 ----------
SCRIPT_HEADER = '''import json
import pandas as pd

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


def hit(text, words):
    text = str(text).lower()
    return any(word.lower() in text for word in words)
'''

SCRIPT_FOOTER = '''

LFS = [lf_01, lf_02, lf_03, lf_04, lf_05, lf_06,
       lf_07, lf_08, lf_09, lf_10, lf_11, lf_12]


def predict(text):
    results = [result for lf in LFS if (result := lf(text)) is not None]
    if not results:
        return "low", [], "未命中风险 LF"

    categories = list(dict.fromkeys(result[0] for result in results))
    level = max((result[1] for result in results), key=LEVEL_ORDER.get)
    reason = "；".join(result[2] for result in results)
    return level, categories, reason


def label_csv(input_csv, output_csv):
    data = pd.read_csv(input_csv, encoding="utf-8-sig")
    predictions = data["内容"].apply(predict)
    data["风险等级"] = predictions.apply(lambda value: value[0])
    data["风险类别"] = predictions.apply(
        lambda value: json.dumps(value[1], ensure_ascii=False)
    )
    data["原因"] = predictions.apply(lambda value: value[2])
    data[["新闻id", "时间", "内容", "风险等级", "风险类别", "原因"]].to_csv(
        output_csv, index=False, encoding="utf-8-sig"
    )
'''


def build_messages(index: int, risk_type: str, lf_name: str) -> list[dict[str, str]]:
    prompt = USER_PROMPT.format(
        index=index,
        risk_type=risk_type,
        lf_name=lf_name,
        positive_examples=json.dumps(EXAMPLES[risk_type], ensure_ascii=False),
        negative_examples=json.dumps(EXAMPLES["无明显风险"], ensure_ascii=False),
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def generate_lf(index: int, risk_type: str, lf_name: str) -> str:
    response = CLIENT.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
        messages=build_messages(index, risk_type, lf_name),
    )
    code = json.loads(response.choices[0].message.content)["code"].strip()
    validate_lf(code, risk_type, lf_name)
    return code


def validate_lf(code: str, risk_type: str, lf_name: str) -> None:
    tree = ast.parse(code)
    functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    if functions != [lf_name]:
        raise ValueError(f"{lf_name} 返回了错误的函数：{functions}")
    if risk_type not in code:
        raise ValueError(f"{lf_name} 未返回指定风险类别")
    if "risk_score" in code or "风险分数" in code:
        raise ValueError(f"{lf_name} 包含风险分数")


def main() -> None:
    if len(RISK_TYPES) != 12 or any(name not in EXAMPLES for name in RISK_TYPES):
        raise ValueError("词表或 examples.json 未完整覆盖 12 类风险")

    lf_codes = []
    tasks = zip(RISK_TYPES, LF_NAMES)
    for index, (risk_type, lf_name) in enumerate(
        tqdm(tasks, total=12, desc="生成 Label Functions", unit="个"),
        start=1,
    ):
        lf_codes.append(generate_lf(index, risk_type, lf_name))

    script = SCRIPT_HEADER + "\n\n".join(lf_codes) + SCRIPT_FOOTER
    ast.parse(script)
    OUTPUT_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_SCRIPT.write_text(script, encoding="utf-8")
    print(f"生成完成：{OUTPUT_SCRIPT}")


if __name__ == "__main__":
    main()
