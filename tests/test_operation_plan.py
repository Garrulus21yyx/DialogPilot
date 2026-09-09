"""Graph/SDK contract proofs; scripted review is not semantic model evaluation."""
import asyncio
from itertools import combinations

import pytest
from langchain_core.messages import AIMessage

from application.operation_plan import OperationPlanError, operation_plan_schema, validate_operation_plan
from tests.test_approval_conversation import domain


def step(ident, dependencies=(), tool="prepare_order_cancel", target="DP1234"):
    return dict(id=ident, tool=tool, target=target, goal="Cancel " + target,
                preconditions="Order is paid", effects="Order becomes cancelled",
                depends_on=list(dependencies))


def plan(*steps, selected="a"):
    current = next((s for s in steps if s["id"] == selected), {})
    return {"current": {k: current[k] for k in ("goal", "preconditions", "effects") if k in current},
            "remaining_steps": [{**s, "depends_on": ["current" if d == selected else d for d in s["depends_on"]]}
                                for s in steps if s is not current]}


def proposal(value):
    return AIMessage(content="", tool_calls=[dict(name="prepare_order_cancel", id="p",
        args={"order_id": "DP1234", "operation_plan": value})])


def validate(value):
    validate_operation_plan(value, selected_tool="prepare_order_cancel",
                            allowed_tools={"prepare_order_cancel"})


def test_all_four_node_ordered_dags_keep_current_identity_implicit():
    edges = tuple(combinations("abcd", 2))
    for mask in range(1 << len(edges)):
        dependencies = {ident: [] for ident in "abcd"}
        for index, (before, after) in enumerate(edges):
            if mask & (1 << index):
                dependencies[after].append(before)
        value = plan(*(step(i, dependencies[i]) for i in "abcd"))
        validate(value)
        assert set(value["current"]) == {"goal", "preconditions", "effects"}
        assert "next_step" not in value


@pytest.mark.parametrize("names", [("prepare_a",), ("prepare_a", "prepare_b"),
                                  ("prepare_order_cancel", "prepare_change_42")])
def test_model_schema_and_runtime_accept_exact_same_callable_names(names):
    from jsonschema import Draft202012Validator
    schema = operation_plan_schema(names)
    validator = Draft202012Validator(schema)
    for name in (*names, "unknown", names[0].removeprefix("prepare_")):
        value = plan(step("a"), step("b", tool=name))
        assert validator.is_valid(value) is (name in names)
        if name in names:
            validate_operation_plan(value, selected_tool=names[0], allowed_tools=names)
        else:
            with pytest.raises(OperationPlanError, match="preserving all remaining assigned goals"):
                validate_operation_plan(value, selected_tool=names[0], allowed_tools=names)
    # Per-invocation schema generation never mutates another task's scope.
    assert operation_plan_schema(("prepare_unrelated",)) != schema
    assert operation_plan_schema(names) == schema


def test_wrong_business_name_can_be_repaired_without_losing_remaining_goal():
    bad = plan(step("a"), step("b", tool="order_cancel", target="DP5678"))
    fixed = plan(step("a"), step("b", tool="prepare_order_cancel", target="DP5678"))
    agent, context, model, calls = domain([proposal(bad), proposal(fixed)])
    tools = agent._tools(context)
    prepare = next(t for t in tools if t.name == "prepare_order_cancel")
    enum = prepare.args_schema["properties"]["operation_plan"]["properties"]["remaining_steps"]["items"]["properties"]["tool"]["enum"]
    assert enum == ["prepare_order_cancel"]
    result = asyncio.run(agent(context))
    assert result.pending_action and len(calls) == 1
    assert model.calls == 2 and model.review_calls == 1
    assert any("preserving all remaining assigned goals" in str(m) for m in result.working_messages)


@pytest.mark.parametrize("value", [
    None, {}, plan(), plan(step("a"), step("b"), step("b")),
    plan(step("a"), step("b", ("missing",))), plan(step("a"), step("b", ("c",)), step("c", ("b",))),
    plan(step("a"), step("b", tool="prepare_unassigned")), plan(step("a"), selected="missing"),
    plan({**step("a"), "effects": " "}),
])
def test_invalid_plan_never_reaches_preparation_or_semantic_review(value):
    with pytest.raises(OperationPlanError):
        validate(value)
    agent, context, model, calls = domain([proposal(value), proposal(value)])
    result = asyncio.run(agent(context))
    assert result.pending_action is None and not calls
    assert model.review_calls == 0
    assert result.status.value == "TERMINAL_FAILURE"


@pytest.mark.parametrize("dependent", [False, True])
def test_plan_survives_history_but_only_selected_arguments_enter_action(dependent):
    value = plan(step("a"), step("b", ("a",) if dependent else (), target="DP5678"))
    agent, context, model, calls = domain([proposal(value)])
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert model.calls == model.review_calls == 1
    assert len(calls) == 1  # selected preparation read, no future preparation/write
    args = {arg.name: arg.value for arg in result.pending_action.arguments}
    assert args == {"order_id": "DP1234", "expected_order_version": 4}
    assert result.action_receipts == ()
    proposed = next(message for message in result.working_messages
                    if message["type"] == "ai" and message["data"].get("tool_calls"))
    assert proposed["data"]["tool_calls"][0]["args"]["operation_plan"] == value
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    serde = target_checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(result)) == result


