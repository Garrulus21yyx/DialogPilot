"""Owner-defined state algebra and native interaction recovery, without an LLM judge."""
import asyncio
from dataclasses import replace
from itertools import permutations, product

import pytest
from langchain_core.messages import AIMessage

from application.action_compatibility import (
    ActionCompatibilityError, ActionStateTransition, validate_action_compatibility,
)
from evaluation.retail_action_contracts import retail_action_transitions
from tests.test_operation_plan import step, plan, proposal
from tests.test_approval_conversation import domain


RULES = {"prepare_" + name: rule for name, rule in retail_action_transitions().items()}


def candidate(name, target="O1", later=()):
    value = {"tool": "prepare_" + name, "arguments": {"order_id": target}}
    if later:
        value["arguments"]["operation_plan"] = plan(step("a"), *later)
    return value


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("same_target", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_return_exchange_only_conflicts_on_same_order(reverse, same_target, batch):
    names = ["return_delivered_order_items", "exchange_delivered_order_items"]
    if reverse:
        names.reverse()
    target = "O1" if same_target else "O2"
    value = ({"actions": [candidate(names[0]), candidate(names[1], target)]} if batch else
             candidate(names[0], later=(step("b", ("a",), "prepare_" + names[1], target),)))
    if same_target:
        with pytest.raises(ActionCompatibilityError):
            validate_action_compatibility(value, RULES)
    else:
        validate_action_compatibility(value, RULES)


def test_feasible_sequence_not_misreported_as_mutual_exclusion():
    names = ["modify_pending_order_address", "cancel_pending_order"]
    with pytest.raises(ActionCompatibilityError) as caught:
        validate_action_compatibility({"actions": [candidate(name) for name in names]}, RULES)
    assert caught.value.code == "ACTION_SEQUENCE_REQUIRED"
    validate_action_compatibility(candidate(names[0], later=(
        step("b", ("a",), "prepare_" + names[1], "O1"),)), RULES)
    with pytest.raises(ActionCompatibilityError):
        validate_action_compatibility(candidate(names[1], later=(
            step("b", ("a",), "prepare_" + names[0], "O1"),)), RULES)


def test_generated_sequences_match_independent_state_simulation():
    definitions = [ActionStateTransition("order", "order_id", states, result)
                   for states in (("A",), ("B",), ("A", "B"))
                   for result in (None, "A", "B")]
    for transitions in product(definitions, repeat=3):
        rules = {f"prepare_{i}": transition for i, transition in enumerate(transitions)}
        value = candidate("0", later=(step("b", (), "prepare_1", "O1"),
                                      step("c", (), "prepare_2", "O1")))
        possible = False
        for tail in permutations((1, 2)):
            for initial in ("A", "B"):
                state, valid = initial, True
                for i in (0, *tail):
                    rule = transitions[i]
                    if state not in rule.states:
                        valid = False
                        break
                    state = rule.resulting_state or state
                possible |= valid
        try:
            validate_action_compatibility(value, rules)
        except ActionCompatibilityError:
            assert not possible
        else:
            assert possible


def test_generated_unordered_batches_preserve_future_for_every_execution_order():
    definitions = [ActionStateTransition("order", "order_id", states, result)
                   for states in (("A",), ("B",), ("A", "B"))
                   for result in (None, "A", "B")]
    for transitions in product(definitions, repeat=3):
        rules = {f"prepare_{i}": transition for i, transition in enumerate(transitions)}
        value = {"actions": [candidate("0", later=(step("c", (), "prepare_2", "O1"),)), candidate("1")]}
        possible = False
        for initial in ("A", "B"):
            orders_valid = []
            for order in ((0, 1, 2), (1, 0, 2)):
                state, valid = initial, True
                for i in order:
                    rule = transitions[i]
                    if state not in rule.states:
                        valid = False
                        break
                    state = rule.resulting_state or state
                orders_valid.append(valid)
            possible |= all(orders_valid)
        try:
            validate_action_compatibility(value, rules)
        except ActionCompatibilityError:
            assert not possible
        else:
            assert possible


def test_native_conflict_goes_to_choice_before_any_preparation():
    value = plan(step("a"), step("b", ("a",)))
    question = "Only one cancellation can be submitted for this order. Which change do you want?"
    agent, context, model, calls = domain([proposal(value), AIMessage(content="", tool_calls=[
        {"name": "request_user_input", "id": "choice", "args": {"question": question}}])])
    action = replace(agent._registry.actions[0], state_transition=ActionStateTransition(
        "order.status", "order_id", ("paid",), "cancelled"))
    agent._registry = replace(agent._registry, actions=(action,))
    context = replace(context, work_item=replace(context.work_item,
                                               registry_fingerprint=agent._registry.fingerprint))
    result = asyncio.run(agent(context))
    assert result.status.value == "NEEDS_USER_INPUT"
    assert not result.prepared_actions and not result.action_receipts and not calls
    assert model.calls == 2 and model.review_calls == 0
    assert any(entry.get("reason_code") == "ACTION_PLAN_INFEASIBLE"
               for entry in result.execution_feedback)
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    serde = target_checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(result)) == result
    # A subsequent user choice prepares the selected operation normally; the
    # conflict is not a sticky denial or an approval that must be asked twice.
    from tests.test_approval_conversation import call
    followup, _, followup_model, followup_reads = domain([call("prepare_order_cancel", "selected")])
    followup._registry = agent._registry
    next_context = replace(context, current_message="Cancel this order only.",
                           working_messages=result.working_messages)
    resumed = asyncio.run(followup(next_context))
    assert resumed.status.value == "WAITING_APPROVAL"
    assert followup_model.calls == 1 and len(followup_reads) == 1
    assert resumed.pending_action is not None and not resumed.missing_inputs


