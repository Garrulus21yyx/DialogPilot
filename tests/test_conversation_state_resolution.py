from datetime import datetime, timezone

import pytest

from application.agent_result import RequestedField
from application.conversation_state import (
    ConversationState,
    ConversationStateConflict,
    InMemoryConversationStateStore,
    PendingApprovalState,
    PendingInteractionState,
    ResumeBinding,
    WorkstreamState,
    WorkstreamStatus,
)
from application.deterministic_resolution import (
    DeterministicResolutionError,
    DeterministicResolver,
    ExplicitControlSignal,
    ResolutionKind,
    TurnObservations,
)
from core.identity import ConversationId, TenantId, UserId


def _workstream(
    workstream_id: str = "refund-ws-1",
    *,
    status: WorkstreamStatus = WorkstreamStatus.ACTIVE,
) -> WorkstreamState:
    return WorkstreamState(
        workstream_id,
        "billing_refund",
        "execute_refund:v1",
        "WAIT_ORDER_ID",
        status,
        1,
        flow_ref="execute_refund:v1",
    )


def _state(*workstreams: WorkstreamState) -> ConversationState:
    state = ConversationState.empty(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conversation-a",
    )
    for workstream in workstreams:
        state = state.start_workstream(workstream)
    return state


@pytest.mark.parametrize("approval_first", [False, True])
def test_independent_waits_have_the_same_state_in_either_arrival_order(approval_first):
    from dataclasses import replace
    state = _state(_workstream(), _workstream("other"))
    approval = PendingApprovalState("approval", 1, "refund-ws-1", "write", "refund.request.create:v1",
        "operation", "order:A", "1", "2099-01-01T00:00:00+00:00")
    interaction = PendingInteractionState("input", 1,
        (RequestedField("reference", "other", "string"),), (), checkpoint_thread_id="input-thread")
    if approval_first:
        result = state.wait_for_approval(approval).wait_for_interaction(interaction)
    else:
        result = state.wait_for_interaction(interaction).wait_for_approval(approval)
    assert result.pending_approval == approval
    assert result.pending_interaction.interaction_id == "input"
    assert result._workstream("refund-ws-1").status is WorkstreamStatus.WAITING_APPROVAL
    assert result._workstream("other").status is WorkstreamStatus.WAITING_INPUT
    # Construction/restoration must enforce the same rule as public mutations.
    invalid = replace(result.pending_interaction, checkpoint_thread_id=None)
    with pytest.raises(ConversationStateConflict, match="independently"):
        replace(result, pending_interaction=invalid)


def test_single_pending_field_binds_typed_reply_without_semantic_router():
    state = _state(_workstream())
    state = state.wait_for_interaction(PendingInteractionState(
        "interaction-1",
        1,
        (RequestedField("order_id", "refund-ws-1", "string"),),
        (),
    ))

    resolution = DeterministicResolver().resolve(
        TurnObservations(
            "DP1234", interaction_id="interaction-1", interaction_version=1,
            interaction_values=(("refund-ws-1", "order_id", "DP1234"),),
        ),
        state,
    )

    assert resolution.kind is ResolutionKind.FILL_PENDING_INPUT
    assert resolution.fields[0].value == "DP1234"
    assert resolution.state_fingerprint == state.fingerprint

    next_state = state.consume_interaction(
        interaction_id=resolution.signal_id,
        interaction_version=resolution.signal_version,
        values=tuple(
            (item.workstream_id, item.field_name, item.value)
            for item in resolution.fields
        ),
    )
    assert next_state.pending_interaction is None
    assert next_state.active_workstreams[0].slots[0].value == "DP1234"