def test_semantic_conflict_asks_choice_without_approval_or_preparation():
    value = plan(step("a"), step("b", ("a",)))
    question = "These changes cannot both be made. Which outcome do you prefer?"
    agent, context, model, calls = domain([proposal(value), AIMessage(content="", tool_calls=[
        dict(name="request_user_input", id="choice", args={"question": question})])])
    model.outcome_reviews = [
        {"accepted": False, "feedback": "Both operations consume the same initial state; ask which goal to retain."},
        {"accepted": True, "feedback": ""}]
    result = asyncio.run(agent(context))
    assert result.status.value == "NEEDS_USER_INPUT"
    assert result.pending_action is None and not result.action_receipts and not calls
    assert result.missing_inputs and model.calls == 2 and model.review_calls == 1


def test_missing_multi_action_plan_can_be_corrected_in_existing_review():
    first = proposal(plan(step("a")))
    first.tool_calls[0]["args"].pop("operation_plan")
    agent, context, model, calls = domain([first, proposal(plan(step("a"), step("b", target="DP5678")))])
    model.outcome_reviews = [{"accepted": False, "feedback": "Include the remaining related changes in operation_plan."},
                             {"accepted": True, "feedback": ""}]
    result = asyncio.run(agent(context))
    assert result.pending_action and len(calls) == 1
    assert model.calls == model.review_calls == 2


def test_plan_metadata_does_not_change_operation_identity():
    async def run():
        from langgraph.prebuilt import ToolRuntime
        agent, context, _, calls = domain([])
        tool = agent._action_tool(context.work_item.allowed_actions[0], preparation_names=("prepare_order_cancel",))
        runtime = ToolRuntime(state={}, context=context, config={}, stream_writer=lambda _: None,
                              tool_call_id="same-call", store=None)
        _, plain = await tool.coroutine(runtime=runtime, order_id="DP1234")
        _, planned = await tool.coroutine(runtime=runtime, order_id="DP1234", operation_plan=plan(step("a")))
        from infrastructure.target_agent_result_adapter import restore_framework_artifact
        assert restore_framework_artifact(plain).pending_action == restore_framework_artifact(planned).pending_action
    asyncio.run(run())


def test_current_tool_cannot_be_redefined_in_metadata():
    value = plan(step("a"))
    value["current"]["tool"] = "prepare_other"
    with pytest.raises(OperationPlanError, match="schema"):
        validate(value)
    with pytest.raises(OperationPlanError, match="capabilities"):
        validate_operation_plan(plan(step("a")), selected_tool="prepare_other",
                                allowed_tools={"prepare_order_cancel"})


def test_invalid_plan_diagnostic_does_not_echo_business_values(caplog):
    value = plan({**step("a"), "effects": {"secret": "CANARY_PRIVATE_VALUE"}})
    with pytest.raises(OperationPlanError) as caught:
        validate(value)
    assert "CANARY_PRIVATE_VALUE" not in str(caught.value)
    agent, context, _, _ = domain([proposal(value), proposal(value)])
    result = asyncio.run(agent(context))
    diagnostics = [entry for entry in result.execution_feedback
                   if entry.get("stage") == "domain_outcome"]
    assert diagnostics and "CANARY_PRIVATE_VALUE" not in str(diagnostics)
    assert "CANARY_PRIVATE_VALUE" not in caplog.text
    # Original working-call context remains intact for scoped continuation;
    # it is not itself an exception diagnostic and must not be destructively redacted.


def test_cycle_diagnostic_does_not_export_model_supplied_node_ids():
    from core.tracing import exception_chain
    value = plan(step("a"), step("CANARY_PRIVATE_ID", ("CANARY_PRIVATE_ID",)))
    with pytest.raises(OperationPlanError) as caught:
        validate(value)
    assert "CANARY_PRIVATE_ID" not in str(exception_chain(caught.value))


def test_continuation_replans_remaining_work_without_replaying_prior_plan():
    from dataclasses import replace
    from application.agent_result import AgentResult, AgentResultStatus, ReceiptRef
    first_agent, context, _, _ = domain([proposal(plan(step("a"), step("b", target="DP5678")))])
    first = asyncio.run(first_agent(context))
    message = proposal(plan(step("b", target="DP5678"), selected="b"))
    message.tool_calls[0]["id"] = "remaining"
    message.tool_calls[0]["args"]["order_id"] = "DP5678"
    second_agent, _, model, calls = domain([message])
    committed = AgentResult("executed-a", context.work_item.owner_agent, AgentResultStatus.SUCCEEDED,
        "COMMITTED", "test", action_receipts=(ReceiptRef("receipt-a", "v1", "operation-a",
            "COMMITTED", "order.cancel_action"),))
    next_context = replace(context, working_messages=first.working_messages,
        dependency_results=(committed,))
    second = asyncio.run(second_agent(next_context))
    assert second.pending_action and model.calls == 1
    assert calls == [("read", {"order_id": "DP5678"})]
    assert {a.name: a.value for a in second.pending_action.arguments}["order_id"] == "DP5678"
