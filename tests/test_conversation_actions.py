"""Native model action availability and whole-turn conversion properties."""
import asyncio
import copy
import itertools

import pytest
from jsonschema import Draft202012Validator, ValidationError

from application.conversation_actions import planning_actions, action_proposal
from application.conversation_agent import ConversationAgent, ConversationProviderOutputError
from application.turn_planning import CommandKind, ProposalDisposition, RoutePolicy, TurnPlanCompiler
from core.identity import IdentityFactory
from core.model_policy import ModelProfile, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from tests.framework_structured_stub import action_models
from tests.test_conversation_agent import _invoke, Provider


def payload():
    return {"message": "yes", "supported_goals": ["general_qa", "order_status", "delegate_task",
            "continue_active_work", "cancel_active_work"], "domain_capabilities": [
        {"agent_id": "order_logistics", "description": "Orders"}]}


def calls(*items):
    return [{"name": name, "args": args, "id": f"call-{i}"} for i, (name, args) in enumerate(items)]


def provider(*items, text="", **kwargs):
    models = action_models(*items, text=text, **kwargs)
    profile = ModelProfile("test")
    return AnthropicConversationPlanningProvider(models, model_profile=profile, synthesis_profile=profile), models


@pytest.mark.parametrize("pending,retained", [(False, False), (True, False), (False, True)])
def test_each_author_receives_its_own_approval_responsibility(monkeypatch, pending, retained):
    from application.action_approval import ACTION_INTERACTION_CONTRACT, action_presentation_instruction
    from infrastructure.target_domain_outcome import SYSTEM
    from tests.test_target_framework_agent import ScriptedToolModel
    from tests.test_approval_conversation import domain

    prompts = []
    generate = ScriptedToolModel._generate

    def capture(self, messages, *args, **kwargs):
        prompts.append(messages[0].content)
        return generate(self, messages, *args, **kwargs)

    monkeypatch.setattr(ScriptedToolModel, "_generate", capture)
    p, models = provider(text="Which payment method would you like to use?")

    async def run():
        await p.plan(payload())
        assert models[ModelRole.SYNTHESIS].calls == 0
        await p.compose({"current_message": "Help with this change.",
                         "evidence": {"pending_actions": [{"operation_key": "prepared"}] if pending else [],
                                      "requested_inputs": [], "user_context": {
                                          "retained_approval": {"operation_key": "old"} if retained else None}}})

    asyncio.run(run())
    assert len(prompts) == 2
    assert all(ACTION_INTERACTION_CONTRACT not in prompt for prompt in prompts)
    assert prompts[0].startswith("You are DialogPilot, the customer's ecommerce service assistant.")
    assert "Approval presentation belongs to the" in prompts[0]
    assert action_presentation_instruction([{}] if pending else []) in prompts[1]
    assert ACTION_INTERACTION_CONTRACT in SYSTEM
    worker, context, _, _ = domain([])
    assert ACTION_INTERACTION_CONTRACT in worker._system(context)
    assert "delegate only open investigations" not in prompts[0]
    assert "The specialist" in prompts[0]
    assert models[ModelRole.INTENT].calls == models[ModelRole.SYNTHESIS].calls == 1


@pytest.mark.parametrize("approval,pending,active,resumable", itertools.product((False, True), repeat=4))
def test_state_product_exposes_only_available_actions(approval, pending, active, resumable):
    value = payload()
    if approval:
        value["pending_approval"] = {"approval_id": "secret-state-id"}
    if pending:
        value["pending_input"] = {"requested_fields": [{"target_work_item_id": "w", "field_name": "color", "value_schema": "string"}]}
    if active:
        value["active_work_controls"] = [{"control_id": "c", "objective": "old task"}]
    if resumable:
        value["resumable_work"] = [{"control_id": "c", "objective": "old task"}]
    before = copy.deepcopy(value)
    actions = {action.name: action for action in planning_actions(value)}
    assert ("review_action" in actions) == approval
    assert ("supply_input" in actions) == pending
    assert ("continue_active_work" in actions) == resumable
    assert ("cancel_active_work" in actions) == active
    assert {"knowledge_search", "delegate_task"} <= actions.keys()
    assert not {"respond", "unsupported_request"} & actions.keys()
    assert "order_status" not in actions  # Domain discovery remains available.
    assert "submit_turn_plan" not in actions
    for action in actions.values():
        Draft202012Validator.check_schema(action.schema)
        assert "approval_id" not in action.properties
    assert value == before


def test_entity_selection_injects_original_value_and_source_not_model_copies():
    p, models = provider(("order_status", {"entity": "DP1234"}))
    result, state, registry = _invoke(ConversationAgent(p), "查订单 DP1234")
    assert result.disposition is ProposalDisposition.RESOLVED
    command, = result.commands
    assert command.kind is CommandKind.DIRECT_TOOL
    assert command.tool_id == "order_lookup"
    assert command.argument_bindings[0].value == "DP1234"
    assert command.argument_bindings[0].source_ref == "turn-message:current:reference:1"
    RoutePolicy().accept(result, state, registry)
    assert models[ModelRole.INTENT].calls == 1
    assert models[ModelRole.SYNTHESIS].calls == 0


