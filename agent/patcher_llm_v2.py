from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional
from tqdm import tqdm

DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_TEMPERATURE = float(os.environ.get("PATCHER_LLM_TEMPERATURE", "0.2"))
DEFAULT_MAX_RETRIES = int(os.environ.get("PATCHER_LLM_MAX_RETRIES", "2"))
DEFAULT_SOURCE_CODE_LIMIT = int(os.environ.get("PATCHER_SOURCE_CODE_LIMIT", "80000"))
DEFAULT_ANALYSIS_LIMIT = int(os.environ.get("PATCHER_ANALYSIS_LIMIT", "50000"))

class StageProgress:
    def __init__(self, total: int, enabled: bool = True, desc: str = "Patching"):
        self.enabled = enabled
        self.total = total
        self.current = 0
        self.desc = desc
        self.bar = None

        if self.enabled and tqdm is not None:
            self.bar = tqdm(total=total, desc=desc, ncols=100)
        elif self.enabled:
            print(f"[0/{total}] {desc}")

    def update(self, message: str) -> None:
        self.current += 1
        if self.bar is not None:
            self.bar.set_postfix_str(message)
            self.bar.update(1)
        elif self.enabled:
            print(f"[{self.current}/{self.total}] {message}")

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()

@dataclass
class PatchLLMResult:
    success: bool
    source_script: str
    analysis_json: str
    output_script: str
    patch_report_json: str
    patch_report_markdown: str
    model: str
    output_bytes: int = 0
    syntax_ok: bool = False
    error_message: str = ""


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_env_file(env_path: str | Path = ".env") -> None:
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_openai_client():
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("未安装 openai 包。请先执行: pip install openai") from e
    return OpenAI


