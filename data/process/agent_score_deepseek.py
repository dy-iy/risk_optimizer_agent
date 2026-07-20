from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prompts.LLM_labeler import (
    ALLOWED_RISK_TYPES,
    NO_OBVIOUS_RISK,
    USER_PROMPT_TEMPLATE,
    build_system_prompt,
)

load_dotenv()

@dataclass(frozen=True)
class RunConfig:
    input_csv: str = "data/input/raw_1000_news.csv"
    output_csv: str = "data/process/output/LLM_label_2.csv"
    content_col: str = "内容"
    id_col: str = "新闻id"
    time_col: str = "时间"
    model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    temperature: float = 0.0
    max_tokens: int = 700
    max_rows: Optional[int] = None
    max_workers: int = 20
    max_retries: int = 3
    retry_sleep_seconds: float = 2.0


# ===== 在这里改路径和运行参数 =====
CONFIG = RunConfig()


def _load_langchain_components():
    try:
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "缺少 LangChain 依赖，请先安装：pip install langchain langchain-openai"
        ) from exc

    return ChatOpenAI, ChatPromptTemplate, JsonOutputParser


def read_csv_auto(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path)


class CryptoRiskLabelAgent:
    """使用 LangChain + DeepSeek API 对加密货币新闻做风险标注。"""

    def __init__(self, config: RunConfig) -> None:
        load_dotenv()

        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("未读取到 DEEPSEEK_API_KEY 或 OPENAI_API_KEY，请检查 .env 文件。")

        ChatOpenAI, ChatPromptTemplate, JsonOutputParser = _load_langchain_components()

        self.max_retries = config.max_retries
        self.sleep_between_retries = config.retry_sleep_seconds
        self.allowed_risk_types = ALLOWED_RISK_TYPES
        self.system_prompt = build_system_prompt()

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{system_prompt}"),
                ("user", USER_PROMPT_TEMPLATE),
            ]
        )
        llm = ChatOpenAI(
            model=config.model,
            api_key=api_key,
            base_url=config.base_url,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        self.chain = prompt | llm | JsonOutputParser()

    def _normalize_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        risk_score = max(0, min(100, self._safe_int(result.get("risk_score", 0), default=0)))
        risk_types = self._normalize_risk_types(result.get("risk_types", []))
        primary_risk_type = self._normalize_primary_type(
            result.get("primary_risk_type", NO_OBVIOUS_RISK),
            risk_types,
        )

        return {
            "risk_score": risk_score,
            "risk_label": self._score_to_label(risk_score),
            "risk_types": risk_types,
            "primary_risk_type": primary_risk_type,
            "reason": self._clean_text(
                result.get("reason", ""),
                default="新闻未体现明确高危负面事件，整体风险有限。",
            ),
            "confidence": self._safe_float(result.get("confidence", 0.5), default=0.5),
            "summary": self._clean_text(result.get("summary", ""), default="未提供摘要"),
        }

    def _normalize_risk_types(self, risk_types: Any) -> List[str]:
        if not isinstance(risk_types, list):
            return []

        filtered = [
            item
            for item in risk_types
            if item in self.allowed_risk_types
        ]
        return list(dict.fromkeys(filtered))

    @staticmethod
    def _normalize_primary_type(primary_risk_type: Any, risk_types: List[str]) -> str:
        primary = str(primary_risk_type).strip() if primary_risk_type else NO_OBVIOUS_RISK
        if not risk_types:
            return NO_OBVIOUS_RISK
        if primary == NO_OBVIOUS_RISK or primary not in risk_types:
            return risk_types[0]
        return primary

    @staticmethod
    def _score_to_label(score: int) -> str:
        if score <= 39:
            return "low"
        if score <= 69:
            return "medium"
        return "high"

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(round(float(value)))
        except Exception:
            return default

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            value = float(value)
        except Exception:
            value = default
        return max(0.0, min(1.0, value))

    @staticmethod
    def _clean_text(value: Any, default: str) -> str:
        if not isinstance(value, str):
            value = str(value)
        return value.strip() or default

    def _fallback_result(self, reason: str, confidence: float = 0.0) -> Dict[str, Any]:
        return {
            "risk_score": 0,
            "risk_label": "low",
            "risk_types": [],
            "primary_risk_type": NO_OBVIOUS_RISK,
            "reason": reason,
            "confidence": confidence,
            "summary": "调用失败",
        }

    async def alabel_news(self, news_text: str) -> Dict[str, Any]:
        news_text = str(news_text).strip()
        if not news_text:
            return {
                "risk_score": 0,
                "risk_label": "low",
                "risk_types": [],
                "primary_risk_type": NO_OBVIOUS_RISK,
                "reason": "新闻内容为空。",
                "confidence": 1.0,
                "summary": "空内容",
            }

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                parsed = await self.chain.ainvoke(
                    {
                        "system_prompt": self.system_prompt,
                        "news_text": news_text,
                    }
                )
                return self._normalize_result(parsed)
            except Exception as exc:
                last_err = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self.sleep_between_retries)

        return self._fallback_result(f"模型调用失败：{last_err}")

    def label_news(self, news_text: str) -> Dict[str, Any]:
        return asyncio.run(self.alabel_news(news_text))


