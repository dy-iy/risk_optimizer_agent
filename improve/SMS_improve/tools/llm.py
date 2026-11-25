"""DeepSeek 调用、Token 用量和请求耗时。"""

import csv
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from dotenv import load_dotenv
from openai import OpenAI


USAGE_FIELDS = ("输入Token", "输出Token", "总Token")
REQUEST_TIME = "请求耗时（秒）"
LEGACY_TOKEN_FIELDS = ["运行批次", "版本", "阶段", *USAGE_FIELDS, "状态"]
TOKEN_FIELDS = [*LEGACY_TOKEN_FIELDS, REQUEST_TIME]
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
    # 项目根目录位于 improve/<任务>/ 的上两级。
    load_dotenv(Path(folder).resolve().parents[1] / ".env")
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
    _config(folder)


def _read_tokens(path):
    if not path.exists() or path.stat().st_size == 0:
        return None, []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        if fields not in (LEGACY_TOKEN_FIELDS, TOKEN_FIELDS):
            raise ValueError("tokens.csv 表头不符合当前格式，请检查该文件。")
        rows = list(reader)
    for row in rows:
        for field in USAGE_FIELDS:
            row[field] = int(row[field]) if row[field] else None
        row[REQUEST_TIME] = float(row[REQUEST_TIME]) if row.get(REQUEST_TIME) else None
    return fields, rows


def _upgrade_token_log(path, rows):
    # 先完整写入新表，再原子替换旧表，历史请求耗时留空。
    stream = NamedTemporaryFile(dir=path.parent, prefix=".tokens-", suffix=".tmp",
                                mode="w", encoding="utf-8-sig", newline="", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            writer = csv.DictWriter(stream, fieldnames=TOKEN_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _usage_count(usage, name):
    value = getattr(usage, name, None)
    return value if type(value) is int and value >= 0 else None


def _record_tokens(folder, run_id, version_name, stage, usage, status, elapsed):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tokens.csv"
    fields, rows = _read_tokens(path)
    row = {
        "运行批次": run_id,
        "版本": version_name,
        "阶段": stage,
        "输入Token": _usage_count(usage, "prompt_tokens"),
        "输出Token": _usage_count(usage, "completion_tokens"),
        "总Token": _usage_count(usage, "total_tokens"),
        "状态": status,
        REQUEST_TIME: f"{elapsed:.6f}",
    }
    if fields == LEGACY_TOKEN_FIELDS:
        _upgrade_token_log(path, [*rows, row])
    else:
        new_file = fields is None
        with path.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=TOKEN_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
            stream.flush()
            os.fsync(stream.fileno())
    _write_token_summary(folder, run_id)


def call_llm(messages, version_name, stage, folder, run_id, json_mode=False):
    """每次只请求一次；API 已返回的用量先保存，再检查生成内容。"""
    if stage not in STAGES:
        raise ValueError(f"未知模型调用阶段：{stage}")
    config = _config(folder)
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
        started = perf_counter()
        try:
            response = client.chat.completions.create(**request)
        except BaseException as error:
            _record_tokens(folder, run_id, version_name, stage, None, "请求失败，用量未知",
                           perf_counter() - started)
            if not isinstance(error, Exception):
                raise
            # 不把请求头、响应正文或密钥写入错误日志。
            status = getattr(error, "status_code", None)
            detail = f"，HTTP {status}" if isinstance(status, int) else ""
            raise LLMAPIError(f"{stage} 调用失败（{type(error).__name__}{detail}），Token 用量未知。") from None
        elapsed = perf_counter() - started
        _record_tokens(folder, run_id, version_name, stage, getattr(response, "usage", None), "已返回", elapsed)
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


def _total_text(rows, field):
    if not rows:
        return "—"
    known = [row[field] for row in rows if row[field] is not None]
    missing = len(rows) - len(known)
    if not known:
        return f"—（{missing} 次未知）"
    result = f"{sum(known):,.3f}" if field == REQUEST_TIME else f"{sum(known):,}"
    return f"{result}（另 {missing} 次未知）" if missing else result


def _version_summary(rows, field, headers):
    lines = [
        "| 版本 | " + " | ".join(headers) + " | 本轮合计 | 本次累计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    cumulative = []
    for version in dict.fromkeys(row["版本"] for row in rows):
        selected = [row for row in rows if row["版本"] == version]
        cumulative.extend(selected)
        values = [_total_text([row for row in selected if row["阶段"] == stage], field) for stage in STAGES]
        lines.append("| " + " | ".join([version, *values, _total_text(selected, field),
                                         _total_text(cumulative, field)]) + " |")
    return lines


def _write_token_summary(folder, run_id, workflow_timings=()):
    folder = Path(folder)
    _, rows = _read_tokens(folder / "tokens.csv")
    current = [row for row in rows if row["运行批次"] == str(run_id)]
    history = [row for row in rows if row["运行批次"] != str(run_id)]
    lines = ["# Token 用量与耗时", "", f"本次运行批次 `{run_id}`", ""]
    if current:
        lines.extend(_version_summary(current, "总Token", ("分析用量", "分析纠正用量", "修改用量", "LF 修复用量")))
    else:
        lines.append("本次暂无模型调用。")
    lines.extend([
        "", f"本次累计 {_total_text(current, '总Token')} Token（{len(current)} 次调用）。",
        f"历史累计（不含本次） {_total_text(history, '总Token')} Token（{len(history)} 次调用）。",
        f"全部累计 {_total_text(rows, '总Token')} Token（{len(rows)} 次调用）。",
    ])
    incomplete = sum(any(row[field] is None for field in USAGE_FIELDS) for row in rows)
    if incomplete:
        lines.extend(["", f"有 {incomplete} 次调用缺少部分或全部用量；未知字段留空，合计仅统计已知总 Token。"])
    lines.extend(["", "## 模型请求耗时（秒）", ""])
    if current:
        lines.extend(_version_summary(current, REQUEST_TIME, STAGES))
        lines.append("")
    lines.extend([
        f"本次请求累计 {_total_text(current, REQUEST_TIME)} 秒。",
        f"历史请求累计（不含本次） {_total_text(history, REQUEST_TIME)} 秒。",
        f"全部请求累计 {_total_text(rows, REQUEST_TIME)} 秒。",
        "", "请求计时包含失败及纠正请求。旧记录缺失的耗时留空，合计只计算已知值。",
    ])
    if workflow_timings:
        lines.extend(["", "## 工作流耗时（秒）", "", "| 阶段 | 版本 | 状态 | 耗时 |", "| --- | --- | --- | ---: |"])
        lines.extend(
            f"| {row['阶段']} | {row['版本']} | {row['状态']} | {float(row['耗时（秒）']):,.3f} |"
            for row in workflow_timings
        )
    lines.append("")
    result = "\n".join(lines)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tokens.md").write_text(result, encoding="utf-8")
    return result


def show_token_summary(folder, run_id, workflow_timings=()):
    """更新易读汇总，并在终端打印同一张中文表。"""
    print(_write_token_summary(folder, run_id, workflow_timings))
