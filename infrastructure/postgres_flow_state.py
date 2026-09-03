"""PostgreSQL owner for one conversation's finite active-flow aggregate."""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.data_location_registry import (
    DataSubjectRef,
    DataWriteIntent,
    DurableWriteKind,
)
from application.flow_state import FlowStateAggregate, FlowStateError
from application.turn_state import (
    ActiveFlowRef,
    FlowBinding,
    FlowAggregateVersion,
    FlowDefinitionRef,
    PendingInputKind,
    PendingSignalRef,
    PrincipalScope,
)
from infrastructure.data_location_fence import PostgresDataLocationWriteFence


class PostgresFlowStateStore:
    location_id = "location:conversation-flow-state:v1"
    producer = "flow-state-store"

    def __init__(self, pool, *, fence=None) -> None:
        self.pool = pool
        self.fence = fence or PostgresDataLocationWriteFence(pool)

    def load(self, principal: PrincipalScope) -> FlowStateAggregate:
        scope = _scope(principal)
        with self.pool.transaction() as connection:
            subject = connection.execute("""
                SELECT deletion_epoch,deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, scope).fetchone()
            if subject is None or subject[1] is not None:
                raise FlowStateError("conversation flow state is unavailable")
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute("""
                    SELECT aggregate_id,state,version,deletion_epoch
                    FROM dialogpilot_app.conversation_flow_state
                    WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                """, scope).fetchone()
        if row is None:
            return FlowStateAggregate.empty(
                principal,
                deletion_epoch=int(subject[0]),
            )
        if int(row["deletion_epoch"]) != int(subject[0]):
            raise FlowStateError("conversation flow state deletion epoch differs")
        return _restore(principal, row)

    def compare_and_set(
        self,
        current: FlowStateAggregate,
        next_state: FlowStateAggregate,
    ) -> bool:
        if (
            next_state.principal != current.principal
            or next_state.aggregate.aggregate_id != current.aggregate.aggregate_id
            or next_state.aggregate.version != current.aggregate.version + 1
            or next_state.deletion_epoch != current.deletion_epoch
        ):
            raise FlowStateError("next flow state does not follow current state")
        self.fence.authorize(DataWriteIntent(
            location_id=self.location_id,
            producer=self.producer,
            kind=DurableWriteKind.PRODUCER,
            schema_version=next_state.schema_version,
            retention_class="conversation_operational",
            subject=DataSubjectRef(*_scope(next_state.principal)),
            expected_deletion_epoch=next_state.deletion_epoch,
        ))
        scope = _scope(current.principal)
        payload = Jsonb(_state_payload(next_state))
        with self.pool.transaction() as connection:
            if current.aggregate.version == 0:
                row = connection.execute("""
                    INSERT INTO dialogpilot_app.conversation_flow_state (
                        tenant_id,user_id,conversation_id,aggregate_id,state,
                        version,deletion_epoch
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,user_id,conversation_id) DO NOTHING
                    RETURNING version
                """, (
                    *scope,
                    next_state.aggregate.aggregate_id,
                    payload,
                    next_state.aggregate.version,
                    next_state.deletion_epoch,
                )).fetchone()
            else:
                row = connection.execute("""
                    UPDATE dialogpilot_app.conversation_flow_state
                    SET state=%s,version=%s,updated_at=transaction_timestamp()
                    WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                      AND aggregate_id=%s AND version=%s
                      AND deletion_epoch=%s
                    RETURNING version
                """, (
                    payload,
                    next_state.aggregate.version,
                    *scope,
                    current.aggregate.aggregate_id,
                    current.aggregate.version,
                    current.deletion_epoch,
                )).fetchone()
        return row is not None


def _state_payload(state: FlowStateAggregate) -> dict[str, object]:
    pending = state.pending_slot
    return {
        "schema_version": state.schema_version,
        "active_flows": [{
            "flow_id": flow.definition.flow_id,
            "flow_version": flow.definition.version,
            "instance_id": flow.instance_id,
            "state_version": flow.state_version,
            "bindings": [{
                "name": binding.name,
                "value_json": binding.value_json,
            } for binding in flow.bindings],
        } for flow in state.active_flows],
        "pending_slot": ({
            "signal_id": pending.signal_id,
            "signal_version": pending.signal_version,
            "flow_instance_id": pending.flow_instance_id,
            "field_name": pending.field_name,
        } if pending else None),
    }


def _restore(principal: PrincipalScope, row) -> FlowStateAggregate:
    payload = dict(row["state"])
    if payload.get("schema_version") != "conversation-flow-state-v1":
        raise FlowStateError("unsupported conversation flow state schema")
    flows = tuple(ActiveFlowRef(
        FlowDefinitionRef(str(item["flow_id"]), str(item["flow_version"])),
        str(item["instance_id"]),
        int(item["state_version"]),
        principal.fingerprint,
        tuple(FlowBinding(
            str(binding["name"]),
            str(binding["value_json"]),
        ) for binding in item.get("bindings", ())),
    ) for item in payload.get("active_flows", ()))
    raw_pending = payload.get("pending_slot")
    pending = PendingSignalRef(
        str(raw_pending["signal_id"]),
        int(raw_pending["signal_version"]),
        PendingInputKind.SLOT_VALUE,
        str(raw_pending["flow_instance_id"]),
        str(raw_pending["field_name"]),
        principal.fingerprint,
    ) if raw_pending else None
    return FlowStateAggregate(
        principal=principal,
        aggregate=FlowAggregateVersion(
            str(row["aggregate_id"]),
            int(row["version"]),
        ),
        active_flows=flows,
        pending_slot=pending,
        deletion_epoch=int(row["deletion_epoch"]),
    )


def _scope(principal: PrincipalScope) -> tuple[str, str, str]:
    return principal.tenant_id, principal.user_id, principal.conversation_id
