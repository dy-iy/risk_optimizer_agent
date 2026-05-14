from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError as e:
    raise ImportError("未安装 openai SDK，请先执行: pip install openai python-dotenv") from e


@dataclass
class PatchLLMResult:
    success: bool
    analysis_json: str
    source_script: str
    target_script: str
    patch_report_json: str
    model_name: str
    error_message: str = ""
    raw_response_chars: int = 0


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    ensure_parent_dir(path)
    path.write_text(text, encoding="utf-8")


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_code_block(text: str) -> str:
    """
    优先提取 markdown 代码块中的 Python 代码。
    如果没有代码块，则直接返回原文本。
    """
    pattern = r"```(?:python)?\s*([\s\S]*?)```"
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    if matches:
        matches = sorted(matches, key=len, reverse=True)
        return matches[0].strip()
    return text.strip()


def basic_python_sanity_check(code: str) -> tuple[bool, str]:
    """
    对 LLM 生成的脚本做最基本的文本级检查。
    """
    required_snippets = [
        "RISK_SCORERS",
        "score_all_risks",
        "to_csv",
        "rule_label",
        "rule_types",
        "rule_primary_type",
    ]
    missing = [s for s in required_snippets if s not in code]
    if missing:
        return False, f"生成代码缺少关键片段: {missing}"

    required_funcs = [
        "def score_hack",
        "def score_volatility",
        "def score_whale",
        "def score_outage",
    ]
    missing_funcs = [s for s in required_funcs if s not in code]
    if missing_funcs:
        return False, f"生成代码缺少关键函数: {missing_funcs}"

    return True, ""


def build_system_prompt() -> str:
    return """你是一个负责“最小改动修复 Python 规则脚本”的高级代码代理。

你的任务：
1. 阅读一份加密货币新闻风险规则脚本；
2. 阅读分析报告；
3. 基于分析结果，生成一个新的完整 Python 脚本；
4. 必须尽量保持原脚本结构不变，只做必要修改；
5. 不要删掉现有输出字段；
6. 不要改项目路径读取方式；
7. 不要引入复杂新依赖；
8. 输出必须是完整可运行的 Python 代码。

强约束：
- 保持输入/输出接口不变；
- 保持这些输出列仍然存在：
  score_hack, score_fraud, score_regulatory, score_outage, score_stablecoin,
  score_liquidation, score_whale, score_volatility, score_team, score_solvency,
  score_infra, score_macro, risk, rule_label, rule_types, rule_primary_type
- 不要重写整个项目；
- 不要把脚本改成类风格；
- 不要删除原有 helper 函数；
- 只在必要位置做增量修改；
- 尽量保留原注释风格；
- 如需新增关键词或弱触发分支，可以新增；
- 如需收紧某些规则，请直接在对应 scorer 内修改。

输出要求：
- 只输出完整 Python 代码
- 不要输出解释
- 最好放在 ```python 代码块中
""".strip()


def build_user_prompt(analysis: dict, source_code: str) -> str:
    summary = json.dumps(analysis.get("summary", {}), ensure_ascii=False, indent=2)
    findings = json.dumps(analysis.get("findings", []), ensure_ascii=False, indent=2)

    return f"""下面是规则脚本的错误分析结果。请你基于这些分析，对原始脚本做“小步修复”，生成一个新的完整 Python 脚本。

【分析摘要】
{summary}

【关键 findings】
{findings}

请重点处理这些问题：
1. score_volatility 误报过多：纯百分比、正面上涨、统计性描述不应轻易触发异常波动风险。
2. score_whale 误报较多：不要让过宽的语义触发，区分弱巨鲸风险和强巨鲸风险。
3. score_outage 存在盲区：补充冷钱包无法访问、私钥未移交、资产无法访问、提款异常等运维风险信号。
4. score_regulatory 对弱监管信号识别不足：提案、草案、咨询期、监管阻力、警告等可作为低强度监管风险。
5. score_team 可补充轻度治理异常：如高管离职、项目停滞、转型受阻。
6. score_liquidation 可补充低强度清算/逼空预期：期权集中到期、负资金费率、空头挤压风险等。

修改原则：
- 保持原脚本主体结构；
- 保持所有既有输出列；
- 可以新增少量关键词表或弱触发词表；
- 不要把所有低强度新闻都打太高，尽量给 0.30~0.40 左右的低风险分；
- 保持代码简洁、可运行、易读。

【原始脚本】
```python
{source_code}
```
""".strip()