@pytest.mark.parametrize("field_name,action_name,argument", [
    ("order_id", "order_status", "entity"),
    ("order_id", "delegate_task", "order"),
    ("asset_id", "delegate_task", "media"),
])
def test_scoped_entity_values_preserve_all_sources_and_reject_unknowns(field_name, action_name, argument):
    rows = [{"value": "A", "source_ref": "first"},
            {"value": "A", "source_ref": "second"},
            {"value": "candidate_1", "source_ref": "third"},
            {"value": "B", "source_ref": "fourth"}]
    for order in itertools.permutations(rows):
        value = payload()
        value["entity_bindings"] = [{"field_name": field_name, "status": "AMBIGUOUS",
                                     "candidates": [*order, order[0]]}]
        original = copy.deepcopy(value)
        action = next(a for a in planning_actions(value) if a.name == action_name)
        base = ({"target_agent": "order_logistics", "objective": "Investigate", "allow_action_proposals": False}
                if action_name == "delegate_task" else {})
        choices = action.properties[argument]["enum"]
        assert len(choices) == 4  # Same value/source duplicates do not create a choice.
        assert "candidate_1" in choices and "B" in choices
        assert "A" not in choices  # Two sources cannot be silently collapsed.
        converted = [action.convert({**base, argument: key}) for key in choices]
        assert {(row[field_name], row[f"{field_name}_source_ref"]) for row in converted} == {
            (row["value"], row["source_ref"]) for row in rows}
        for invalid in ("unknown", "entity_1", "A"):
            with pytest.raises(ValidationError):
                action.convert({**base, argument: invalid})
        with pytest.raises(ValidationError):
            action.convert({**base, argument: "B", f"{field_name}_source_ref": "forged"})
        assert value == original


@pytest.mark.parametrize("status", ["STALE", "UNAUTHORIZED", "MISSING"])
def test_unusable_binding_is_not_exposed(status):
    value = payload()
    value["entity_bindings"] = [{"field_name": "order_id", "status": status,
                               "candidates": [{"value": "DP9999", "source_ref": "stale"}]}]
    assert "order_status" not in {a.name for a in planning_actions(value)}


def test_mixed_approval_partial_fields_and_independent_work_is_one_proposal():
    value = payload()
    value["pending_approval"] = {"approval_id": "approval-original"}
    value["pending_input"] = {"requested_fields": [
        {"target_work_item_id": "w1", "field_name": "order_id", "value_schema": "string"},
        {"target_work_item_id": "w2", "field_name": "order_id", "value_schema": "string"}]}
    batch = calls(("review_action", {"decision": "approve"}),
                  ("supply_input", {"values": {"order_id_2": "DP1234"}}),
                  ("knowledge_search", {"query": "运费政策"}))
    for ordering in itertools.permutations(batch):
        result = action_proposal(planning_actions(value), ordering, "我会处理")
        assert result["approval_decision"] == {"approval_id": "approval-original", "decision": "approve"}
        assert result["input_values"] == [{"target_work_item_id": "w2", "field_name": "order_id", "value": "DP1234"}]
        assert result["goals"] == [{"kind": "general_qa", "resolved_query": "运费政策"}]
        assert "response" not in result


@pytest.mark.parametrize("bad", [
    ("review_action", {"decision": "approve", "approval_id": "invented"}),
    ("unknown_tool", {}), ("supply_input", {"values": {"field_3": "x"}}),
    ("knowledge_search", {"query": ""}), ("unsupported_request", {}),
])
def test_late_invalid_call_rejects_entire_batch_without_any_execution(bad):
    value = payload()
    value["pending_approval"] = {"approval_id": "a"}
    before = copy.deepcopy(value)
    with pytest.raises((ValueError, ValidationError)):
        action_proposal(planning_actions(value), calls(("review_action", {"decision": "approve"}), bad), "")
    assert value == before


@pytest.mark.parametrize("targets", [1, 2, 5])
def test_resume_identity_injected_but_multiple_targets_require_selection(targets):
    value = payload()
    value["resumable_work"] = [{"control_id": f"control-{i}", "objective": f"task {i}"} for i in range(targets)]
    actions = planning_actions(value)
    args = {} if targets == 1 else {"target": f"task_{targets}"}
    result = action_proposal(actions, calls(("continue_active_work", args)), "")
    assert result["goals"][0]["revises_control_id"] == f"control-{targets - 1}"
    if targets > 1:
        with pytest.raises(ValidationError):
            action_proposal(actions, calls(("continue_active_work", {})), "")


def test_native_dependencies_reach_existing_task_graph_without_replanning():
    p, models = provider(("knowledge_search", {"goal_id": "policy", "query": "退货规则"}),
        ("delegate_task", {"target_agent": "billing_refund", "objective": "结合政策调查订单",
                           "allow_action_proposals": False, "goal_id": "investigate", "depends_on": ["policy"]}))
    proposal, state, registry = _invoke(ConversationAgent(p), "先查规则，再调查订单")
    identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="native")
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry), state, registry, identity)
    assert proposal.commands[1].dependencies == ("policy",)
    assert len(plan.work.items) == 2
    assert models[ModelRole.INTENT].calls == 1


