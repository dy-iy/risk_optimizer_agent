from __future__ import annotations

import asyncio
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv()

NO_OBVIOUS_RISK = "无明显风险"

ALLOWED_RISK_TYPES = [
    "链上漏洞 / 攻击风险",
    "诈骗 / 跑路 / Rug Pull 风险",
    "监管与法律风险",
    "交易所与系统运维风险",
    "稳定币异常风险",
    "爆仓 / 清算风险",
    "大额转账 / 巨鲸行为风险",
    "异常行情波动风险",
    "项目治理 / 团队异常风险",
    "偿付能力 / 储备 / 流动性风险",
    "基础设施 / 协议层异常风险",
    "宏观 / 政策冲击风险",
    NO_OBVIOUS_RISK,
]


ADJUDICATOR_SYSTEM_PROMPT = f"""
你是一个加密货币新闻风险标注的最终审核员 / 裁决员。

你的任务不是简单相信 LLMa 或 LLMb，也不是对两个模型的分数取平均。
你必须首先理解新闻内容本身，然后基于新闻中的事实证据，裁决最终的风险类别、风险分数和理由。

你需要遵守以下原则：

1. 严格基于新闻事实
- 只能根据新闻文本中明确出现的信息进行判断。
- 不要根据常识、联想、市场传闻或外部知识补充新闻中没有写出的风险。
- 如果新闻只是普通公告、观点、合作、上线、融资、产品更新等，且没有明显负面风险，应判定为“无明显风险”。

2. 独立裁决，而不是模型投票
- LLMa 和 LLMb 的标注只是参考信息。
- 如果两者都错，你必须给出新的裁决。
- 如果一方明显更符合新闻事实，可以采纳该方。
- 如果两方各有部分合理之处，可以综合后裁决。
- 不允许因为两个模型都标了风险就默认有风险。

3. 风险类别必须和新闻事实直接对应

可选风险类别只能来自以下列表：
{json.dumps(ALLOWED_RISK_TYPES, ensure_ascii=False, indent=2)}

4. 风险分数按 0-10 分裁决

0 分：无明显风险。新闻中没有实质性负面事件或风险信号。
1-2 分：轻微风险。存在弱风险信号、间接影响、一般提醒、轻度不确定性。
3-4 分：中低风险。存在明确负面信息，但影响范围有限，尚未造成严重后果。
5-6 分：中高风险。存在较明确风险事件，可能影响项目、交易、资金安全或市场情绪。
7-8 分：高风险。已发生严重负面事件，例如攻击、执法、清算、脱锚、停服、大额异常等。
9-10 分：极高风险。涉及重大损失、系统性冲击、广泛用户受损、项目崩盘、严重法律后果等。

5. 类别和分数必须一致
- 如果类别为“无明显风险”，分数必须为 0。
- 如果分数为 0，类别必须为“无明显风险”。
- 不要给普通中性新闻强行打高分。
- 不要因为出现“上涨、下跌、价格、交易量”等词就自动标为异常行情波动风险。
- 不要因为出现“监管、法院、SEC、法律”等词就自动标为监管风险，必须看是否真的存在处罚、调查、诉讼、限制、禁令或政策冲击。
- 不要因为出现“大额转账、钱包、交易所地址”等词就自动标为巨鲸风险，必须看是否具有异常性或潜在市场影响。
- 不要因为出现“攻击、漏洞、黑客”等历史背景词就自动标为链上漏洞风险，必须看新闻是否在描述当前或具体风险事件。

6. 分歧裁决方法
你需要依次判断：
- 新闻核心事件是什么？
- 这个事件是否真的构成风险？
- 风险是否已经发生，还是只是猜测？
- 风险影响对象是谁：用户资金、项目方、交易所、协议、稳定币、市场、监管环境？
- LLMa 的类别和分数是否有事实依据？
- LLMb 的类别和分数是否有事实依据？
- 最终应该保留、修正、合并，还是全部否定？

7. 多风险处理
- 如果新闻确实包含多个明确风险，可以输出多个风险类别。
- 但不要为了覆盖而滥标。
- 只有新闻文本中有直接证据支持的类别才能保留。
- 如果存在主风险和次风险，分数应主要反映主风险强度。

8. 输出要求
你必须严格输出 JSON。
不要输出 markdown。
不要输出解释性文本。
不要输出 JSON 之外的任何内容。

输出 JSON 格式必须如下：

{{
  "final_risk_score": 0,
  "final_risk_types": ["无明显风险"],
  "final_primary_risk_type": "无明显风险",
  "decision": "adopt_a / adopt_b / merge / override_both",
  "confidence": "high / medium / low",
  "evidence": [
    "新闻中支持裁决的关键事实1",
    "新闻中支持裁决的关键事实2"
  ],
  "reasoning": "简要说明为什么这样裁决，尤其要说明为什么采纳或否定 LLMa / LLMb 的标注。",
  "llm_a_assessment": {{
    "is_reasonable": true,
    "problem": "如果合理写'基本合理'；如果不合理，说明其错误，例如过度标注、类别不匹配、分数过高、忽略事实等。"
  }},
  "llm_b_assessment": {{
    "is_reasonable": true,
    "problem": "如果合理写'基本合理'；如果不合理，说明其错误，例如过度标注、类别不匹配、分数过高、忽略事实等。"
  }}
}}
""".strip()


