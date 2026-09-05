from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.conversation_state import (
    ConversationState,
    ConversationStateConflict,
    WorkControlStatus,
)
from application.work_item import ControlMode, WorkControlBinding, WorkItem


def _item(control_id: str, revision: int, *, work_item_id: str) -> WorkItem:
    return WorkItem(
        work_item_id=work_item_id,
        owner_agent="product_technical",
        objective=f"identify product revision {revision}",
        control_mode=ControlMode.DELEGATED,
        allowed_tools=("catalog_search",),
        allowed_skills=(),
        arguments=(),
        requirement_ids=("product.canonical_model",),
        dependencies=(),
        effect=CapabilityEffect.READ,
        risk=CapabilityRisk.LOW,
        expected_output_schema="agent-result-v1",
        verification_profile="product-v1",
        state_snapshot_version=0,
        registry_fingerprint="registry:test",
        timeout_seconds=8,
        max_steps=4,
        control=WorkControlBinding(control_id, revision),
    )


def test_revising_one_control_invalidates_only_that_objective() -> None:
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conv-a",
    )
    product_v1 = _item("control:product", 1, work_item_id="product-v1")
    refund_v1 = _item("control:refund", 1, work_item_id="refund-v1")
    state = state.accept_work_items(
        (product_v1, refund_v1), invocation_key="invocation-1",
    )

    product_v2 = _item("control:product", 2, work_item_id="product-v2")
    state = state.accept_work_items(
        (product_v2,), invocation_key="invocation-2",
    )

    assert not state.accepts(product_v1.control)
    assert state.accepts(product_v2.control)
    assert state.accepts(refund_v1.control)


def test_work_control_revisions_cannot_skip_or_replay() -> None:
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conv-a",
    )
    first = _item("control:product", 1, work_item_id="product-v1")
    state = state.accept_work_items((first,), invocation_key="invocation-1")

    for revision in (1, 3):
        candidate = _item(
            "control:product", revision, work_item_id=f"product-v{revision}",
        )
        try:
            state.accept_work_items((candidate,), invocation_key="invocation-2")
        except ConversationStateConflict:
            pass
        else:
            raise AssertionError("stale or skipped control revision was accepted")


def test_closed_control_rejects_late_results() -> None:
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conv-a",
    )
    item = _item("control:product", 1, work_item_id="product-v1")
    state = state.accept_work_items((item,), invocation_key="invocation-1")
    state = state.close_work_control(
        item.control, status=WorkControlStatus.CANCELLED,
    )

    assert not state.accepts(item.control)
