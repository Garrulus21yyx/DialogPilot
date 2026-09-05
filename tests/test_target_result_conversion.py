"""Execution mode does not change the authority of a governed tool result."""
import asyncio
import json
from dataclasses import replace

import pytest

from application.agent_result import FactSourceKind
from application.work_item import ControlMode
from infrastructure.target_agent_result_adapter import (
    framework_artifact, restore_framework_artifact,
)
from infrastructure.target_framework_agent import _adapt_framework_result
from infrastructure.target_tool_execution import TargetToolExecutor
from mcp.tool_manager import ToolResult
from tests.test_target_framework_agent import _context, _item


@pytest.mark.parametrize("authority,source_kind", [
    ("knowledge.active_source", FactSourceKind.KNOWLEDGE_ASSERTED),
    ("media.visible_text", FactSourceKind.MEDIA_OBSERVED),
    ("refund.current_state", FactSourceKind.VERIFIED_STATE),
])
@pytest.mark.parametrize("receipt_id", ["", "receipt:123"])
def test_direct_and_framework_preserve_identical_tool_provenance(
    authority, source_kind, receipt_id,
):
    result = ToolResult(
        True, {"state": "observed", "amount_minor": 123}, "catalog_search",
        call_id="call:123", receipt_id=receipt_id, authority=authority,
        output_schema_version="fixture-output-v2", status="success",
    )

    class Tools:
        async def execute_for_agent(self, *args, **kwargs):
            return result

    item = replace(
        _item(allowed_tools=("catalog_search",)),
        control_mode=ControlMode.DIRECT,
        requirement_ids=(authority,),
    )
    direct = asyncio.run(TargetToolExecutor(Tools())(_context(item)))
    delegated = _adapt_framework_result(
        _context(replace(item, control_mode=ControlMode.DELEGATED)),
        (restore_framework_artifact(framework_artifact(result)),), (), "fixture-v1",
    )
    direct_fact, = direct.facts
    delegated_fact, = delegated.facts
    assert direct_fact == replace(delegated_fact, observed_at=direct_fact.observed_at)
    assert direct_fact.source_kind is source_kind
    assert direct_fact.source_ref == (receipt_id or result.call_id)
    assert direct_fact.producer_id == result.tool_name
    assert direct_fact.producer_version == result.output_schema_version
    assert direct_fact.subject_ref == f"work-item:{item.work_item_id}"
    assert json.loads(direct_fact.value_json) == result.data
