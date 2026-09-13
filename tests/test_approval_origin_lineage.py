"""Approval member authority survives compilation, revisions and persistence."""
import asyncio
from dataclasses import replace

import pytest

from application.work_item import WorkControlBinding
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.target_understanding import StateBoundTargetUnderstanding
from application.turn_planning import RoutePolicy, TurnPlanCompiler
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from tests.test_approval_operation_set import prepared


def two_origins():
    agent, context, _, state = prepared(2)
    first = context.work_item
    second = replace(first, work_item_id="second-origin", control=WorkControlBinding("second", 1))
    state = state.accept_work_items((second,), invocation_key="second-preparation")
    pending = state.pending_approval
    pending = replace(pending, suspended_work_items=(first, second), additional_operations=(replace(
        pending.additional_operations[0], origin_work_item_id=second.work_item_id, control=second.control),))
    return agent._registry, replace(state, pending_approval=pending), (first, second)


def approve(registry, state):
    pending = state.pending_approval
    resolution = DeterministicResolver().resolve(TurnObservations(
        "", approval_decision=True, approval_id=pending.approval_id), state)
    state = state.consume_approval(approval_id=pending.approval_id, approval_version=1, approved=True)
    proposal = asyncio.run(StateBoundTargetUnderstanding()(TurnObservations("yes"), state, resolution, registry))
    return state, proposal


def compile_plan(registry, state, proposal):
    from core.identity import IdentityFactory
    identity = IdentityFactory(lambda: "lineage").create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a", request_id="approve")
    return TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry), state, registry, identity)


@pytest.mark.parametrize("selected", [0, 1])
@pytest.mark.parametrize("transition", ["cancel", "revise"])
def test_member_origin_controls_only_its_unsubmitted_write(selected, transition):
    registry, state, origins = two_origins()
    state, proposal = approve(registry, state)
    plan = compile_plan(registry, state, proposal)
    writes = plan.work.items[:2]
    continued = plan.work.items[2:]
    assert all(item.dependencies == (writes[index].work_item_id,) for index, item in enumerate(continued))
    state = state.accept_work_items(plan.work.items, invocation_key="approved-plan")
    state = conversation_state_from_payload(conversation_state_to_payload(state))
    assert all(state.accepts_work(item) for item in writes)
    assert all(item.authorization_controls == (continued[index].control,) for index, item in enumerate(writes))
    if transition == "cancel":
        state = state.accept_work_items((), invocation_key="changed", cancelled_controls=(continued[selected].control,))
    else:
        revised = replace(continued[selected], work_item_id="changed", dependencies=(),
                          control=WorkControlBinding(continued[selected].control.control_id, 3))
        state = state.accept_work_items((revised,), invocation_key="changed")
    assert not state.accepts_work(writes[selected])
    assert state.accepts_work(writes[1 - selected])
    assert len(state.accepted_approvals[0].operations) == 2  # authorization history remains intact


@pytest.mark.parametrize("selected", [0, 1])
def test_any_preparation_origin_retires_the_unapproved_set(selected):
    _, state, origins = two_origins()
    state = state.accept_work_items((), invocation_key="cancel", cancelled_controls=(origins[selected].control,))
    assert state.pending_approval is None


def test_order_is_compiled_from_accepted_scope_not_proposal():
    registry, state, _ = two_origins()
    pending = state.pending_approval
    state = replace(state, pending_approval=replace(pending, additional_operations=(replace(
        pending.additional_operations[0], depends_on=(pending.operation_key,)),)))
    state, proposal = approve(registry, state)
    # Even a command producer omitting the dependency cannot discard approved ordering.
    proposal = replace(proposal, commands=(proposal.commands[0], replace(proposal.commands[1], dependencies=()),
                                           *proposal.commands[2:]))
    plan = compile_plan(registry, state, proposal)
    assert plan.work.items[1].dependencies == (plan.work.items[0].work_item_id,)


def test_member_cannot_claim_another_origins_control():
    _, state, origins = two_origins()
    pending = state.pending_approval
    with pytest.raises(ValueError, match="originating objective"):
        replace(pending, additional_operations=(replace(pending.additional_operations[0], control=origins[0].control),))


