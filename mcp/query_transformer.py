"""Production query-transformation owner shared by retrieval and evaluation.

Generated text is only a retrieval hint.  The raw query is always retained and
HyDE text is explicitly marked as non-evidence so downstream generation cannot
silently treat a model-authored hypothetical passage as a source.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from core.llm_metrics import create_message
from core.llm_utils import extract_text_content
from core.model_policy import ModelProfile, ModelRole

logger = logging.getLogger(__name__)


QUERY_TRANSFORM_PROMPT_VERSION = "customer-support-query-v1"


@dataclass(frozen=True)
class GeneratedQuery:
    kind: str
    text: str
    usable_as_evidence: bool = False


@dataclass(frozen=True)
class QueryTransformation:
    raw_query: str
    standalone: str
    expansions: tuple[str, ...]
    hyde: str
    errors: tuple[str, ...] = ()
    prompt_version: str = QUERY_TRANSFORM_PROMPT_VERSION

    def multi_query(self) -> tuple[GeneratedQuery, ...]:
        """Return raw first; generated variants can retrieve but never prove claims."""
        values = [GeneratedQuery("raw", self.raw_query)]
        values.extend(GeneratedQuery("multi_query", item) for item in self.expansions)
        return tuple(values)


class QueryTransformer:
    """Own prompts, parsing, validation, and fail-safe fallbacks for query transforms."""

    def __init__(self, client: Any, model_profile: ModelProfile):
        self._client = client
        self._model_profile = model_profile

    async def standalone(self, query: str, history: Sequence[str] = ()) -> tuple[str, str | None]:
        query = _clean(query).strip()
        history = tuple(item for item in (_clean(value).strip() for value in history[-8:]) if item)
        if not history:
            return query, None
        prompt = f"""你是客服知识库检索查询改写器。根据对话历史，把当前问题改写成一条可独立理解的检索查询。
只补全历史中明确出现的指代；不得猜测订单号、金额、日期、产品、政策或用户意图；保留否定词和精确编号。
返回严格 JSON：{{"query":"..."}}。

对话历史：
{json.dumps(history, ensure_ascii=False)}
当前问题：{json.dumps(query, ensure_ascii=False)}"""
        try:
            payload = await self._json_call(prompt, max_tokens=256)
            candidate = _clean(payload.get("query")).strip()
            if not candidate:
                raise ValueError("empty standalone query")
            return candidate, None
        except Exception as exc:
            logger.warning("standalone query rewrite failed; retaining raw query: %s", exc)
            return query, type(exc).__name__

    async def expand(self, query: str, *, n: int = 2) -> tuple[tuple[str, ...], str | None]:
        query = _clean(query).strip()
        if n < 1 or n > 5:
            raise ValueError("query expansion count must be between 1 and 5")
        prompt = f"""你是客服知识库检索查询扩展器。为当前问题生成 {n} 条语义互补的搜索查询，以提高文档召回。
覆盖可能的政策名称、办理步骤、条件或异常处理，但不得虚构事实、订单号、金额、日期或产品；保留原问题中的否定词和精确编号。
不要回答问题。返回严格 JSON：{{"queries":["...", "..."]}}。

当前问题：{json.dumps(query, ensure_ascii=False)}"""
        try:
            payload = await self._json_call(prompt, max_tokens=384)
            values = payload.get("queries")
            if not isinstance(values, list):
                raise ValueError("queries must be an array")
            expansions = _dedupe(_clean(value).strip() for value in values if isinstance(value, str))
            expansions = tuple(value for value in expansions if value and value != query)[:n]
            if not expansions:
                raise ValueError("no usable expanded queries")
            return expansions, None
        except Exception as exc:
            logger.warning("query expansion failed; retaining raw query only: %s", exc)
            return (), type(exc).__name__

    async def hyde(self, query: str) -> tuple[str, str | None]:
        query = _clean(query).strip()
        prompt = f"""你是客服知识库检索辅助器。写一小段“可能出现在官方帮助中心的假想文档”，仅用于向量检索。
它不是事实证据：不得编造具体金额、期限、电话号码、网址、法规编号或保证性结论；不确定处使用一般性的政策术语。
不要声称已查询账户或已完成操作。返回严格 JSON：{{"passage":"..."}}。

用户问题：{json.dumps(query, ensure_ascii=False)}"""
        try:
            payload = await self._json_call(prompt, max_tokens=384)
            passage = _clean(payload.get("passage")).strip()
            if not passage:
                raise ValueError("empty HyDE passage")
            return passage, None
        except Exception as exc:
            logger.warning("HyDE generation failed; disabling HyDE for this query: %s", exc)
            return "", type(exc).__name__

    async def transform(
        self,
        query: str,
        *,
        history: Sequence[str] = (),
        expansion_count: int = 2,
        include_hyde: bool = True,
    ) -> QueryTransformation:
        raw = _clean(query).strip()
        if not raw:
            raise ValueError("query must not be empty")
        standalone, standalone_error = await self.standalone(raw, history)
        expansions, expansion_error = await self.expand(standalone, n=expansion_count)
        hyde, hyde_error = await self.hyde(standalone) if include_hyde else ("", None)
        errors = tuple(
            f"{stage}:{error}" for stage, error in (
                ("standalone", standalone_error),
                ("multi_query", expansion_error),
                ("hyde", hyde_error),
            ) if error
        )
        return QueryTransformation(raw, standalone, expansions, hyde, errors)

    async def _json_call(self, prompt: str, *, max_tokens: int) -> dict[str, Any]:
        response = await create_message(
            self._client,
            self._model_profile,
            ModelRole.REWRITE,
            max_tokens=max_tokens,
            temperature=0.1,
            messages=[{"role": "user", "content": _clean(prompt)}],
        )
        raw = extract_text_content(response.content)
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model response did not contain a JSON object")
        payload = json.loads(raw[start:end + 1])
        if not isinstance(payload, dict):
            raise ValueError("model response JSON must be an object")
        return payload


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).encode("utf-8", errors="ignore").decode("utf-8")


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
