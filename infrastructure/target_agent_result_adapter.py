"""Shared provenance conversion from governed runtime results to Target facts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pydantic import TypeAdapter

from application.agent_result import AgentResult, AgentResultStatus, FactRecord, FactSourceKind, merge_facts
from application.knowledge_tool_contract import tool_domain_outcome
from mcp.tool_manager import ToolResult


_ARTIFACT_SCHEMAS = {
    "tool-result-v1": TypeAdapter(ToolResult),
    "agent-result-v1": TypeAdapter(AgentResult),
}


def framework_artifact(result: ToolResult | AgentResult) -> dict:
    """Use a stable wire contract inside ToolMessage, which dumps nested dataclasses."""
    schema = "tool-result-v1" if isinstance(result, ToolResult) else "agent-result-v1"
    return {"schema": schema, "result": _ARTIFACT_SCHEMAS[schema].dump_python(result, mode="json")}


def restore_framework_artifact(artifact: dict) -> ToolResult | AgentResult:
    return _ARTIFACT_SCHEMAS[artifact["schema"]].validate_python(artifact["result"])


def fact_from_tool_result(item, result: ToolResult) -> FactRecord:
    outcome = tool_domain_outcome(result)
    if outcome is not None and outcome[0] is not AgentResultStatus.SUCCEEDED:
        raise ValueError("knowledge outcome does not provide evidence")
    authority = str(result.authority)
    source_kind = (
        FactSourceKind.KNOWLEDGE_ASSERTED
        if authority.startswith("knowledge.")
        else FactSourceKind.MEDIA_OBSERVED
        if authority.startswith("media.")
        else FactSourceKind.VERIFIED_STATE
    )
    return FactRecord(
        result.query_ref or item.aggregate_ref or f"tool-observation:{result.tool_name}:{result.call_id}",
        authority,
        json.dumps(
            result.data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        source_kind,
        str(result.receipt_id or result.call_id),
        result.tool_name,
        str(result.output_schema_version or "tool-output-v1"),
        result.observed_at or datetime.now(timezone.utc),
        observation_started_at=result.observation_started_at,
    )