def test_unbound_candidate_is_not_an_approval_presentation():
    from application.response_assembly import _response_context
    from tests.test_response_assembly import _board
    _, _, result, state = prepared(2)
    assert _response_context(_board(result))["pending_actions"] == []
    assert len(_response_context(_board(result), pending_approval=state.pending_approval)["pending_actions"]) == 2


def test_checkpoint_and_sql_cannot_restore_missing_authorization_as_empty():
    import ormsgpack
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, TargetCheckpointContractError
    from infrastructure.postgres_target_runtime import _work_item_to_payload, _work_item_from_payload
    registry, state, _ = two_origins()
    state, proposal = approve(registry, state)
    item = compile_plan(registry, state, proposal).work.items[0]
    serde = target_checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(item)) == item
    assert _work_item_from_payload(_work_item_to_payload(item)) == item
    kind, payload = serde.dumps_typed(item)
    def remove_lineage(code, raw):
        fields = ormsgpack.unpackb(raw, ext_hook=lambda c, b: ormsgpack.Ext(c, b))
        if isinstance(fields, (tuple, list)) and tuple(fields[:2]) == ("application.work_item", "WorkItem"):
            fields[2].pop("authorization_controls")
        return ormsgpack.Ext(code, ormsgpack.packb(fields))
    old = ormsgpack.packb(ormsgpack.unpackb(payload, ext_hook=remove_lineage))
    with pytest.raises(TargetCheckpointContractError, match="authorization-lineage"):
        serde.loads_typed((kind, old))
    raw = _work_item_to_payload(item)
    raw.pop("authorization_controls")
    with pytest.raises(ValueError, match="authorization-lineage"):
        _work_item_from_payload(raw)


@pytest.mark.parametrize("cancel_before_send", [True, False])
def test_postgres_send_boundary_and_unknown_effect_recovery(postgres_database_url, cancel_before_send):
    from uuid import uuid4
    from application.conversation_store import ConversationScope
    from application.work_control import WorkSuperseded
    from application.write_workflow import OperationStatus, WriteOutcomeStatus
    from core.identity import TenantId, UserId, ConversationId
    from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresMigrationRunner
    from infrastructure.postgres_target_runtime import PostgresConversationStateStore, PostgresOperationLedger
    from tests.test_work_control import _item as read_item
    from tests.test_write_workflow import _item as write_item, accept_work

    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=3))
    pool.open()
    scope = ConversationScope(TenantId("tenant"), UserId("user"), ConversationId(uuid4().hex))
    origin = read_item("origin", 1, work_item_id="prepared-origin")
    write = replace(write_item(), authorization_controls=(origin.control,))
    try:
        accept_work(pool, scope, origin)
        accept_work(pool, scope, write)
        store = PostgresConversationStateStore(pool)
        ledger = PostgresOperationLedger(pool, scope)
        planned = ledger.acquire(write)
        executing = replace(planned, status=OperationStatus.EXECUTING, version=planned.version + 1,
                            attempts=1, effect_status=WriteOutcomeStatus.OUTCOME_UNKNOWN)
        def cancel():
            before = store.load(scope.tenant_id, scope.user_id, scope.conversation_id)
            after = before.accept_work_items((), invocation_key="cancel", cancelled_controls=(origin.control,))
            assert store.compare_and_set(before, after)
        if cancel_before_send:
            cancel()
            with pytest.raises(WorkSuperseded):
                ledger.compare_and_set(planned, executing, submission=write)
            assert ledger.acquire(write) == planned
        else:
            assert ledger.compare_and_set(planned, executing, submission=write)
            cancel()
            unknown = replace(executing, status=OperationStatus.OUTCOME_UNKNOWN, version=executing.version + 1)
            assert ledger.compare_and_set(executing, unknown)
            reconciling = replace(unknown, status=OperationStatus.RECONCILING, version=unknown.version + 1)
            assert ledger.compare_and_set(unknown, reconciling)
            assert ledger.acquire(write).effect_status is WriteOutcomeStatus.OUTCOME_UNKNOWN
    finally:
        pool.close()
