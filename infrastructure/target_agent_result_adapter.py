"""Shared provenance conversion from governed runtime results to Target facts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pydantic import TypeAdapter
from langchain_core.messages import ToolMessage, messages_from_dict

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


async def resolved_working_messages(context, archive):
    """Project a consumed tool interaction into the SDK working conversation.

    The persisted original and its archive stay immutable. Matching uses the
    validated continuation and operation receipt, never words in a reply.
    """
    messages = messages_from_dict(list(context.working_messages))
    origin = context.work_item.continuation_of
    if origin is None:
        return messages
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage):
            continue
        artifact = message.artifact or {}
        envelope = artifact.get("result", {})
        if artifact.get("schema") != "agent-result-v1" or envelope.get("status") not in {
                "NEEDS_USER_INPUT", "WAITING_APPROVAL"}:
            continue
        original = ((await archive.load(context, artifact["reference"]))["artifact"]
                    if "reference" in artifact else artifact)
        pending = restore_framework_artifact(original)
        if pending.work_item_id != origin or pending.owner_agent != context.work_item.owner_agent:
            continue
        resolution = None
        if pending.pending_action is not None:
            action = pending.pending_action
            for result in context.dependency_results:
                receipts = tuple(receipt for receipt in result.action_receipts
                    if receipt.operation_key == action.operation_key and receipt.effect_status == "COMMITTED"
                    and receipt.requirement_id in action.requirement_ids)
                if result.owner_agent == action.owner_agent and result.status is AgentResultStatus.SUCCEEDED and receipts:
                    resolution = {"status": "COMMITTED", "operation_key": action.operation_key,
                        "receipts": [receipt.__dict__ for receipt in receipts],
                        "facts": [json.loads(fact.value_json) for fact in result.facts
                                  if fact.requirement_id in action.requirement_ids]}
                    break
        elif pending.status is AgentResultStatus.NEEDS_USER_INPUT:
            if context.trusted_context.get("resolved_input_signal"):
                resolution = {"status": "ANSWERED", "reply": context.current_message,
                              "source_kind": "USER_ASSERTED", "approval_granted": False,
                              "signal_id": context.trusted_context["resolved_input_signal"]}
        if resolution is not None:
            messages[index] = message.model_copy(update={
                "content": json.dumps(resolution, ensure_ascii=False),
                "artifact": {"schema": "resolved-interaction-v1", "resolution": resolution,
                             "original_reference": artifact.get("reference")},
                "status": "success",
            })
    return messages


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
