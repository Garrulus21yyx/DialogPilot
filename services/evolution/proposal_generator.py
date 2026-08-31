"""GEPA-lite：用失败组反思生成受限 AgentBundle 候选。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from anthropic import AsyncAnthropic

from core.llm_metrics import create_message
from core.llm_utils import extract_text_content
from core.model_policy import ModelProfile, ModelRole
from services.badcase_registry import BadCaseStage
from .attribution import AttributionDecision
from .bundle import AgentBundle, BundleContractError
from .miner import BadCaseCluster


class ReflectionProvider(Protocol):
    async def propose(self, payload: Mapping[str, Any], candidate_count: int) -> Sequence[Mapping[str, Any]]: ...


class LLMReflectionProvider:
    """让 Judge 角色做自然语言反思；返回值仍由本地闭合合同校验。"""

    def __init__(
        self,
        client: AsyncAnthropic,
        model_profile: ModelProfile,
    ):
        self._client = client
        self._profile = model_profile

    async def propose(self, payload: Mapping[str, Any], candidate_count: int) -> Sequence[Mapping[str, Any]]:
        prompt = (
            "你是 Agent 策略优化器。根据脱敏失败摘要和责任归因，生成彼此不同的最小配置补丁。\n"
            "只能使用 allowed_surfaces 中的顶层字段；不得修改权限、审批、JWT、PII、Verifier、Gold 或生产代码。\n"
            "返回 JSON 数组，每项是一个 patch 对象，不要解释。\n"
            f"候选数: {candidate_count}\n"
            f"输入: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )
        response = await create_message(
            self._client,
            self._profile,
            ModelRole.JUDGE,
            max_tokens=2048,
            temperature=0.6,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = extract_text_content(response.content)
        start, end = raw.find("["), raw.rfind("]") + 1
        parsed = json.loads(raw[start:end])
        if not isinstance(parsed, list):
            raise BundleContractError("reflection provider must return a JSON array")
        return [item for item in parsed if isinstance(item, Mapping)]


class GEPALiteProposalGenerator:
    """反思只产补丁；本类负责范围校验、合并、去重和不可变版本化。"""

    _SURFACES = {
        "prompts", "few_shots", "routing_policy", "retrieval_policy",
        "tool_descriptions", "model_policy",
    }

    def __init__(self, provider: ReflectionProvider):
        self._provider = provider

    async def generate(
        self,
        *,
        base: AgentBundle,
        cluster: BadCaseCluster,
        attribution: AttributionDecision,
        candidate_count: int = 4,
    ) -> Tuple[AgentBundle, ...]:
        if not attribution.evolvable:
            raise BundleContractError(f"badcase group is not auto-evolvable: {attribution.reason}")
        intent_cases = [case for case in cluster.cases if case.stage is BadCaseStage.INTENT]
        if any(not case.approved_intent or not case.annotation_id for case in intent_cases):
            raise BundleContractError(
                "intent candidates require approved, attributable annotations"
            )
        if not 4 <= int(candidate_count) <= 8:
            raise BundleContractError("candidate_count must be between 4 and 8")
        allowed = {surface.value for surface in attribution.allowed_surfaces}
        payload = {
            "baseline": base.to_dict(),
            "failure": cluster.reflection_summary(),
            "attribution": attribution.to_dict(),
            "allowed_surfaces": sorted(allowed),
        }
        raw_patches = await self._provider.propose(payload, int(candidate_count))
        candidates: List[AgentBundle] = []
        seen = set()
        for raw in raw_patches:
            patch = dict(raw.get("patch") or raw)
            unknown = set(patch) - self._SURFACES
            forbidden = set(patch) - allowed
            if unknown or forbidden or not patch:
                continue
            base_payload = base.to_dict()
            merged = dict(base_payload)
            for surface, change in patch.items():
                if not isinstance(change, Mapping):
                    raise BundleContractError(f"candidate surface {surface} must be an object")
                merged[surface] = {**dict(merged.get(surface) or {}), **dict(change)}
            if not any(merged[surface] != base_payload[surface] for surface in patch):
                continue
            canonical_patch = json.dumps(patch, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            patch_hash = hashlib.sha256(canonical_patch.encode("utf-8")).hexdigest()
            if patch_hash in seen:
                continue
            seen.add(patch_hash)
            merged.update({
                "version": f"cand-{base.version[:48]}-{patch_hash[:12]}",
                "base_version": base.version,
                "source_badcase_groups": list(dict.fromkeys([
                    *base.source_badcase_groups, cluster.group_id,
                ])),
            })
            candidate = AgentBundle(**merged)
            if candidate.content_hash != base.content_hash:
                candidates.append(candidate)
            if len(candidates) >= int(candidate_count):
                break
        if len(candidates) != int(candidate_count):
            raise BundleContractError(
                f"provider produced {len(candidates)} valid unique candidates; expected {candidate_count}"
            )
        return tuple(candidates)


def build_llm_proposal_generator(
    *,
    api_key: str,
    base_url: Optional[str],
    model_profile: ModelProfile,
) -> GEPALiteProposalGenerator:
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return GEPALiteProposalGenerator(
        LLMReflectionProvider(AsyncAnthropic(**kwargs), model_profile),
    )