USER_PROMPT_TEMPLATE = """
请对以下加密货币新闻风险标注分歧进行最终裁决。

新闻内容：
{news_text}

LLMa 的标注结果：
{llm_a_result}

LLMb 的标注结果：
{llm_b_result}

请你先理解新闻事实，再判断 LLMa 和 LLMb 哪些地方合理、哪些地方不合理。
最后输出最终风险类别和风险分数。

注意：
- 不要机械取平均分。
- 不要因为两个模型都标了风险就默认有风险。
- 如果新闻没有明确风险，最终必须判为“无明显风险”，分数为 0。
- 必须严格输出 JSON。
""".strip()


@dataclass(frozen=True)
class RunConfig:
    input_csv: str = "data/process/output/consistency_check/need_llm_adjudication.csv"
    output_csv: str = "data/process/output/consistency_check/adjudicated_llm_result.csv"
    content_col: str = "内容"
    id_col: str = "新闻id"
    time_col: str = "时间"
    a_score_col: str = "a_risk_score"
    a_types_col: str = "a_risk_types"
    a_primary_col: str = "a_primary_risk_type"
    a_reason_col: str = "a_reason"
    b_score_col: str = "b_risk_score"
    b_types_col: str = "b_risk_types"
    b_primary_col: str = "b_primary_risk_type"
    b_reason_col: str = "b_reason"
    model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    temperature: float = 0.0
    max_tokens: int = 1200
    max_rows: Optional[int] = None
    max_workers: int = 10
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


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        is_na = pd.isna(value)
    except Exception:
        is_na = False
    try:
        if bool(is_na):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _build_llm_result(
    row: Dict[str, Any],
    score_col: str,
    types_col: str,
    primary_col: str,
    reason_col: str,
) -> str:
    data = {
        "risk_score": _json_safe(row.get(score_col)),
        "risk_types": _json_safe(row.get(types_col)),
        "primary_risk_type": _json_safe(row.get(primary_col)),
        "reason": _json_safe(row.get(reason_col)),
    }
    return json.dumps(data, ensure_ascii=False)


