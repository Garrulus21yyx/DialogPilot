"""Execution mode does not change the authority of a governed tool result."""
import asyncio
import json
from dataclasses import replace

import pytest


@pytest.mark.parametrize("tool_name", ["lookup_record", "search_catalog", "read_case"])
def test_tool_queries_own_distinct_fact_subjects_not_the_work_item(tool_name):
    from mcp.tool_manager import MCPToolManager, Tool
    from infrastructure.target_agent_result_adapter import fact_from_tool_result
    from application.result_board import ResultBoard
    async def handler(params, context):
        return {"record": params["key"]}
    manager = MCPToolManager("test-key", model="test-model")
    manager.register(Tool(tool_name, "Read a record", handler,
        {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
        allowed_agents=("general",), authority="record.details"))
    async def run():
        return [await manager.execute_for_agent(tool_name, {"key": key}, agent_type="general",
            context={"tenant_id": tenant, "user_id": "user"}, call_id=f"call-{index}")
            for index, (tenant, key) in enumerate([("a", "one"), ("a", "two"), ("a", "one"), ("b", "one")])]
    results = asyncio.run(run())
    assert results[0].query_ref == results[2].query_ref
    assert len({result.query_ref for result in results}) == 3
    facts = tuple(fact_from_tool_result(_item(), result) for result in results[:3])
    assert ResultBoard._conflicts(facts) == ()
    changed = replace(facts[2], value_json='{"record":"changed"}')
    assert ResultBoard._conflicts((facts[0], changed))

from application.agent_result import FactSourceKind
from application.default_capability_registry import build_default_capability_registry
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
    from tests.test_knowledge_tool_contract import evidence_result
    data = evidence_result() if authority == "knowledge.active_source" else {"state": "observed", "amount_minor": 123}
    result = ToolResult(
        True, data, "catalog_search",
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
    direct = asyncio.run(TargetToolExecutor(Tools(), registry=build_default_capability_registry('tenant-a'))(_context(item)))
    delegated = _adapt_framework_result(
        _context(replace(item, control_mode=ControlMode.DELEGATED)),
        (restore_framework_artifact(framework_artifact(result)),), "fixture-v1",
        allowed_authorities={"catalog_search": authority},
    )
    assert direct.candidate_response is None
    direct_fact, = direct.facts
    delegated_fact, = delegated.facts
    assert direct_fact == replace(delegated_fact, observed_at=direct_fact.observed_at)
    assert direct_fact.source_kind is source_kind
    assert direct_fact.source_ref == (receipt_id or result.call_id)
    assert direct_fact.producer_id == result.tool_name
    assert direct_fact.producer_version == result.output_schema_version
    assert direct_fact.subject_ref == f"tool-observation:{result.tool_name}:{result.call_id}"
    assert json.loads(direct_fact.value_json) == result.data


@pytest.mark.parametrize('failure_status', ['error','timeout','rejected'])
def test_later_tool_failure_preserves_prior_authoritative_facts(failure_status):
    first=ToolResult(True,{'order_id':'DP9301','status':'shipped'},'order_lookup',
        authority='order.current_state',call_id='first',receipt_id='read-receipt')
    second=ToolResult(False,None,'other_lookup',status=failure_status,error='failed')
    class Tools:
        calls=0
        async def execute_for_agent(self,*args,**kwargs):
            self.calls+=1
            return first if self.calls==1 else second
    item=replace(_item(allowed_tools=('order_lookup','other_lookup')),
        control_mode=ControlMode.DELEGATED,skill_hint='lookup',allowed_skills=('lookup',),requirement_ids=('order.current_state',))
    result=asyncio.run(TargetToolExecutor(Tools(), registry=build_default_capability_registry('tenant-a'))(_context(item)))
    assert json.loads(result.facts[0].value_json)==first.data
    assert result.evidence_refs==('read-receipt',)
    assert result.candidate_response is None