def test_multi_workstream_interaction_requires_all_bound_fields_once():
    state = _state(
        _workstream(),
        WorkstreamState(
            "product-question-ws-1",
            "product_technical",
            "product_qa:v1",
            "WAIT_PRODUCT_REFERENCE",
            WorkstreamStatus.ACTIVE,
            1,
        ),
    )
    state = state.wait_for_interaction(PendingInteractionState(
        "interaction-2",
        1,
        (
            RequestedField("order_id", "refund-ws-1", "string"),
            RequestedField("product_reference", "product-question-ws-1", "string"),
        ),
        (),
    ))
    resolver = DeterministicResolver()

    unresolved = resolver.resolve(
        TurnObservations("DP1234", structured_fields=(("order_id", "DP1234"),)),
        state,
    )
    assert unresolved.kind is ResolutionKind.UNRESOLVED

    resolved = resolver.resolve(TurnObservations(
        "DP1234, SKU-9",
        structured_fields=(("order_id", "DP1234"), ("product_reference", "SKU-9")),
        interaction_id="interaction-2",
        interaction_version=1,
    ), state)
    assert resolved.kind is ResolutionKind.FILL_PENDING_INPUT
    assert {item.workstream_id for item in resolved.fields} == {
        "refund-ws-1", "product-question-ws-1",
    }


def test_consumed_interaction_cannot_be_replayed():
    state = _state(_workstream())
    state = state.wait_for_interaction(PendingInteractionState(
        "interaction-1",
        1,
        (RequestedField("order_id", "refund-ws-1", "string"),),
        (),
    ))
    state = state.consume_interaction(
        interaction_id="interaction-1",
        interaction_version=1,
        values=(("refund-ws-1", "order_id", "DP1234"),),
    )

    with pytest.raises(ConversationStateConflict, match="already consumed"):
        state.consume_interaction(
            interaction_id="interaction-1",
            interaction_version=1,
            values=(("refund-ws-1", "order_id", "DP1234"),),
        )


def test_approval_is_bound_to_identity_and_transitions_only_its_workstream():
    state = _state(_workstream())
    state = state.wait_for_approval(PendingApprovalState(
        "approval-1",
        1,
        "refund-ws-1",
        "refund-write-1",
        "refund.request.create:v1",
        "operation-refund-1",
        "order:DP1234",
        "3",
        "2099-01-01T00:00:00+00:00",
    ))
    resolver = DeterministicResolver()

    with pytest.raises(DeterministicResolutionError, match="another interaction"):
        resolver.resolve(TurnObservations(
            "approve",
            approval_decision=True,
            approval_id="approval-other",
        ), state)

    resolution = resolver.resolve(TurnObservations(
        "",
        approval_decision=True,
        approval_id="approval-1",
    ), state)
    assert resolution.kind is ResolutionKind.APPROVAL_DECISION

    approved = state.consume_approval(
        approval_id="approval-1",
        approval_version=1,
        approved=True,
    )
    assert approved.pending_approval is None
    assert approved.active_workstreams[0].status is WorkstreamStatus.ACTIVE


def test_pending_approval_requires_explicit_id_and_rejects_stale_signal():
    state = _state(_workstream()).wait_for_approval(PendingApprovalState(
        "approval-1", 1, "refund-ws-1", "refund-write-1",
        "refund.request.create:v1", "operation-refund-1",
        "order:DP1234", "3", "2099-01-01T00:00:00+00:00",
    ))
    resolver = DeterministicResolver()

    with pytest.raises(DeterministicResolutionError, match="requires approval identity"):
        TurnObservations("确认", approval_decision=True)

    bound = resolver.resolve(TurnObservations(
        "", approval_decision=True, approval_id="approval-1",
    ), state)
    assert bound.signal_id == "approval-1"
    assert bound.operation_key == "operation-refund-1"

    consumed = state.consume_approval(
        approval_id="approval-1", approval_version=1, approved=True,
    )
    reconciliation = resolver.resolve(TurnObservations(
        "查询结果", approval_decision=True, approval_id="approval-1",
    ), consumed)
    assert reconciliation.kind is ResolutionKind.RECONCILE_WORKFLOW

    completed = consumed.complete_workstream(
        "refund-ws-1",
        expected_version=consumed.workstreams[0].state_version,
    )
    with pytest.raises(DeterministicResolutionError, match="stale or unknown"):
        resolver.resolve(TurnObservations(
            "再次确认", approval_decision=True, approval_id="approval-1",
        ), completed)