def extract_json_block(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM 返回为空")

    fenced = re.search(r"```json\s*(\{.*\})\s*```", text, flags=re.S | re.I)
    if fenced:
        return fenced.group(1)

    direct = re.search(r"(\{.*\})", text, flags=re.S)
    if direct:
        return direct.group(1)

    raise ValueError("未从 LLM 返回中提取到 JSON")


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n# [TRUNCATED]"


def resolve_default_source_script(project_root: Path) -> Optional[Path]:
    env_path = os.environ.get("SOURCE_SCRIPT")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    candidates = [
        project_root / "risk_labeler_v1.py",
        project_root / "scripts" / "risk_labeler_v1.py",
        project_root / "src" / "risk_labeler_v1.py",
        project_root / "app" / "risk_labeler_v1.py",
        project_root / "analyzer" / "risk_labeler_v1.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def resolve_default_analysis_json(project_root: Path) -> Path:
    env_path = os.environ.get("ANALYSIS_LLM_JSON")
    if env_path:
        return Path(env_path)
    return project_root / "reports" / "analysis" / "risk_labeler_v1_analysis_llm.json"


def compile_check(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<patched_script>", "exec")
        return True, ""
    except SyntaxError as e:
        msg = f"SyntaxError: {e.msg} at line {e.lineno}, offset {e.offset}"
        if e.text:
            msg += f"\n>>> {e.text.rstrip()}"
        return False, msg
    except Exception as e:
        return False, f"CompileError: {e}"


def sanitize_code_block(code: str) -> str:
    code = (code or "").strip()
    fence = re.match(r"^```(?:python)?\s*(.*?)\s*```$", code, flags=re.S | re.I)
    if fence:
        return fence.group(1).strip() + "\n"
    return code + ("\n" if code and not code.endswith("\n") else "")


def build_patch_prompt(source_code: str, analysis_data: dict) -> tuple[str, str]:
    analysis_str = json.dumps(analysis_data, ensure_ascii=False, indent=2)

    system_prompt = (
        "你是一个专门给加密货币新闻风险规则系统打补丁的 Python 工程师。"
        "你会根据 LLM 错误分析报告，直接产出一份完整、可运行、结构尽量稳定的新脚本。"
        "必须优先做证据充分的修改，不要随意重构，不要引入不必要的新依赖。"
        "输出必须是纯 JSON，不要输出 markdown，不要解释。"
    )

    user_prompt = f"""
你将收到两份输入：
1. 当前版本的完整 Python 规则脚本
2. 基于误报/漏报/类型错配做出的 analysis_llm.json

你的目标：
- 直接生成“完整的新脚本代码”，而不是只给修改建议。
- 只根据 analysis_llm.json 中证据充分的部分做修改。
- 尽量保留原脚本的整体结构、函数名、输入输出接口、列名和主流程。
- 不要改变 CSV 输入输出协议，除非 analysis 明确要求。
- 不要依赖除标准库和 pandas 之外的新第三方包。
- 修改重点应该落在：关键词范围、否定词、金额门槛、阈值、强弱触发、主类别逻辑、英文大小写归一化等。
- 如果 analysis 证据不足，就少改，不要硬改。
- 生成的 full_code 必须是完整、可运行、可保存为 .py 的脚本。

输出 JSON schema：
{{
  "patch_version": "v2.1 或类似版本号",
  "summary": ["一句话总结1", "一句话总结2"],
  "changes": [
    {{
      "target": "score_whale / score_liquidation / shared logic 等",
      "reason": "为什么改",
      "action": "做了什么改动",
      "risk": "low / medium / high"
    }}
  ],
  "validation_notes": ["注意点1", "注意点2"],
  "full_code": "完整 Python 代码字符串"
}}

当前脚本：
```python
{source_code}
```

analysis_llm.json：
```json
{analysis_str}
```
""".strip()

    return system_prompt, user_prompt


def build_repair_prompt(bad_code: str, compile_error: str) -> tuple[str, str]:
    system_prompt = (
        "你是一个 Python 修复助手。"
        "你会在不改变业务逻辑的前提下，最小化修复代码中的语法/结构问题。"
        "输出必须是纯 JSON，不要输出 markdown。"
    )

    user_prompt = f"""
下面这份由 LLM 生成的 Python 代码编译失败了。
请只修复导致编译失败的问题，尽量不要改变业务逻辑。

编译错误：
{compile_error}

坏代码：
```python
{bad_code}
```

输出 JSON schema：
{{
  "repair_summary": ["修了什么"],
  "full_code": "修复后的完整 Python 代码字符串"
}}
""".strip()
    return system_prompt, user_prompt


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    load_env_file()
    OpenAI = require_openai_client()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("运行前请先设置 DEEPSEEK_API_KEY（可放在 .env 文件中）。")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    client = OpenAI(api_key=api_key, base_url=base_url)

    last_error: Optional[Exception] = None
    for _ in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = resp.choices[0].message.content or ""
            return json.loads(extract_json_block(content))
        except Exception as e:
            last_error = e

    raise RuntimeError(f"LLM 调用失败: {last_error}")


def render_patch_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# 规则脚本 Patch 报告（LLM版）")
    lines.append("")

    patch_version = report.get("patch_version", "")
    if patch_version:
        lines.append(f"**Patch 版本**: {patch_version}")
        lines.append("")

    lines.append("## 摘要")
    lines.append("")
    for item in report.get("summary", []) or []:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 变更明细")
    lines.append("")
    for i, item in enumerate(report.get("changes", []) or [], start=1):
        lines.append(f"### {i}. {item.get('target', '未命名目标')}")
        if item.get("reason"):
            lines.append(f"- 原因: {item.get('reason')}" )
        if item.get("action"):
            lines.append(f"- 动作: {item.get('action')}" )
        if item.get("risk"):
            lines.append(f"- 风险: {item.get('risk')}" )
        lines.append("")

    notes = report.get("validation_notes", []) or []
    if notes:
        lines.append("## 注意事项")
        lines.append("")
        for item in notes:
            lines.append(f"- {item}")
        lines.append("")

    meta = report.get("meta", {}) or {}
    if meta:
        lines.append("## 元信息")
        lines.append("")
        for k, v in meta.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def patch_script_with_llm(
    source_script: str | Path,
    analysis_json: str | Path,
    output_script: str | Path,
    patch_report_json: str | Path,
    patch_report_markdown: str | Path,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    show_progress: bool = True,
) -> PatchLLMResult:
    source_script = Path(source_script).resolve()
    analysis_json = Path(analysis_json).resolve()
    output_script = Path(output_script).resolve()
    patch_report_json = Path(patch_report_json).resolve()
    patch_report_markdown = Path(patch_report_markdown).resolve()

    progress = StageProgress(total=9, enabled=show_progress, desc="Patcher LLM")

    try:
        # 1) 检查输入文件
        if not source_script.exists():
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=f"source_script 不存在: {source_script}",
            )

        if not analysis_json.exists():
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message=f"analysis_json 不存在: {analysis_json}",
            )
        progress.update("输入文件检查完成")

        # 2) 读取源脚本
        source_code = truncate_text(read_text_safe(source_script), DEFAULT_SOURCE_CODE_LIMIT)
        progress.update("源脚本读取完成")

        # 3) 读取 analysis_json
        analysis_data = json.loads(read_text_safe(analysis_json))
        analysis_data_str = json.dumps(analysis_data, ensure_ascii=False)
        if len(analysis_data_str) > DEFAULT_ANALYSIS_LIMIT:
            analysis_data = json.loads(truncate_text(analysis_data_str, DEFAULT_ANALYSIS_LIMIT))
        progress.update("analysis_json 读取完成")

        # 4) 构造 prompt
        system_prompt, user_prompt = build_patch_prompt(
            source_code=source_code,
            analysis_data=analysis_data,
        )
        progress.update("patch prompt 构建完成")

        # 5) 首轮 LLM patch
        llm_report = call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=temperature,
        )
        progress.update("LLM 首轮 patch 完成")

        full_code = sanitize_code_block(str(llm_report.get("full_code", "")))
        if not full_code.strip():
            progress.close()
            return PatchLLMResult(
                success=False,
                source_script=str(source_script),
                analysis_json=str(analysis_json),
                output_script=str(output_script),
                patch_report_json=str(patch_report_json),
                patch_report_markdown=str(patch_report_markdown),
                model=model,
                error_message="LLM 未返回 full_code",
            )

        # 6) 语法检查 / 修复
        syntax_ok, compile_error = compile_check(full_code)
        if not syntax_ok:
            repair_system, repair_user = build_repair_prompt(full_code, compile_error)
            repaired = call_llm_json(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=model,
                temperature=0.0,
            )
            repaired_code = sanitize_code_block(str(repaired.get("full_code", "")))
            if repaired_code.strip():
                full_code = repaired_code
                syntax_ok, compile_error = compile_check(full_code)
                llm_report.setdefault("validation_notes", [])
                llm_report["validation_notes"] = list(llm_report.get("validation_notes", []) or []) + list(
                    repaired.get("repair_summary", []) or []
                )
            progress.update("语法修复完成")
        else:
            progress.update("语法检查通过")

        # 7) 写 output_script
        llm_report["meta"] = {
            "model": model,
            "syntax_ok": syntax_ok,
            "source_script": str(source_script),
            "analysis_json": str(analysis_json),
        }
        if compile_error:
            llm_report["meta"]["compile_error"] = compile_error

        ensure_parent_dir(output_script)
        output_script.write_text(full_code, encoding="utf-8")
        progress.update("output_script 写入完成")

        # 8) 写 patch_report_json
        ensure_parent_dir(patch_report_json)
        patch_report_json.write_text(
            json.dumps(llm_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        progress.update("patch_report_json 写入完成")

        # 9) 写 patch_report_markdown
        ensure_parent_dir(patch_report_markdown)
        patch_report_markdown.write_text(
            render_patch_markdown(llm_report),
            encoding="utf-8",
        )
        progress.update("patch_report_markdown 写入完成")

        progress.close()

        return PatchLLMResult(
            success=syntax_ok,
            source_script=str(source_script),
            analysis_json=str(analysis_json),
            output_script=str(output_script),
            patch_report_json=str(patch_report_json),
            patch_report_markdown=str(patch_report_markdown),
            model=model,
            output_bytes=len(full_code.encode("utf-8")),
            syntax_ok=syntax_ok,
            error_message="" if syntax_ok else f"生成脚本仍未通过语法检查: {compile_error}",
        )
    except Exception:
        progress.close()
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch crypto risk rule script with LLM using analysis_llm.json")
    parser.add_argument("--source-script", type=str, default="")
    parser.add_argument("--analysis-json", type=str, default="")
    parser.add_argument("--output-script", type=str, default="")
    parser.add_argument("--patch-report-json", type=str, default="")
    parser.add_argument("--patch-report-markdown", type=str, default="")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent

    default_source = resolve_default_source_script(project_root)
    source_script = Path(args.source_script) if args.source_script else default_source
    analysis_json = Path(args.analysis_json) if args.analysis_json else resolve_default_analysis_json(project_root)
    output_script = Path(args.output_script) if args.output_script else project_root / "reports" / "patched" / "risk_labeler_v2_llm.py"
    patch_report_json = Path(args.patch_report_json) if args.patch_report_json else project_root / "reports" / "patched" / "risk_labeler_v2_patch_report.json"
    patch_report_markdown = Path(args.patch_report_markdown) if args.patch_report_markdown else project_root / "reports" / "patched" / "risk_labeler_v2_patch_report.md"

    if source_script is None:
        result = PatchLLMResult(
            success=False,
            source_script="",
            analysis_json=str(analysis_json),
            output_script=str(output_script),
            patch_report_json=str(patch_report_json),
            patch_report_markdown=str(patch_report_markdown),
            model=args.model,
            error_message="未找到默认 source_script，请通过 --source-script 显式传入。",
        )
    else:
        result = patch_script_with_llm(
            source_script=source_script,
            analysis_json=analysis_json,
            output_script=output_script,
            patch_report_json=patch_report_json,
            patch_report_markdown=patch_report_markdown,
            model=args.model,
            temperature=args.temperature,
            show_progress=True,
        )

    print("=" * 60)
    print("PATCH LLM RESULT")
    print("=" * 60)
    print(f"success               : {result.success}")
    print(f"model                 : {result.model}")
    print(f"source_script         : {result.source_script}")
    print(f"analysis_json         : {result.analysis_json}")
    print(f"output_script         : {result.output_script}")
    print(f"patch_report_json     : {result.patch_report_json}")
    print(f"patch_report_markdown : {result.patch_report_markdown}")
    print(f"output_bytes          : {result.output_bytes}")
    print(f"syntax_ok             : {result.syntax_ok}")
    if result.error_message:
        print(f"error_message         : {result.error_message}")

    print("\nResult JSON:")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