def test_default_cross_domain_rules_share_business_resource_not_agent_name():
    from application.default_capability_registry import build_default_capability_registry
    registry = build_default_capability_registry("tenant")
    refund, shipping = registry.action("refund.request.create:v1"), registry.action("order.shipping_address.change:v1")
    assert refund.owner_agent != shipping.owner_agent
    rules = {"prepare_" + tool: action.state_transition for action in registry.actions for tool in action.allowed_tool_ids}
    with pytest.raises(ActionCompatibilityError):
        validate_action_compatibility(candidate("refund_request_create", later=(
            step("b", (), "prepare_shipping_address_change", "O1"),)), rules)
    validate_action_compatibility(candidate("refund_request_create", later=(
        step("b", (), "prepare_shipping_address_change", "O2"),)), rules)


def test_unknown_effect_is_not_assumed_to_preserve_state():
    rules = {"prepare_cancel": ActionStateTransition("order", "order_id", ("paid",), "cancelled"),
             "prepare_reopen": None,
             "prepare_change": ActionStateTransition("order", "order_id", ("paid",))}
    value = candidate("cancel", later=(step("b", ("a",), "prepare_reopen", "O1"),
                                       step("c", ("b",), "prepare_change", "O1")))
    assert validate_action_compatibility(value, rules) == "UNMODELLED_EFFECTS"


def test_preparation_owner_rejects_plan_even_without_model_middleware():
    from infrastructure.target_action_preparation import TargetActionPreparation
    agent, context, _, calls = domain([])
    preparation = TargetActionPreparation(agent._registry, agent._tool_manager)
    with pytest.raises(ActionCompatibilityError):
        asyncio.run(preparation.prepare(context, context.work_item.allowed_actions[0],
            {"order_id": "DP1234"}, "test", operation_plan=plan(step("a"), step("b", ("a",)))))
    assert calls == []


def test_native_cross_owner_reference_is_checked_without_granting_that_action():
    from application.default_capability_registry import build_default_capability_registry
    base = build_default_capability_registry("tenant-a")
    agent, context, model, calls = domain([
        proposal(plan(step("a"), step("b", ("a",), "prepare_refund_request_create"))),
        AIMessage(content="", tool_calls=[{"name": "request_user_input", "id": "choice", "args": {
            "question": "Cancellation and this refund request require different order states. Which objective should we investigate?"}}])])
    refund = replace(base.action("refund.request.create:v1"), flow_ref=None)
    registry = replace(agent._registry,
        agents=(*agent._registry.agents, replace(base.agent(refund.owner_agent), allowed_skill_ids=())),
        actions=(*agent._registry.actions, refund))
    agent._registry = registry
    context = replace(context, work_item=replace(context.work_item, registry_fingerprint=registry.fingerprint))
    assert "prepare_refund_request_create" not in {tool.name for tool in agent._tools(context)}
    result = asyncio.run(agent(context))
    assert result.status.value == "NEEDS_USER_INPUT"
    assert model.review_calls == 0 and calls == [] and result.pending_action is None
    assert context.work_item.allowed_actions == ("order.cancel:v1",)


def test_joint_dependencies_cannot_use_different_incompatible_resource_orders():
    rules = {f"prepare_{name}": ActionStateTransition(resource, "order_id", (start,), end)
             for name, resource, start, end in (("root", "root", "S", "S"),
                 ("a1", "A", "0", "1"), ("a2", "A", "1", "2"),
                 ("b1", "B", "0", "1"), ("b2", "B", "1", "2"))}
    value = candidate("root", later=(
        step("a1", ("b2",), "prepare_a1", "O1"),
        step("a2", (), "prepare_a2", "O1"),
        step("b1", ("a2",), "prepare_b1", "O1"),
        step("b2", (), "prepare_b2", "O1")))
    with pytest.raises(ActionCompatibilityError):
        validate_action_compatibility(value, rules)


def test_batch_conflict_with_restore_operation_does_not_force_goal_abandonment():
    rules = {"prepare_cancel": ActionStateTransition("order", "order_id", ("paid",), "cancelled"),
             "prepare_reopen": ActionStateTransition("order", "order_id", ("cancelled",), "paid"),
             "prepare_change": ActionStateTransition("order", "order_id", ("paid",), "modified")}
    with pytest.raises(ActionCompatibilityError) as caught:
        validate_action_compatibility({"actions": [candidate("cancel"), candidate("change"), candidate("reopen")]}, rules)
    assert caught.value.code == "ACTION_SEQUENCE_REQUIRED"
    validate_action_compatibility(candidate("cancel", later=(
        step("b", ("a",), "prepare_reopen", "O1"), step("c", ("b",), "prepare_change", "O1"))), rules)


def test_approval_owner_rechecks_custom_executor_prepared_set():
    from types import SimpleNamespace
    from application.action_approval import bind_action_approval
    from application.conversation_state import ConversationState
    from application.work_item import ArgumentValue
    from tests.test_approval_operation_set import prepared
    from tests.test_response_assembly import _board
    agent, context, result, _ = prepared(2)
    first, second = result.prepared_actions
    second = replace(second, arguments=tuple(
        ArgumentValue.create(argument.name, "DP1000") if argument.name == "order_id" else argument
        for argument in second.arguments))
    result = replace(result, additional_actions=(second,))
    state = ConversationState.empty(tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a")
    with pytest.raises(ActionCompatibilityError):
        bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=(context.work_item,))),
            _board(result), agent._registry, "checkpoint")
    assert state.pending_approval is None