class _OpenAIJsonChain:
    """Fallback chain used when LangChain is not installed."""

    def __init__(self, config: RunConfig, api_key: str) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    async def ainvoke(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            news_text=inputs["news_text"],
            llm_a_result=inputs["llm_a_result"],
            llm_b_result=inputs["llm_b_result"],
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": inputs["system_prompt"]},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.S)
            if not match:
                raise ValueError(f"模型输出中找不到 JSON: {content[:500]}")
            return json.loads(match.group(0))


class AdjudicatorAgent:
    """使用 LangChain + DeepSeek API 对两份风险标注结果做最终裁决。"""

    def __init__(self, config: RunConfig) -> None:
        load_dotenv()

        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("未读取到 DEEPSEEK_API_KEY 或 OPENAI_API_KEY，请检查 .env 文件。")

        self.max_retries = config.max_retries
        self.sleep_between_retries = config.retry_sleep_seconds

        try:
            ChatOpenAI, ChatPromptTemplate, JsonOutputParser = _load_langchain_components()
        except ImportError:
            self.chain = _OpenAIJsonChain(config, api_key)
            return

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
        score = self._safe_float(result.get("final_risk_score", 0), default=0.0)
        score = max(0.0, min(10.0, score))
        if score.is_integer():
            score = int(score)

        risk_types = self._normalize_risk_types(result.get("final_risk_types"))
        if score == 0:
            risk_types = [NO_OBVIOUS_RISK]
        elif NO_OBVIOUS_RISK in risk_types and len(risk_types) > 1:
            risk_types = [item for item in risk_types if item != NO_OBVIOUS_RISK]
        elif risk_types == [NO_OBVIOUS_RISK] and score > 0:
            score = 0
            risk_types = [NO_OBVIOUS_RISK]

        primary = str(result.get("final_primary_risk_type", "")).strip()
        if score == 0:
            primary = NO_OBVIOUS_RISK
        elif primary not in risk_types or primary == NO_OBVIOUS_RISK:
            primary = next(
                (risk_type for risk_type in risk_types if risk_type != NO_OBVIOUS_RISK),
                NO_OBVIOUS_RISK,
            )

        decision = str(result.get("decision", "override_both")).strip()
        if decision not in {"adopt_a", "adopt_b", "merge", "override_both"}:
            decision = "override_both"

        confidence = str(result.get("confidence", "medium")).strip()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"

        return {
            "final_risk_score": score,
            "final_risk_types": risk_types,
            "final_primary_risk_type": primary,
            "decision": decision,
            "confidence": confidence,
            "evidence": self._normalize_string_list(result.get("evidence", [])),
            "reasoning": self._clean_text(result.get("reasoning", "")),
            "llm_a_assessment": self._normalize_assessment(result.get("llm_a_assessment", {})),
            "llm_b_assessment": self._normalize_assessment(result.get("llm_b_assessment", {})),
            "adjudicator_raw": json.dumps(result, ensure_ascii=False),
            "adjudicator_error": "",
        }

    def _normalize_risk_types(self, value: Any) -> List[str]:
        if value is None:
            return [NO_OBVIOUS_RISK]

        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return [NO_OBVIOUS_RISK]
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else [text]
            except Exception:
                items = re.split(r"[,，;；、|]+", text)
        else:
            items = [str(value)]

        filtered = []
        for item in items:
            item = str(item).strip()
            if item in ALLOWED_RISK_TYPES and item not in filtered:
                filtered.append(item)

        return filtered or [NO_OBVIOUS_RISK]

    @staticmethod
    def _normalize_string_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _normalize_assessment(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {"is_reasonable": None, "problem": str(value)}
        return {
            "is_reasonable": value.get("is_reasonable"),
            "problem": str(value.get("problem", "")),
        }

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _fallback_result(self, reason: str) -> Dict[str, Any]:
        return {
            "final_risk_score": None,
            "final_risk_types": [],
            "final_primary_risk_type": "",
            "decision": "",
            "confidence": "low",
            "evidence": [],
            "reasoning": "",
            "llm_a_assessment": {"is_reasonable": None, "problem": ""},
            "llm_b_assessment": {"is_reasonable": None, "problem": ""},
            "adjudicator_raw": "",
            "adjudicator_error": reason,
        }

    async def aadjudicate(
        self,
        news_text: str,
        llm_a_result: str,
        llm_b_result: str,
    ) -> Dict[str, Any]:
        last_err: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                parsed = await self.chain.ainvoke(
                    {
                        "system_prompt": ADJUDICATOR_SYSTEM_PROMPT,
                        "news_text": news_text,
                        "llm_a_result": llm_a_result,
                        "llm_b_result": llm_b_result,
                    }
                )
                return self._normalize_result(parsed)
            except Exception as exc:
                last_err = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self.sleep_between_retries)

        return self._fallback_result(f"模型调用失败：{last_err}")

    def adjudicate(self, news_text: str, llm_a_result: str, llm_b_result: str) -> Dict[str, Any]:
        return asyncio.run(self.aadjudicate(news_text, llm_a_result, llm_b_result))


async def _adjudicate_row_async(
    position: int,
    row: Dict[str, Any],
    agent: AdjudicatorAgent,
    config: RunConfig,
) -> Tuple[int, Dict[str, Any]]:
    news_text = str(row.get(config.content_col, "") or "")
    llm_a_result = _build_llm_result(
        row,
        config.a_score_col,
        config.a_types_col,
        config.a_primary_col,
        config.a_reason_col,
    )
    llm_b_result = _build_llm_result(
        row,
        config.b_score_col,
        config.b_types_col,
        config.b_primary_col,
        config.b_reason_col,
    )

    result = await agent.aadjudicate(news_text, llm_a_result, llm_b_result)
    llm_a_assessment = result.get("llm_a_assessment", {})
    llm_b_assessment = result.get("llm_b_assessment", {})

    return position, {
        **row,
        "final_risk_score": result.get("final_risk_score"),
        "final_risk_types": json.dumps(result.get("final_risk_types", []), ensure_ascii=False),
        "final_primary_risk_type": result.get("final_primary_risk_type"),
        "adjudicator_decision": result.get("decision"),
        "adjudicator_confidence": result.get("confidence"),
        "adjudicator_evidence": json.dumps(result.get("evidence", []), ensure_ascii=False),
        "adjudicator_reasoning": result.get("reasoning"),
        "llm_a_is_reasonable": llm_a_assessment.get("is_reasonable"),
        "llm_a_problem": llm_a_assessment.get("problem"),
        "llm_b_is_reasonable": llm_b_assessment.get("is_reasonable"),
        "llm_b_problem": llm_b_assessment.get("problem"),
        "adjudicator_raw": result.get("adjudicator_raw"),
        "adjudicator_error": result.get("adjudicator_error"),
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


async def process_csv_async(config: RunConfig = CONFIG) -> None:
    df = read_csv_auto(config.input_csv)
    if config.max_rows is not None:
        df = df.head(config.max_rows)

    required_cols = [
        config.content_col,
        config.a_score_col,
        config.a_types_col,
        config.a_primary_col,
        config.a_reason_col,
        config.b_score_col,
        config.b_types_col,
        config.b_primary_col,
        config.b_reason_col,
    ]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"输入文件中缺少必要列：{', '.join(missing_cols)}")

    records = df.to_dict("records")
    concurrency = max(1, min(config.max_workers, len(records) or 1))
    agent = AdjudicatorAgent(config)

    output_path = Path(config.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

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
            _adjudicate_row_async(
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

        with tqdm(total=len(records), desc=f"异步裁决新闻中({concurrency} concurrent)") as bar:
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

    print(f"裁决完成，结果已保存到：{config.output_csv}")


def process_csv(config: RunConfig = CONFIG) -> None:
    asyncio.run(process_csv_async(config))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="裁决两路加密货币新闻风险标注")
    parser.add_argument("--input-csv", default=CONFIG.input_csv)
    parser.add_argument("--output-csv", default=CONFIG.output_csv)
    parser.add_argument("--model", default=CONFIG.model)
    parser.add_argument("--max-rows", type=int, default=CONFIG.max_rows)
    parser.add_argument("--max-workers", type=int, default=CONFIG.max_workers)
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> RunConfig:
    return replace(
        CONFIG,
        input_csv=args.input_csv,
        output_csv=args.output_csv,
        model=args.model,
        max_rows=args.max_rows,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    process_csv(config_from_args(parse_args()))
