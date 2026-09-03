from datetime import datetime, timezone

import pytest

from application.turn_state import (
    ActiveFlowRef,
    FlowAggregateVersion,
    FlowDefinitionRef,
    PendingSlotRef,
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateError,
    TurnStateSnapshot,
)


def test_snapshot_binds_active_flow_and_slot_to_authenticated_principal() -> None:
    principal = PrincipalScope("tenant-1", "user-1", "conversation-1")
    flow = ActiveFlowRef(
        FlowDefinitionRef("refund_status", "v1"),
        "flow-1",
        7,
        principal.fingerprint,
    )
    slot = PendingSlotRef(
        "signal-1",
        4,
        flow.instance_id,
        "order_id",
        principal.fingerprint,
    )
    snapshot = TurnStateSnapshot(
        "request-1",
        principal,
        FlowAggregateVersion("flow-state:conversation-1", 9),
        (flow,),
        slot,
        (),
        (),
        (),
        (StateSourceStatus(
            "flow_state", StateAvailability.CURRENT, "flow-store-v1",
        ),),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert snapshot.pending_slot.flow_instance_id == snapshot.active_flows[0].instance_id


def test_snapshot_rejects_cross_principal_state() -> None:
    principal = PrincipalScope("tenant-1", "user-1", "conversation-1")
    foreign = PrincipalScope("tenant-2", "user-1", "conversation-1")
    flow = ActiveFlowRef(
        FlowDefinitionRef("refund_status", "v1"),
        "flow-1",
        7,
        foreign.fingerprint,
    )
    with pytest.raises(TurnStateError, match="another principal"):
        TurnStateSnapshot(
            "request-1",
            principal,
            FlowAggregateVersion("flow-state:conversation-1", 9),
            (flow,),
            None,
            (),
            (),
            (),
            (),
            datetime(2026, 9, 3, tzinfo=timezone.utc),
        )
