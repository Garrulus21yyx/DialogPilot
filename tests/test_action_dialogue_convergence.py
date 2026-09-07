"""Scope and consumed-interaction invariants across prepared action dialogue."""
import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage, messages_to_dict
from langgraph.store.memory import InMemoryStore

from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec, ReceiptRef
from application.target_understanding import StateBoundTargetUnderstanding
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, RoutePolicy, TurnProposal
from infrastructure.target_agent_result_adapter import framework_artifact, resolved_working_messages
from infrastructure.target_result_archive import TargetResultArchive
from tests.test_approval_conversation import domain, call
from tests.test_target_turn_planning import _registry, _state


@pytest.mark.parametrize("scope", [False, True])
def test_model_plan_card_and_compiler_share_action_scope(scope):
    from application.conversation_agent import ConversationAgent
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from tests.test_conversation_agent import Provider
    registry = _registry()
    state = _state()
    value = {"status": "resolved", "goals": [{"kind": "delegate_task",
        "target_agent": "billing_refund", "objective": "Handle this objective",
        "allow_action_proposals": scope}]}
    provider = Provider(value)
    observation = TurnObservations("Handle this objective")
    proposal = asyncio.run(ConversationAgent(provider).plan(observation, state,
        DeterministicResolver().resolve(observation, state), registry))
    cards = provider.calls[0]["domain_capabilities"]
    for card in cards:
        assert {action["action_ref"] for action in card["action_proposals"]} == {
            action.ref for action in registry.actions if action.owner_agent == card["agent_id"]}
    accepted = RoutePolicy().accept(proposal, state, registry)
    assert bool(accepted.commands[0].allowed_actions) is scope
    del value["goals"][0]["allow_action_proposals"]
    invalid = asyncio.run(ConversationAgent(Provider(value)).plan(observation, state,
        DeterministicResolver().resolve(observation, state), registry))
    assert invalid.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


@pytest.mark.parametrize("scope", [False, True])
def test_delegated_proposal_scope_is_explicit_and_preserved(scope):
    registry = _registry()
    command = CommandProposal("goal", CommandKind.DELEGATE_TASK, "billing_refund",
        "Investigate the supplied objective", allow_action_proposals=scope)
    accepted = RoutePolicy().accept(TurnProposal(ProposalDisposition.RESOLVED, (command,), "test"),
                                    _state(), registry)
    assert bool(accepted.commands[0].allowed_actions) is scope
    from tests.test_target_framework_agent import _item
    item = replace(_item(), allowed_actions=accepted.commands[0].allowed_actions)
    resumed = StateBoundTargetUnderstanding._resume_command(1, item)
    assert resumed.allow_action_proposals is scope


@pytest.mark.parametrize("scope", [False, True])
def test_same_domain_does_not_grant_another_goals_action_tools(scope):
    agent, context, _, calls = domain([call("prepare_order_cancel"), AIMessage(content="Finished.")])
    context = replace(context, work_item=replace(context.work_item,
        allowed_actions=context.work_item.allowed_actions if scope else ()))
    names = {tool.name for tool in agent._tools(context)}
    assert ("prepare_order_cancel" in names) is scope
    result = asyncio.run(agent(context))
    assert bool(result.pending_action) is scope
    assert result.action_receipts == ()
    assert len(calls) == int(scope)


@pytest.mark.parametrize("variant", ["committed", "unknown", "failed", "other_operation", "other_owner", "other_origin", "other_requirement"])
@pytest.mark.parametrize("archived", [False, True])
def test_receipt_resolves_only_its_matching_prepared_tool(variant, archived):
    async def run():
        agent, context, _, _ = domain([call("prepare_order_cancel"), AIMessage(content="Prepared.")])
        pending = await agent(context)
        action = pending.pending_action
        previous_id = context.work_item.work_item_id
        receipt = ReceiptRef("receipt", "v1", action.operation_key,
            "OUTCOME_UNKNOWN" if variant == "unknown" else "COMMITTED", action.requirement_ids[0])
        if variant == "other_operation":
            receipt = replace(receipt, operation_key="different")
        if variant == "other_requirement":
            receipt = replace(receipt, requirement_id="different")
        done = AgentResult("write-result", "different" if variant == "other_owner" else action.owner_agent,
            AgentResultStatus.TERMINAL_FAILURE if variant == "failed" else AgentResultStatus.SUCCEEDED,
            "test", "v1", action_receipts=(receipt,))
        # Work control is assigned by the plan compiler in production.
        from application.work_item import WorkControlBinding
        item = replace(context.work_item, work_item_id="continued",
            control=context.work_item.control or WorkControlBinding("control", 2),
            continuation_of="different" if variant == "other_origin" else previous_id)
        archive = TargetResultArchive(InMemoryStore())
        artifact = framework_artifact(pending)
        reference = await archive.save(replace(context, work_item=item), {"artifact": artifact, "content": "NOT EXECUTED"})
        if archived:
            artifact = {"schema": "agent-result-v1", "reference": reference,
                        "result": {"status": "WAITING_APPROVAL"}}
        messages = [AIMessage(content="", tool_calls=[{"name": "prepare_order_cancel", "id": "call", "args": {"order_id": "A"}}]),
                    ToolMessage(content="NOT EXECUTED", tool_call_id="call", id="result-id", artifact=artifact)]
        original = messages_to_dict(messages)
        resumed = replace(context, work_item=item, working_messages=tuple(original), dependency_results=(done,))
        projected = await resolved_working_messages(resumed, archive)
        assert messages_to_dict(messages) == original
        assert projected[1].id == "result-id" and projected[1].tool_call_id == "call"
        assert projected[0] == messages[0]
        if variant == "committed":
            assert json.loads(projected[1].content)["status"] == "COMMITTED"
            assert json.loads(projected[1].content)["receipts"][0]["receipt_id"] == "receipt"
            again = await resolved_working_messages(replace(resumed, working_messages=tuple(messages_to_dict(projected))), archive)
            assert again == projected
        else:
            assert projected == messages
    asyncio.run(run())


@pytest.mark.parametrize("bound", [False, True])
def test_only_consumed_input_signal_completes_user_tool_without_approval(bound):
    async def run():
        from application.work_item import WorkControlBinding
        _, context, _, _ = domain([])
        origin = context.work_item.work_item_id
        pending = AgentResult(origin, context.work_item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
            "INPUT", "v1", missing_inputs=(MissingInputSpec("reply", origin, "INPUT", "string", "Which size?"),))
        message = ToolMessage(content="Which size?", tool_call_id="question", artifact=framework_artifact(pending))
        context = replace(context, current_message="Medium", work_item=replace(context.work_item,
            work_item_id="continued", control=WorkControlBinding("control", 2), continuation_of=origin),
            working_messages=tuple(messages_to_dict([message])),
            trusted_context={**context.trusted_context, "resolved_input_signal": "signal" if bound else ""})
        result = await resolved_working_messages(context, TargetResultArchive(InMemoryStore()))
        if bound:
            answer = json.loads(result[0].content)
            assert answer["reply"] == "Medium" and answer["approval_granted"] is False
        else:
            assert result == [message]
    asyncio.run(run())
