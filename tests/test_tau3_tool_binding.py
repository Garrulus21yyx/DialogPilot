"""Environment registration is metadata-driven, without benchmark task answers."""
import asyncio
from types import SimpleNamespace
import pytest
from application.capability_registry import CapabilityEffect
from evaluation.tau3_tool_binding import bind_environment
from mcp.tool_manager import MCPToolManager


@pytest.mark.parametrize("write", [False, True])
def test_rejected_request_keeps_endpoint_feedback_without_transport_retry_or_circuit_failure(write):
    from mcp.tool_manager import ToolCallStatus, ToolEffectStatus
    definition = SimpleNamespace(name="lookup", openai_schema={"function": {
        "name": "lookup", "description": "Read or update a record.",
        "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"]}}})
    environment = SimpleNamespace(get_tools=lambda: [definition], get_policy=lambda: "Policy",
        tools=SimpleNamespace(tool_type=lambda _: SimpleNamespace(value="write" if write else "read")))
    calls = []
    async def call(tool, arguments):
        calls.append(arguments)
        return SimpleNamespace(content='{"error":"record_id must include its prefix"}', error=True, id="rejected")
    manager = MCPToolManager("test-key", model="test-model")
    bind_environment(environment, manager, call)
    async def run():
        return await manager.execute_for_agent("lookup", {"record_id": "1"}, agent_type="retail",
            context={"business_operation_key": "op"}, approved=True, allowed_tool_ids=("lookup",))
    result = asyncio.run(run())
    assert not result.success and result.status == ToolCallStatus.REJECTED.value
    assert "record_id must include its prefix" in result.output_for_model
    assert result.effect_status == (ToolEffectStatus.NOT_COMMITTED.value if write else ToolEffectStatus.NONE.value)
    assert len(calls) == 1
    assert manager.get_stats()["lookup"]["consecutive_fails"] == 0


@pytest.mark.parametrize("name", ["exchange_record", "update_preferences", "request_service"])
def test_environment_write_registration_and_observed_receipt(name):
    def definition(tool_name):
        return SimpleNamespace(name=tool_name, openai_schema={"function": {
            "name": tool_name, "description": "An environment capability.",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}},
                           "required": ["record_id"]}}})
    environment = SimpleNamespace(
        get_tools=lambda: [definition("read_record"), definition(name)],
        get_policy=lambda: "Follow the environment policy.",
        tools=SimpleNamespace(tool_type=lambda tool: SimpleNamespace(value="read" if tool == "read_record" else "write")),
    )
    calls = []
    async def call(tool, arguments):
        calls.append((tool, arguments))
        return SimpleNamespace(content='{"updated": true}', error=False, id="call-1")
    manager = MCPToolManager("test-key", model="test-model")
    registry = bind_environment(environment, manager, call)
    action, = registry.actions
    assert action.flow_ref is None
    assert action.allowed_tool_ids == (name,)
    assert registry.tool(name).effect is CapabilityEffect.WRITE
    assert registry.planning_shortcuts == ()
    # Discovery permissions and action-runtime reconciliation have different
    # owners. The model must never invent an internal operation key.
    assert registry.agents[0].allowed_tool_ids == ("read_record", name)
    assert action.reconciliation.tool_id == "observed_operation_status"
    assert registry.tool(action.reconciliation.tool_id).effect is CapabilityEffect.READ
    from application.turn_planning import CommandKind, CommandProposal, RoutePolicy, TurnProposal, ProposalDisposition, TurnPlanningError
    from tests.test_entity_binding import _state
    from dataclasses import replace
    state = replace(_state(), tenant_id=registry.tenant_id)
    proposal = CommandProposal('goal', CommandKind.DELEGATE_TASK, 'retail', 'Investigate record')
    delegated = RoutePolicy().accept(TurnProposal(ProposalDisposition.RESOLVED, (proposal,), 'TEST'), state, registry)
    assert delegated.commands[0].allowed_tools == ('read_record',)
    forbidden = CommandProposal('probe', CommandKind.DIRECT_TOOL, 'retail', 'Probe invented operation',
        tool_id=action.reconciliation.tool_id, requirement_ids=(action.reconciliation.requirement_id,))
    with pytest.raises(TurnPlanningError, match='allowlist'):
        RoutePolicy().accept(TurnProposal(ProposalDisposition.RESOLVED, (forbidden,), 'TEST'), state, registry)
    execution = CommandProposal('execute', CommandKind.EXECUTE_ACTION, 'retail', 'Execute approved action',
        action_ref=action.ref, requirement_ids=action.requirement_ids, approval_binding='accepted-approval',
        operation_key='op-1', target_entity_ref='record:R1', target_entity_version='1')
    validated = RoutePolicy().accept(TurnProposal(ProposalDisposition.RESOLVED, (execution,), 'TEST'), state, registry)
    assert validated.commands[0].allowed_tools == (name, action.reconciliation.tool_id)
    tool = next(tool for tool in manager.registered_tools if tool.name == name)
    receipt = asyncio.run(tool.handler({"record_id": "R1"}, {"business_operation_key": "op-1"}))
    assert receipt.receipt_id == "official-call:call-1"
    assert calls == [(name, {"record_id": "R1"})]
    status = next(tool for tool in manager.registered_tools if tool.name == "observed_operation_status")
    assert asyncio.run(status.handler({"operation_key": "missing"}, {}))["status"] == "UNKNOWN"
    assert asyncio.run(status.handler({"operation_key": "op-1"}, {}))["status"] == "COMMITTED"
    from infrastructure.target_workflow_execution import _ToolReconciler
    from application.write_workflow import WriteOutcomeStatus
    runtime = _ToolReconciler(manager, SimpleNamespace(trusted_context=()), principal='retail')
    item = SimpleNamespace(reconciliation=action.reconciliation, arguments=(),
                           expected_output_schema=action.receipt_schema_version)
    observed = asyncio.run(runtime.reconcile(item, operation_key='op-1'))
    unknown = asyncio.run(runtime.reconcile(item, operation_key='invented-operation'))
    assert observed.status is WriteOutcomeStatus.COMMITTED
    assert observed.receipt_id == receipt.receipt_id
    assert unknown.status is WriteOutcomeStatus.OUTCOME_UNKNOWN
    assert len(calls) == 1  # reconciliation never repeats the business write
