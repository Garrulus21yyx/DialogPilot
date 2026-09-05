import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.conversation_state import (
    ConversationState,
    ConversationStateConflict,
    WorkControlStatus,
)
from application.work_item import ControlMode, WorkControlBinding, WorkItem
from application.orchestration_runtime import OrchestrationRuntime
from application.work_control import WorkControlGuard
from application.work_item import WorkPlan
from infrastructure.postgres_publication import (
    PostgresPublicationService,
    PublicationConflictError,
)


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


def test_running_revision_is_discarded_without_stopping_unrelated_work() -> None:
    store_state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conv-a",
    )
    product_v1 = _item("control:product", 1, work_item_id="product-v1")
    refund_v1 = replace(
        _item("control:refund", 1, work_item_id="refund-v1"),
        owner_agent="billing_refund",
        objective="query refund",
        allowed_tools=("refund_status",),
        requirement_ids=("refund.current_state",),
    )
    from application.conversation_state import InMemoryConversationStateStore
    store = InMemoryConversationStateStore()
    assert store.compare_and_set(
        store_state,
        store_state.accept_work_items(
            (product_v1, refund_v1), invocation_key="invocation-1",
        ),
    )

    async def execute(context):
        item = context.work_item
        if item.control.control_id == "control:product":
            current = store.load("tenant-a", "user-a", "conv-a")
            product_v2 = replace(
                item,
                work_item_id="product-v2",
                objective="identify corrected product",
                control=WorkControlBinding("control:product", 2),
            )
            assert store.compare_and_set(
                current,
                current.accept_work_items(
                    (product_v2,), invocation_key="invocation-2",
                ),
            )
        fact = FactRecord(
            f"work:{item.work_item_id}",
            item.requirement_ids[0],
            '{"status":"ok"}',
            FactSourceKind.VERIFIED_STATE,
            f"receipt:{item.work_item_id}",
            item.allowed_tools[0],
            "v1",
            datetime.now(timezone.utc),
        )
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "DONE",
            "test-v1",
            facts=(fact,),
        )

    runtime = OrchestrationRuntime(
        direct_executor=execute,
        domain_workers={
            "product_technical": execute,
            "billing_refund": execute,
        },
        control_guard=WorkControlGuard(store),
    )
    board = asyncio.run(runtime.execute(
        WorkPlan((product_v1, refund_v1), product_v1.work_item_id),
        current_message="run both",
        trusted_context={
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "conversation_id": "conv-a",
        },
    ))

    by_id = {item.work_item_id: item for item in board.results}
    assert by_id["product-v1"].status is AgentResultStatus.SUPERSEDED
    assert by_id["refund-v1"].status is AgentResultStatus.SUCCEEDED
    assert board.partial_delivery_allowed


def test_publication_boundary_rejects_a_superseded_revision() -> None:
    class Connection:
        def execute(self, _sql, _params):
            return self

        def fetchone(self):
            return ({
                "work_controls": [{
                    "control_id": "control:product",
                    "revision": 2,
                    "status": "ACTIVE",
                }],
            },)

    command = SimpleNamespace(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conv-a",
        expected_work_controls=(WorkControlBinding("control:product", 1),),
    )

    with pytest.raises(PublicationConflictError):
        PostgresPublicationService._assert_work_controls(Connection(), command)
