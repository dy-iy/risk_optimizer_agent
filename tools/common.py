from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm import tqdm


class StageProgress:
    def __init__(self, total: int, enabled: bool = True, desc: str = "Working"):
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


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def read_json_file(path: Path) -> dict:
    return json.loads(read_text_file(path))


def write_json_file(path: Path, data: Any) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_csv_file(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(path)


def write_csv_file(df: pd.DataFrame, path: Path) -> None:
    ensure_parent_dir(path)
    df.to_csv(path, index=False, encoding="utf-8-sig")


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
    except Exception as exc:
        raise RuntimeError("未安装 openai 包。请先执行: pip install openai") from exc
    return OpenAI


def create_openai_client(default_base_url: str):
    load_env_file()
    OpenAI = require_openai_client()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("运行前请先设置 DEEPSEEK_API_KEY（可放在 .env 文件中）。")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", default_base_url)
    return OpenAI(api_key=api_key, base_url=base_url)


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


def call_chat_json(
    *,
    messages: list[dict[str, str]],
    model: str,
    default_base_url: str,
    temperature: float = 0.2,
    max_retries: int = 2,
    error_prefix: str = "LLM 调用失败",
) -> dict:
    client = create_openai_client(default_base_url)

    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=messages,
            )
            content = resp.choices[0].message.content or ""
            return json.loads(extract_json_block(content))
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"{error_prefix}: {last_error}")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def normalize_str_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return df[col].fillna("").astype(str).str.strip()
