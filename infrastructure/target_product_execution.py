"""Product-domain composite Skill runtime for Target Architecture v1."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.orchestration_runtime import AgentContextView
from application.work_item import ControlMode


class TargetProductExecutor:
    """Pass verified OCR output into catalog lookup; never infer a model in prose."""

    version = "target-product-executor-v1"

    def __init__(self, tool_manager) -> None:
        self._tools = tool_manager

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        if (
            item.owner_agent != "product_technical"
            or item.control_mode is not ControlMode.DELEGATED
            or item.skill_hint != "product_identification"
            or not {"media_read", "catalog_search"}.issubset(item.allowed_tools)
        ):
            return self._failure(item, "UNSUPPORTED_PRODUCT_WORK_CONTRACT")
        arguments = {argument.name: argument.value for argument in item.arguments}
        asset_id = str(arguments.get("asset_id") or "").strip()
        if not asset_id:
            return self._failure(item, "PRODUCT_ASSET_REQUIRED")

        media = await self._tools.execute_for_agent(
            "media_read", {"asset_id": asset_id}, agent_type="technical",
            context=dict(context.trusted_context),
            call_id=f"{item.work_item_id}:media_read",
        )
        if not media.success:
            return self._tool_failure(item, media.status, "MEDIA_READ")
        media_data = media.data if isinstance(media.data, dict) else {}
        evidence_ref = str(media_data.get("evidence_ref") or "").strip()
        text = str(media_data.get("text") or "").strip()
        if not evidence_ref or not text:
            return self._failure(item, "MEDIA_TEXT_NOT_FOUND")

        catalog = await self._tools.execute_for_agent(
            "catalog_search", {"query": text}, agent_type="technical",
            context=dict(context.trusted_context),
            call_id=f"{item.work_item_id}:catalog_search",
        )
        if not catalog.success:
            return self._tool_failure(item, catalog.status, "CATALOG_SEARCH")
        match = catalog.data if isinstance(catalog.data, dict) else {}
        if match.get("status") != "MATCHED" or not match.get("canonical_model"):
            return self._failure(
                item,
                "PRODUCT_MODEL_AMBIGUOUS"
                if match.get("status") == "AMBIGUOUS"
                else "PRODUCT_MODEL_NOT_FOUND",
                evidence_refs=(evidence_ref,),
            )
        value_json = json.dumps(
            match, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        source_ref = str(match.get("source_ref") or catalog.call_id)
        model = str(match["canonical_model"])
        name = str(match.get("product_name") or model)
        fact = FactRecord(
            f"product:{model}", "product.canonical_model", value_json,
            FactSourceKind.VERIFIED_STATE, source_ref, "catalog_search",
            str(catalog.output_schema_version or "product-catalog-match-v1"),
            datetime.now(timezone.utc),
        )
        return AgentResult(
            item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
            "PRODUCT_MODEL_VERIFIED", self.version, facts=(fact,),
            evidence_refs=(evidence_ref, source_ref),
            candidate_response=f"识别到商品型号 {model}（{name}）。",
        )

    def _tool_failure(self, item, status: str, prefix: str) -> AgentResult:
        retryable = status in {"error", "timeout"}
        return AgentResult(
            item.work_item_id, item.owner_agent,
            AgentResultStatus.RETRYABLE_FAILURE if retryable else AgentResultStatus.TERMINAL_FAILURE,
            f"{prefix}_{str(status or 'FAILED').upper()}", self.version,
            retryable=retryable,
        )

    def _failure(
        self, item, reason: str, *, evidence_refs: tuple[str, ...] = (),
    ) -> AgentResult:
        return AgentResult(
            item.work_item_id, item.owner_agent,
            AgentResultStatus.TERMINAL_FAILURE, reason, self.version,
            evidence_refs=evidence_refs,
        )
