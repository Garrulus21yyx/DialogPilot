"""Durable Target v1 control-state and write-operation event stores.

Both adapters reuse the immutable conversation event log.  The log is the
business authority; LangGraph checkpoints remain execution-position data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.agent_result import RequestedField
from application.conversation_state import (
    ConversationOwner,
    ConversationState,
    ConversationStateError,
    PendingApprovalState,
    PendingInteractionState,
    ResumeBinding,
    WorkstreamState,
    WorkstreamStatus,
)
from application.conversation_store import ConversationScope
from application.work_item import ArgumentValue, WorkItem
from application.write_workflow import (
    OperationConflict,
    OperationRecord,
    OperationStatus,
    WriteWorkflowError,
)
from core.identity import ConversationId, TenantId, UserId


_STATE_EVENT = "target.conversation_state.changed.v1"
_OPERATION_EVENT = "target.write_operation.changed.v1"


class PostgresConversationStateStore:
    """CAS owner for the complete Target v1 conversation control aggregate."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def load(
        self,
        tenant_id: TenantId,
        user_id: UserId,
        conversation_id: ConversationId,
    ) -> ConversationState:
        scope = ConversationScope(tenant_id, user_id, conversation_id)
        with self.pool.transaction() as connection:
            subject = _subject(connection, scope, create=False, lock=False)
            if subject is None:
                return ConversationState.empty(
                    tenant_id=str(tenant_id),
                    user_id=str(user_id),
                    conversation_id=str(conversation_id),
                )
            _assert_active(subject)
            row = _latest_state_event(connection, scope)
        return (
            conversation_state_from_payload(row["payload"])
            if row is not None
            else ConversationState.empty(
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                conversation_id=str(conversation_id),
            )
        )

    def compare_and_set(
        self,
        current: ConversationState,
        next_state: ConversationState,
    ) -> bool:
        _validate_state_successor(current, next_state)
        scope = ConversationScope(
            current.tenant_id, current.user_id, current.conversation_id,
        )
        with self.pool.transaction() as connection:
            subject = _subject(connection, scope, create=True, lock=True)
            _assert_active(subject)
            row = _latest_state_event(connection, scope)
            stored = (
                conversation_state_from_payload(row["payload"])
                if row is not None
                else ConversationState.empty(
                    tenant_id=str(current.tenant_id),
                    user_id=str(current.user_id),
                    conversation_id=str(current.conversation_id),
                )
            )
            if stored.fingerprint != current.fingerprint:
                return False
            _append_event(
                connection,
                scope,
                event_type=_STATE_EVENT,
                logical_key=(
                    f"conversation-state:{current.conversation_id}:"
                    f"v{next_state.version}:{next_state.fingerprint}"
                ),
                payload=conversation_state_to_payload(next_state),
            )
            return True


class PostgresOperationLedger:
    """Conversation-bound monotonic ledger for governed business writes."""

    def __init__(self, pool, scope: ConversationScope) -> None:
        self.pool = pool
        self.scope = scope

    def acquire(self, item: WorkItem) -> OperationRecord:
        operation_key = str(item.operation_key or "")
        if not operation_key:
            raise WriteWorkflowError("write work item has no operation key")
        with self.pool.transaction() as connection:
            subject = _subject(connection, self.scope, create=True, lock=True)
            _assert_active(subject)
            current = _load_operation(connection, self.scope, operation_key)
            if current is None:
                current = OperationRecord(
                    operation_key,
                    item.fingerprint,
                    OperationStatus.PLANNED,
                    1,
                    0,
                    reason_code="OPERATION_REGISTERED",
                )
                _append_operation(connection, self.scope, current)
            elif current.work_item_fingerprint != item.fingerprint:
                raise OperationConflict("operation key is bound to another work item")
            return current

    def compare_and_set(
        self,
        current: OperationRecord,
        next_record: OperationRecord,
    ) -> bool:
        _validate_operation_successor(current, next_record)
        with self.pool.transaction() as connection:
            subject = _subject(connection, self.scope, create=False, lock=True)
            _assert_active(subject)
            stored = _load_operation(connection, self.scope, current.operation_key)
            if stored != current:
                return False
            _append_operation(connection, self.scope, next_record)
            return True


