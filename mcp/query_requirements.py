"""Typed information-requirement planning for customer-support retrieval.

This component owns *what must be searched for*.  It does not retrieve evidence
and it never receives Gold annotations.  Raw/standalone query preservation and
the deterministic fallback keep model-authored decomposition a recall aid, not
an authoritative rewrite of user intent.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.usage import RunUsage

from core.llm_metrics import record_external_llm_run
from core.model_policy import ModelProfile, ModelRole, ReasoningEffort

logger = logging.getLogger(__name__)

REQUIREMENT_PROMPT_VERSION = "customer-support-requirements-v1"
QueryKind = Literal["simple", "parallel_composite", "dependent"]
_NEGATIONS = frozenset({
    "no", "not", "never", "without", "except", "unless", "cannot", "can't",
    "不", "没", "没有", "不能", "无需", "除外", "除非",
})


class _StructuredRequirementOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_kind: QueryKind
    requirements: list[str]


@dataclass(frozen=True)
class _RequirementDeps:
    identifiers: tuple[str, ...]
    negations: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalRequirement:
    requirement_id: str
    query: str


@dataclass(frozen=True)
class QueryRequirementPlan:
    raw_query: str
    standalone_query: str
    query_kind: QueryKind
    requirements: tuple[RetrievalRequirement, ...]
    error: str | None = None
    prompt_version: str = REQUIREMENT_PROMPT_VERSION

    @property
    def retrieval_queries(self) -> tuple[RetrievalRequirement, ...]:
        """Raw is an immutable safety branch; generated requirements follow it."""
        raw = RetrievalRequirement("R0", self.raw_query)
        generated = tuple(
            item for item in self.requirements if item.query != self.raw_query
        )
        return (raw, *generated)


class QueryRequirementPlanner:
    """Classify and decompose a support query into at most three search needs."""

    def __init__(
        self,
        client: Any,
        model_profile: ModelProfile,
        *,
        structured_agent: Any | None = None,
    ):
        self._model_profile = model_profile
        if structured_agent is None:
            provider = AnthropicProvider(anthropic_client=client)
            model = AnthropicModel(model_profile.model, provider=provider)
            structured_agent = Agent(
                model,
                deps_type=_RequirementDeps,
                output_type=ToolOutput(
                    _StructuredRequirementOutput,
                    name="submit_requirements",
                    description="Submit the query class and one to three search requirements.",
                    max_retries=1,
                ),
                retries={"output": 1},
            )
            structured_agent.output_validator(self._validate_output)
        self._structured_agent = structured_agent

    async def plan(self, raw_query: str, standalone_query: str = "") -> QueryRequirementPlan:
        raw = _clean(raw_query).strip()
        standalone = _clean(standalone_query).strip() or raw
        if not raw:
            raise ValueError("raw query must not be empty")
        deps = _RequirementDeps(
            identifiers=_identifiers(standalone),
            negations=_negations(standalone),
        )
        prompt = f"""你是客服知识库的信息需求规划器。判断问题属于：
- simple：一个可独立检索的信息需求；
- parallel_composite：两个或三个可以分别检索、最后合并回答的并列需求；
- dependent：后一个需求依赖前一个检索结果，不能安全地并行拆开。

只拆用户明确提出的条件，不扩写成常识性子问题，不回答问题，不虚构实体、日期、金额、编号或政策。
每个 requirement 必须是能独立搜索的完整短句，并保留原问题中的实体、否定、例外和精确编号。
simple 返回 1 条；parallel_composite 返回 2–3 条；dependent 返回 1 条保守的完整查询。
只通过 submit_requirements 工具提交。

原始问题：{json.dumps(raw, ensure_ascii=False)}
可独立理解的问题：{json.dumps(standalone, ensure_ascii=False)}"""
        usage = RunUsage()
        started = time.perf_counter()
        error: Exception | None = None
        try:
            result = await self._structured_agent.run(
                prompt,
                deps=deps,
                model_settings=self._model_settings(),
                usage=usage,
            )
            output = result.output
            self._require_valid(output, deps)
            requirements = tuple(
                RetrievalRequirement(f"R{index}", _clean(query).strip())
                for index, query in enumerate(output.requirements, 1)
            )
            return QueryRequirementPlan(
                raw, standalone, output.query_kind, requirements,
            )
        except Exception as exc:
            error = exc
            logger.warning("requirement planning failed; retaining raw query: %s", exc)
            return QueryRequirementPlan(
                raw,
                standalone,
                "simple",
                (RetrievalRequirement("R1", standalone),),
                error=type(exc).__name__,
            )
        finally:
            record_external_llm_run(
                self._model_profile,
                ModelRole.REWRITE,
                usage,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=type(error).__name__ if error is not None else None,
            )

    @staticmethod
    def _validate_output(
        ctx: RunContext[_RequirementDeps], output: _StructuredRequirementOutput,
    ) -> _StructuredRequirementOutput:
        try:
            QueryRequirementPlanner._require_valid(output, ctx.deps)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return output

    @staticmethod
    def _require_valid(
        output: _StructuredRequirementOutput, deps: _RequirementDeps,
    ) -> None:
        values = tuple(_clean(value).strip() for value in output.requirements)
        expected = 2 if output.query_kind == "parallel_composite" else 1
        if not expected <= len(values) <= (3 if output.query_kind == "parallel_composite" else 1):
            raise ValueError(f"{output.query_kind} returned an invalid requirement count")
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("requirements must be non-empty and unique")
        joined = " ".join(values).casefold()
        missing_ids = [value for value in deps.identifiers if value.casefold() not in joined]
        if missing_ids:
            raise ValueError(f"requirements dropped identifiers: {missing_ids}")
        missing_negations = [value for value in deps.negations if value.casefold() not in joined]
        if missing_negations:
            raise ValueError(f"requirements dropped negations: {missing_negations}")

    def _model_settings(self) -> dict[str, Any]:
        profile = self._model_profile
        settings: dict[str, Any] = {
            "max_tokens": max(768, profile.min_completion_tokens),
        }
        if profile.provider != "deepseek":
            settings["temperature"] = 0.0
        elif profile.reasoning is ReasoningEffort.NONE:
            settings.update({
                "temperature": 0.0,
                "extra_body": {"thinking": {"type": "disabled"}},
            })
        else:
            settings["extra_body"] = {
                "thinking": {"type": "enabled"},
                "output_config": {"effort": profile.reasoning.value},
            }
        return settings


def _identifiers(text: str) -> tuple[str, ...]:
    # Preserve numeric/date/order-like tokens and mixed alpha-numeric policy IDs.
    return tuple(dict.fromkeys(re.findall(
        r"(?<!\w)(?:[A-Za-z]+[-_]?)?\d[\w./:-]*(?!\w)", text,
    )))


def _negations(text: str) -> tuple[str, ...]:
    lowered = text.casefold()
    values = []
    for value in _NEGATIONS:
        if value.isascii():
            present = re.search(rf"(?<!\w){re.escape(value)}(?!\w)", lowered) is not None
        else:
            present = value in lowered
        if present:
            values.append(value)
    return tuple(sorted(values))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).encode("utf-8", errors="ignore").decode("utf-8")