def test_plain_reply_and_query_preamble_use_different_paths():
    p, _ = provider(text="不客气")
    assert asyncio.run(p.plan(payload())) == {"status": "respond", "response": "不客气"}
    p, _ = provider(("knowledge_search", {"query": "退货政策"}), text="我来查询")
    assert "response" not in asyncio.run(p.plan(payload()))


@pytest.mark.parametrize("kwargs", [{"text": ""}, {"text": "incomplete", "stop": "max_tokens"},
    {"invalid": [{"name": "knowledge_search", "args": "{bad", "id": "bad", "error": "parse"}]}])
def test_protocol_failure_is_not_outage_or_success(kwargs):
    p, _ = provider(**kwargs)
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(p.plan(payload()))


def test_retired_wire_protocol_is_not_a_fallback():
    p, _ = provider(("submit_turn_plan", {"result": {"status": "out_of_scope"}}))
    with pytest.raises(ConversationProviderOutputError, match="unavailable"):
        asyncio.run(p.plan(payload()))


def test_historical_options_preserved_not_silently_replaced_by_current_time():
    options = {"policy_date": "2023-06-01", "applicable_region": "CN"}
    p, _ = provider(("knowledge_search", {"query": "2023-06-01的退货政策", "knowledge_options": options}))
    proposal, _, _ = _invoke(ConversationAgent(p), "2023-06-01的退货政策")
    assert {arg.name: arg.value for arg in proposal.commands[0].arguments}.items() >= options.items()


def test_real_pending_input_conversion_reuses_task_not_new_goal():
    from tests.test_parameter_acceptance import accepted_work
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from application.target_conversation_manager import TargetTurnContext
    state, registry, item, _ = accepted_work()
    p, _ = provider(("supply_input", {"values": {"reply": "blue"}}))
    obs = TurnObservations("blue")
    result = asyncio.run(ConversationAgent(p).plan(obs, state, DeterministicResolver().resolve(obs, state), registry, TargetTurnContext()))
    assert result.disposition is ProposalDisposition.RESOLVED
    assert result.input_values == ((item.work_item_id, "reply", "blue"),)
    assert not result.commands


def test_delegation_preserves_entities_and_business_details_in_objective():
    objective = "调查订单和图片并改址到上海新路8号"
    p, _ = provider(("delegate_task", {"target_agent": "product_technical", "objective": objective,
        "allow_action_proposals": True, "order": "DP1234", "media": "IMG5678"}))
    proposal, state, registry = _invoke(ConversationAgent(p), "调查 DP1234 和 IMG5678，改到上海新路8号")
    command, = proposal.commands
    assert command.kind is CommandKind.DELEGATE_TASK
    assert {binding.field_name: binding.value for binding in command.argument_bindings} == {
        'order_id': 'DP1234', 'asset_id': 'IMG5678'}
    assert command.objective == objective
    RoutePolicy().accept(proposal, state, registry)


@pytest.mark.parametrize('answer', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_pending_values_fail_before_internal_plan(answer):
    value = payload()
    value['pending_input'] = {'requested_fields': [{'target_work_item_id': 'w', 'field_name': 'quantity', 'value_schema': 'number'}]}
    p, _ = provider(('supply_input', {'values': {'quantity': answer}}))
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(p.plan(value))


def test_separate_disjoint_input_calls_merge_but_duplicate_targets_do_not():
    value = payload()
    value['pending_input'] = {'requested_fields': [
        {'target_work_item_id': 'w', 'field_name': name, 'value_schema': 'string'} for name in ('color', 'size')]}
    batch = calls(('supply_input', {'values': {'color': 'blue'}}),
                  ('supply_input', {'values': {'size': 'L'}}))
    for order in itertools.permutations(batch):
        result = action_proposal(planning_actions(value), order, '')
        assert {v['field_name']: v['value'] for v in result['input_values']} == {'color': 'blue', 'size': 'L'}
    batch[1]['args'] = {'values': {'color': 'red'}}
    with pytest.raises(ValueError, match='duplicate_input'):
        action_proposal(planning_actions(value), batch, '')


@pytest.mark.parametrize('action,owner', [('knowledge_search', 'general'), ('refund_policy', 'billing_refund'),
                                       ('invoice_qa', 'billing_refund'), ('product_qa', 'product_technical')])
def test_knowledge_shortcuts_keep_existing_owner_and_authority(action, owner):
    p, _ = provider((action, {'query': '用户的政策问题'}))
    result, state, registry = _invoke(ConversationAgent(p), '用户的政策问题')
    command, = result.commands
    assert command.target_agent == owner
    assert command.tool_id == 'knowledge_search'
    assert command.requirement_ids == ('knowledge.active_source',)
    RoutePolicy().accept(result, state, registry)