def conversation_state_to_payload(state: ConversationState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "tenant_id": str(state.tenant_id),
        "user_id": str(state.user_id),
        "conversation_id": str(state.conversation_id),
        "version": state.version,
        "owner": state.owner.value,
        "human_ticket_ref": state.human_ticket_ref,
        "workstreams": [
            {
                "workstream_id": item.workstream_id,
                "owner_agent": item.owner_agent,
                "capability_ref": item.capability_ref,
                "phase": item.phase,
                "status": item.status.value,
                "state_version": item.state_version,
                "slots": [
                    {"name": slot.name, "value_json": slot.value_json}
                    for slot in item.slots
                ],
                "flow_ref": item.flow_ref,
            }
            for item in state.workstreams
        ],
        "pending_interaction": (
            {
                "interaction_id": state.pending_interaction.interaction_id,
                "version": state.pending_interaction.version,
                "requested_fields": [
                    {
                        "field_name": item.field_name,
                        "target_work_item_id": item.target_work_item_id,
                        "value_schema": item.value_schema,
                    }
                    for item in state.pending_interaction.requested_fields
                ],
                "workstream_versions": list(state.pending_interaction.workstream_versions),
            }
            if state.pending_interaction else None
        ),
        "pending_approval": (
            dict(state.pending_approval.__dict__)
            if state.pending_approval else None
        ),
        "resume_bindings": [dict(item.__dict__) for item in state.resume_bindings],
        "consumed_signal_ids": list(state.consumed_signal_ids),
    }


def conversation_state_from_payload(raw: Mapping[str, object]) -> ConversationState:
    payload = dict(raw)
    if payload.get("schema_version") != "conversation-state-v1":
        raise ConversationStateError("unsupported conversation state schema")
    pending_raw = payload.get("pending_interaction")
    approval_raw = payload.get("pending_approval")
    return ConversationState(
        TenantId(payload["tenant_id"]),
        UserId(payload["user_id"]),
        ConversationId(payload["conversation_id"]),
        int(payload["version"]),
        tuple(
            WorkstreamState(
                str(item["workstream_id"]),
                str(item["owner_agent"]),
                str(item["capability_ref"]),
                str(item["phase"]),
                WorkstreamStatus(str(item["status"])),
                int(item["state_version"]),
                tuple(
                    ArgumentValue(str(slot["name"]), str(slot["value_json"]))
                    for slot in item.get("slots", ())
                ),
                str(item["flow_ref"]) if item.get("flow_ref") is not None else None,
            )
            for item in payload.get("workstreams", ())
        ),
        (
            PendingInteractionState(
                str(pending_raw["interaction_id"]),
                int(pending_raw["version"]),
                tuple(
                    RequestedField(
                        str(item["field_name"]),
                        str(item["target_work_item_id"]),
                        str(item["value_schema"]),
                    )
                    for item in pending_raw.get("requested_fields", ())
                ),
                tuple(
                    (str(item[0]), int(item[1]))
                    for item in pending_raw.get("workstream_versions", ())
                ),
            )
            if isinstance(pending_raw, Mapping) else None
        ),
        (
            PendingApprovalState(
                str(approval_raw["approval_id"]),
                int(approval_raw["version"]),
                str(approval_raw["workstream_id"]),
                str(approval_raw["work_item_id"]),
                str(approval_raw["action_ref"]),
                str(approval_raw["operation_key"]),
            )
            if isinstance(approval_raw, Mapping) else None
        ),
        tuple(
            ResumeBinding(
                str(item["token"]),
                str(item["workstream_id"]),
                int(item["workstream_version"]),
            )
            for item in payload.get("resume_bindings", ())
        ),
        tuple(str(item) for item in payload.get("consumed_signal_ids", ())),
        str(payload["schema_version"]),
        ConversationOwner(str(payload.get("owner", ConversationOwner.AUTOMATION.value))),
        (
            str(payload["human_ticket_ref"])
            if payload.get("human_ticket_ref") is not None else None
        ),
    )


def operation_to_payload(record: OperationRecord) -> dict[str, object]:
    return {
        "schema_version": "target-write-operation-v1",
        "operation_key": record.operation_key,
        "work_item_fingerprint": record.work_item_fingerprint,
        "status": record.status.value,
        "version": record.version,
        "attempts": record.attempts,
        "receipt_id": record.receipt_id,
        "receipt_schema_version": record.receipt_schema_version,
        "reason_code": record.reason_code,
    }