async def _label_row_async(
    position: int,
    row: Dict[str, Any],
    agent: CryptoRiskLabelAgent,
    config: RunConfig,
) -> Tuple[int, Dict[str, Any]]:
    content = row.get(config.content_col, "")
    result = await agent.alabel_news(content)

    return position, {
        config.id_col: row.get(config.id_col),
        config.time_col: row.get(config.time_col),
        config.content_col: content,
        "risk_score": result["risk_score"],
        "risk_label": result["risk_label"],
        "risk_types": json.dumps(result["risk_types"], ensure_ascii=False),
        "primary_risk_type": result["primary_risk_type"],
        "reason": result["reason"],
        "confidence": result["confidence"],
        "summary": result["summary"],
    }


def _append_ordered_rows(
    output_path: Path,
    rows: List[Dict[str, Any]],
    wrote_header: bool,
) -> bool:
    if not rows:
        return wrote_header

    pd.DataFrame(rows).to_csv(
        output_path,
        mode="a",
        header=not wrote_header,
        index=False,
        encoding="utf-8-sig",
    )
    return True


async def process_csv_async(
    config: RunConfig = CONFIG,
) -> None:
    df = read_csv_auto(config.input_csv)
    if config.max_rows is not None:
        df = df.head(config.max_rows)
    if config.content_col not in df.columns:
        raise ValueError(f"输入文件中未找到内容列：{config.content_col}")

    output_path = Path(config.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    records = df.to_dict("records")
    concurrency = max(1, min(config.max_workers, len(records) or 1))
    agent = CryptoRiskLabelAgent(config)

    next_to_write = 0
    next_to_submit = 0
    pending: Dict[int, Dict[str, Any]] = {}
    in_flight: set[asyncio.Task[Tuple[int, Dict[str, Any]]]] = set()
    wrote_header = False

    def submit_next() -> None:
        nonlocal next_to_submit
        if next_to_submit >= len(records):
            return
        task = asyncio.create_task(
            _label_row_async(
                next_to_submit,
                records[next_to_submit],
                agent,
                config,
            )
        )
        in_flight.add(task)
        next_to_submit += 1

    try:
        for _ in range(concurrency):
            submit_next()

        with tqdm(total=len(records), desc=f"异步标注新闻中({concurrency} concurrent)") as bar:
            while in_flight:
                done, in_flight = await asyncio.wait(
                    in_flight,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    position, row_data = task.result()
                    pending[position] = row_data
                    bar.update(1)
                    submit_next()

                ordered_rows: List[Dict[str, Any]] = []
                while next_to_write in pending:
                    ordered_rows.append(pending.pop(next_to_write))
                    next_to_write += 1

                wrote_header = _append_ordered_rows(output_path, ordered_rows, wrote_header)

    except KeyboardInterrupt:
        for task in in_flight:
            task.cancel()
        print("\n检测到手动中断，已保存当前已完成且可按原始顺序写入的数据。")
        return

    print(f"标注完成，结果已保存到：{config.output_csv}")


def process_csv(config: RunConfig = CONFIG) -> None:
    asyncio.run(process_csv_async(config))


if __name__ == "__main__":
    process_csv(CONFIG)