def call_deepseek_generate_code(
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    base_url: str = "https://api.deepseek.com",
) -> str:
    client = OpenAI(api_key=api_key, base_url=base_url)

    resp = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def patch_rule_script_with_llm(
    analysis_json: str | Path,
    source_script: str | Path,
    target_script: str | Path,
    patch_report_json: str | Path,
    model_name: Optional[str] = None,
) -> PatchLLMResult:
    analysis_json = Path(analysis_json).resolve()
    source_script = Path(source_script).resolve()
    target_script = Path(target_script).resolve()
    patch_report_json = Path(patch_report_json).resolve()

    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return PatchLLMResult(
            success=False,
            analysis_json=str(analysis_json),
            source_script=str(source_script),
            target_script=str(target_script),
            patch_report_json=str(patch_report_json),
            model_name=model_name or "",
            error_message="缺少 DEEPSEEK_API_KEY，请检查 .env",
        )

    if model_name is None:
        model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"

    if not analysis_json.exists():
        return PatchLLMResult(
            success=False,
            analysis_json=str(analysis_json),
            source_script=str(source_script),
            target_script=str(target_script),
            patch_report_json=str(patch_report_json),
            model_name=model_name,
            error_message=f"analysis_json 不存在: {analysis_json}",
        )

    if not source_script.exists():
        return PatchLLMResult(
            success=False,
            analysis_json=str(analysis_json),
            source_script=str(source_script),
            target_script=str(target_script),
            patch_report_json=str(patch_report_json),
            model_name=model_name,
            error_message=f"source_script 不存在: {source_script}",
        )

    analysis = load_json(analysis_json)
    source_code = read_text(source_script)

    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(analysis, source_code)

    try:
        raw_response = call_deepseek_generate_code(
            api_key=api_key,
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except Exception as e:
        return PatchLLMResult(
            success=False,
            analysis_json=str(analysis_json),
            source_script=str(source_script),
            target_script=str(target_script),
            patch_report_json=str(patch_report_json),
            model_name=model_name,
            error_message=f"调用 DeepSeek 失败: {e}",
        )

    code = extract_code_block(raw_response)
    ok, msg = basic_python_sanity_check(code)
    if not ok:
        ensure_parent_dir(patch_report_json)
        with open(patch_report_json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "success": False,
                    "model_name": model_name,
                    "reason": msg,
                    "raw_response": raw_response,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        return PatchLLMResult(
            success=False,
            analysis_json=str(analysis_json),
            source_script=str(source_script),
            target_script=str(target_script),
            patch_report_json=str(patch_report_json),
            model_name=model_name,
            error_message=msg,
            raw_response_chars=len(raw_response),
        )

    write_text(target_script, code)

    report = {
        "success": True,
        "model_name": model_name,
        "analysis_json": str(analysis_json),
        "source_script": str(source_script),
        "target_script": str(target_script),
        "raw_response_chars": len(raw_response),
        "analysis_summary": analysis.get("summary", {}),
        "analysis_findings": analysis.get("findings", []),
    }

    ensure_parent_dir(patch_report_json)
    with open(patch_report_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return PatchLLMResult(
        success=True,
        analysis_json=str(analysis_json),
        source_script=str(source_script),
        target_script=str(target_script),
        patch_report_json=str(patch_report_json),
        model_name=model_name,
        error_message="",
        raw_response_chars=len(raw_response),
    )


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent

    analysis_json = project_root / "reports" / "analysis" / "risk_labeler_v1_analysis.json"
    source_script = project_root / "scripts" / "risk_labeler_v1.py"
    target_script = project_root / "scripts" / "risk_labeler_v2.py"
    patch_report_json = project_root / "reports" / "patches" / "v1_to_v2_patch_llm.json"

    result = patch_rule_script_with_llm(
        analysis_json=analysis_json,
        source_script=source_script,
        target_script=target_script,
        patch_report_json=patch_report_json,
    )

    print("=" * 60)
    print("PATCH LLM RESULT")
    print("=" * 60)
    print(f"success            : {result.success}")
    print(f"analysis_json      : {result.analysis_json}")
    print(f"source_script      : {result.source_script}")
    print(f"target_script      : {result.target_script}")
    print(f"patch_report_json  : {result.patch_report_json}")
    print(f"model_name         : {result.model_name}")
    print(f"raw_response_chars : {result.raw_response_chars}")
    if result.error_message:
        print(f"error_message      : {result.error_message}")