def operation_from_payload(raw: Mapping[str, object]) -> OperationRecord:
    payload = dict(raw)
    if payload.get("schema_version") != "target-write-operation-v1":
        raise WriteWorkflowError("unsupported write operation schema")
    return OperationRecord(
        str(payload["operation_key"]),
        str(payload["work_item_fingerprint"]),
        OperationStatus(str(payload["status"])),
        int(payload["version"]),
        int(payload["attempts"]),
        str(payload.get("receipt_id") or ""),
        str(payload.get("receipt_schema_version") or ""),
        str(payload.get("reason_code") or ""),
    )


def _validate_state_successor(current, next_state) -> None:
    if (
        current.tenant_id != next_state.tenant_id
        or current.user_id != next_state.user_id
        or current.conversation_id != next_state.conversation_id
    ):
        raise ConversationStateError("CAS states belong to different conversations")
    if next_state.version != current.version + 1:
        raise ConversationStateError("CAS must persist exactly one aggregate transition")


def _validate_operation_successor(current, next_record) -> None:
    if current.operation_key != next_record.operation_key:
        raise WriteWorkflowError("operation CAS keys differ")
    if next_record.version != current.version + 1:
        raise WriteWorkflowError("operation CAS must advance one version")
    if current.work_item_fingerprint != next_record.work_item_fingerprint:
        raise OperationConflict("operation binding cannot change")


def _subject(connection, scope: ConversationScope, *, create: bool, lock: bool):
    params = (str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id))
    if create:
        connection.execute("""
            INSERT INTO dialogpilot_app.conversations (
                tenant_id,user_id,conversation_id
            ) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
        """, params)
    suffix = " FOR UPDATE" if lock else ""
    return connection.execute("""
        SELECT next_event_seq,deleted_at
        FROM dialogpilot_app.conversations
        WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
    """ + suffix, params).fetchone()


def _assert_active(subject) -> None:
    if subject is None:
        raise ConversationStateError("conversation is unavailable")
    if subject[1] is not None:
        raise ConversationStateError("conversation is deleted")


def _latest_state_event(connection, scope: ConversationScope):
    params = (str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id))
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute("""
            SELECT payload,seq FROM dialogpilot_app.conversation_events
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
              AND event_type=%s
            ORDER BY seq DESC LIMIT 1
        """, (*params, _STATE_EVENT)).fetchone()


def _load_operation(connection, scope: ConversationScope, operation_key: str):
    params = (str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id))
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute("""
            SELECT payload FROM dialogpilot_app.conversation_events
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
              AND event_type=%s AND payload->>'operation_key'=%s
            ORDER BY seq DESC LIMIT 1
        """, (*params, _OPERATION_EVENT, operation_key)).fetchone()
    return operation_from_payload(row["payload"]) if row is not None else None


def _append_operation(connection, scope, record) -> None:
    _append_event(
        connection,
        scope,
        event_type=_OPERATION_EVENT,
        logical_key=f"write-operation:{record.operation_key}:v{record.version}",
        payload=operation_to_payload(record),
    )


def _append_event(connection, scope, *, event_type, logical_key, payload) -> None:
    subject = _subject(connection, scope, create=False, lock=False)
    _assert_active(subject)
    seq = int(subject[0])
    digest = hashlib.sha256(logical_key.encode("utf-8")).hexdigest()
    event_id = f"event:target:v1:{digest}"
    operation_key = f"operation:target:v1:{digest}"
    created_at = datetime.now(timezone.utc).isoformat()
    content_sha = hashlib.sha256(json.dumps(
        {"event_type": event_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()
    connection.execute("""
        INSERT INTO dialogpilot_app.conversation_events (
            event_id,operation_key,tenant_id,user_id,conversation_id,seq,
            event_type,payload,content_sha256,created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        event_id,
        operation_key,
        str(scope.tenant_id),
        str(scope.user_id),
        str(scope.conversation_id),
        seq,
        event_type,
        Jsonb(payload),
        content_sha,
        created_at,
    ))
    connection.execute("""
        UPDATE dialogpilot_app.conversations
        SET next_event_seq=next_event_seq+1,updated_at=%s
        WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
    """, (
        created_at,
        str(scope.tenant_id),
        str(scope.user_id),
        str(scope.conversation_id),
    ))
