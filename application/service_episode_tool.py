"""MCP tool manifest and authenticated adapter for ServiceEpisode search."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from application.service_episode_retriever import ServiceEpisodeRetrievalPurpose
from mcp.tool_manager import Tool


def build_service_episode_tool(search_provider: Callable[[], Any]) -> Tool:
    async def handler(params, context):
        trusted = context or {}
        tenant_id = str(trusted.get("tenant_id") or "").strip()
        user_id = str(trusted.get("user_id") or "").strip()
        if not tenant_id or not user_id:
            raise ValueError(
                "service_episode_search requires trusted tenant/user context"
            )
        search = search_provider()
        if search is None:
            return {
                "status": "UNAVAILABLE", "hits": [],
                "detail_code": "SERVICE_EPISODE_SEARCH_UNAVAILABLE",
            }
        result = await asyncio.to_thread(
            search.search,
            tenant_id=tenant_id,
            user_id=user_id,
            query=str(params.get("query") or ""),
            entity_ids=tuple(map(str, params.get("entity_ids") or ())),
            purpose=str(
                params.get("purpose")
                or ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE.value
            ),
            explicit_time_reference=bool(
                params.get("explicit_time_reference", False)
            ),
            top_k=min(max(int(params.get("top_k", 5)), 1), 10),
        )
        return result.to_dict()

    return Tool(
        name="service_episode_search",
        description="按需检索当前租户和用户已验证的历史服务经历",
        handler=handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "entity_ids": {"type": "array", "items": {"type": "string"}},
                "purpose": {
                    "type": "string",
                    "enum": [item.value for item in ServiceEpisodeRetrievalPurpose],
                },
                "explicit_time_reference": {"type": "boolean"},
            },
            "required": ["query"],
        },
        allowed_agents=("general", "technical", "billing", "account_security"),
        read_only=True,
        authority="memory.service_episode",
        manifest_version="tool-manifest-v1",
        output_schema_version="service-episode-search-result-v1",
        preconditions=("authenticated_tenant", "authenticated_user"),
        idempotency="read_only",
        retry_policy="safe_read_retry",
        typed_outcomes=(
            "OK", "NO_EVIDENCE", "AMBIGUOUS", "UNAVAILABLE",
            "INVALID_CONTRACT", "CONFLICT",
        ),
        output_fields=(
            "status", "hits", "detail_code", "purpose", "purpose_outcome",
            "policy_version",
            "embedding_profile_fingerprint", "tenant_id", "user_id",
            "backend_id", "generation_id", "episode_id", "episode_revision",
            "outcome_receipt_ref", "provenance_sha256", "verified_at",
            "user_evidence_refs", "assistant_evidence_refs",
        ),
    )