def test_reconciling_transition_is_explicit_and_idempotent_at_current_version():
    state = _state(_workstream("refund-ws-1"))

    reconciling = state.mark_workstream_reconciling(
        "refund-ws-1",
        expected_version=1,
    )

    assert reconciling.version == state.version + 1
    assert reconciling.workstreams[0].status is WorkstreamStatus.RECONCILING
    assert reconciling.workstreams[0].phase == "RECONCILE"
    assert reconciling.mark_workstream_reconciling(
        "refund-ws-1",
        expected_version=2,
    ) is reconciling
    with pytest.raises(ConversationStateConflict, match="version changed"):
        reconciling.mark_workstream_reconciling(
            "refund-ws-1",
            expected_version=1,
        )


def test_expired_approval_fails_before_state_consumption():
    state = _state(_workstream()).wait_for_approval(PendingApprovalState(
        "approval-1", 1, "refund-ws-1", "refund-write-1",
        "refund.request.create:v1", "operation-refund-1",
        "order:DP1234", "3", "2026-09-04T10:00:00+00:00",
    ))
    resolver = DeterministicResolver(
        lambda: datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
    )

    resolution = resolver.resolve(TurnObservations(
        "", approval_decision=True, approval_id="approval-1",
    ), state)
    assert resolution.kind is ResolutionKind.APPROVAL_EXPIRED
    expired = state.consume_approval(
        approval_id=resolution.signal_id,
        approval_version=resolution.signal_version,
        approved=False,
    )
    assert expired.pending_approval is None
    assert expired.workstreams[0].status is WorkstreamStatus.CANCELLED


def test_resume_token_and_explicit_cancel_are_version_bound():
    state = _state(_workstream())
    state = ConversationState(
        state.tenant_id,
        state.user_id,
        state.conversation_id,
        state.version,
        state.workstreams,
        resume_bindings=(ResumeBinding("resume-secret", "refund-ws-1", 1),),
    )
    resolver = DeterministicResolver()

    resumed = resolver.resolve(TurnObservations("resume", resume_token="resume-secret"), state)
    assert resumed.kind is ResolutionKind.RESUME_WORKSTREAM

    cancelled = resolver.resolve(TurnObservations(
        "cancel",
        explicit_control=ExplicitControlSignal.CANCEL,
    ), state)
    assert cancelled.kind is ResolutionKind.CANCEL_WORKSTREAM
    terminal = state.cancel_workstream(
        cancelled.workstream_id,
        expected_version=cancelled.expected_workstream_version,
    )
    assert terminal.workstreams[0].status is WorkstreamStatus.CANCELLED


def test_ambiguous_control_signal_requests_target_instead_of_guessing():
    state = _state(
        _workstream(),
        WorkstreamState(
            "product-question-ws-1",
            "product_technical",
            "product_qa:v1",
            "WAIT_PRODUCT_REFERENCE",
            WorkstreamStatus.ACTIVE,
            1,
        ),
    )

    resolution = DeterministicResolver().resolve(TurnObservations(
        "continue",
        explicit_control=ExplicitControlSignal.CONTINUE,
    ), state)

    assert resolution.kind is ResolutionKind.CLARIFY_WORKSTREAM


def test_active_workstream_does_not_force_an_unrelated_message_to_continue():
    state = _state(_workstream())

    resolution = DeterministicResolver().resolve(
        TurnObservations("另外查一下商品保修政策"), state,
    )

    assert resolution.kind is ResolutionKind.UNRESOLVED
    assert resolution.reason_code == "NO_DETERMINISTIC_BINDING"


def test_store_compare_and_set_rejects_stale_writer():
    store = InMemoryConversationStateStore()
    identity = (TenantId("tenant-a"), UserId("user-a"), ConversationId("conversation-a"))
    first = store.load(*identity)
    concurrent = store.load(*identity)
    first_next = first.start_workstream(_workstream())
    concurrent_next = concurrent.start_workstream(_workstream("refund-ws-2"))

    assert store.compare_and_set(first, first_next) is True
    assert store.compare_and_set(concurrent, concurrent_next) is False
    assert store.load(*identity).workstreams == (_workstream(),)
