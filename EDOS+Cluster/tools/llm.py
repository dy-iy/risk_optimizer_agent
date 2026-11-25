"""DeepSeek 调用和按次记录的 Token 用量。"""

import csv
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


TOKEN_FIELDS = ["运行批次", "版本", "阶段", "输入Token", "输出Token", "总Token", "状态"]
STAGES = ("Analyzer", "Analyzer 修复", "Patcher", "Patcher 修复")


class LLMAPIError(RuntimeError):
    """网络、鉴权等请求失败，不作为 LF 代码错误重试。"""


class LLMResponseError(RuntimeError):
    """API 已返回，但文本为空或生成被截断；保留文本供上层修复。"""

    def __init__(self, message, content="", finish_reason=None):
        super().__init__(message)
        self.content = content
        self.finish_reason = finish_reason


def _config(folder):
    # task_folder 的上一级是项目根目录，输出可单独写入运行目录。
    load_dotenv(Path(folder).resolve().parent / ".env")
    names = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
    values = {name: os.getenv(name, "").strip() for name in names}
    # 与 v4 一致：未显式配置地址时，使用官方默认地址。
    values["DEEPSEEK_BASE_URL"] = values["DEEPSEEK_BASE_URL"] or "https://api.deepseek.com"
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("请在项目根目录 .env 配置：" + "、".join(missing))
    return values


def check_config(folder):
    """启动前检查配置，不请求 API，也不返回密钥。"""
    config = _config(folder)
    return {"model": config["DEEPSEEK_MODEL"], "base_url": config["DEEPSEEK_BASE_URL"], "temperature": 0.1}


def _read_tokens(folder):
    path = Path(folder) / "tokens.csv"
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != TOKEN_FIELDS:
            raise ValueError("tokens.csv 表头不符合当前格式，请检查该文件。")
        rows = list(reader)
    for row in rows:
        for field in ("输入Token", "输出Token", "总Token"):
            row[field] = int(row[field]) if row[field] else None
    return rows


def _usage_count(usage, name):
    value = getattr(usage, name, None)
    return value if type(value) is int and value >= 0 else None


def _record_tokens(folder, run_id, version_name, stage, usage, status):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tokens.csv"
    # 先检查已有表头，防止向不匹配的历史表静默追加。
    if path.exists():
        _read_tokens(folder)
    new_file = not path.exists() or path.stat().st_size == 0
    row = {
        "运行批次": run_id,
        "版本": version_name,
        "阶段": stage,
        "输入Token": _usage_count(usage, "prompt_tokens"),
        "输出Token": _usage_count(usage, "completion_tokens"),
        "总Token": _usage_count(usage, "total_tokens"),
        "状态": status,
    }
    with path.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TOKEN_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        stream.flush()
        os.fsync(stream.fileno())
    _write_token_summary(folder, run_id)


def call_llm(messages, version_name, stage, folder, run_id, json_mode=False, *, task_folder=None):
    """每次只请求一次；API 已返回的用量先保存，再检查生成内容。"""
    if stage not in STAGES:
        raise ValueError(f"未知模型调用阶段：{stage}")
    config = _config(task_folder or folder)
    request = {
        "model": config["DEEPSEEK_MODEL"],
        "messages": messages,
        "temperature": 0.1,
    }
    if json_mode:
        request["response_format"] = {"type": "json_object"}
    with OpenAI(api_key=config["DEEPSEEK_API_KEY"],
                base_url=config["DEEPSEEK_BASE_URL"],
                max_retries=0, timeout=120) as client:
        try:
            response = client.chat.completions.create(**request)
        except BaseException as error:
            _record_tokens(folder, run_id, version_name, stage, None, "请求失败，用量未知")
            if not isinstance(error, Exception):
                raise
            # 不把请求头、响应正文或密钥写入错误日志。
            status = getattr(error, "status_code", None)
            detail = f"，HTTP {status}" if isinstance(status, int) else ""
            raise LLMAPIError(f"{stage} 调用失败（{type(error).__name__}{detail}），Token 用量未知。") from None
        _record_tokens(folder, run_id, version_name, stage, getattr(response, "usage", None), "已返回")
    if not response.choices:
        raise LLMResponseError(f"{stage} 未返回候选内容，Token 用量已记录。")
    choice = response.choices[0]
    content = choice.message.content or ""
    if choice.finish_reason not in ("stop", None):
        raise LLMResponseError(
            f"{stage} 生成未正常结束（{choice.finish_reason}），Token 用量已记录。",
            content, choice.finish_reason)
    if not content.strip():
        raise LLMResponseError(f"{stage} 返回空文本，Token 用量已记录。", content, choice.finish_reason)
    return content


def _total_text(rows):
    if not rows:
        return "—"
    known = [row["总Token"] for row in rows if row["总Token"] is not None]
    missing = len(rows) - len(known)
    if not known:
        return f"—（{missing} 次未知）"
    result = f"{sum(known):,}"
    return f"{result}（另 {missing} 次未知）" if missing else result


def _write_token_summary(folder, run_id):
    folder = Path(folder)
    rows = _read_tokens(folder)
    current = [row for row in rows if row["运行批次"] == str(run_id)]
    history = [row for row in rows if row["运行批次"] != str(run_id)]
    lines = ["# Token 用量", "", f"本次运行批次：`{run_id}`", ""]
    if current:
        lines.extend([
            "| 版本 | 分析用量 | 分析纠正用量 | 修改用量 | LF 修复用量 | 本轮合计 | 本次累计 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        cumulative = []
        for version in dict.fromkeys(row["版本"] for row in current):
            selected = [row for row in current if row["版本"] == version]
            cumulative.extend(selected)
            values = [_total_text([row for row in selected if row["阶段"] == stage]) for stage in STAGES]
            lines.append("| " + " | ".join([version, *values, _total_text(selected), _total_text(cumulative)]) + " |")
    else:
        lines.append("本次暂无模型调用。")
    lines.extend([
        "", f"本次累计：{_total_text(current)} Token（{len(current)} 次调用）。",
        f"历史累计（不含本次）：{_total_text(history)} Token（{len(history)} 次调用）。",
        f"全部累计：{_total_text(rows)} Token（{len(rows)} 次调用）。",
    ])
    incomplete = sum(any(row[field] is None for field in ("输入Token", "输出Token", "总Token")) for row in rows)
    if incomplete:
        lines.extend(["", f"有 {incomplete} 次调用缺少部分或全部用量；未知字段留空，合计仅统计已知总 Token。"])
    lines.extend(["", "表中无调用的阶段显示“—”；CSV 中未知用量留空，数值不使用千分位。", ""])
    result = "\n".join(lines)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tokens.md").write_text(result, encoding="utf-8")
    return result


def show_token_summary(folder, run_id):
    """更新易读汇总，并在终端打印同一张中文表。"""
    print(_write_token_summary(folder, run_id))
