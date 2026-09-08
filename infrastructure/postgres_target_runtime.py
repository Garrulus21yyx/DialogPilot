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
from pydantic import TypeAdapter

from application.agent_result import FactRecord, RequestedField
from application.capability_registry import (
    ActionReconciliationDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRisk,
)
from application.conversation_state import (
    AcceptedApprovalState,
    ConversationOwner,
    ConversationState,
    ConversationStateError,
    PendingApprovalState,
    PendingInteractionState,
    ResumeBinding,
    WorkstreamState,
    WorkstreamStatus,
    WorkControlState,
    WorkControlStatus,
)
from application.conversation_store import ConversationScope
from application.entity_binding import BindingSource, EntityBinding
from application.work_item import (
    ArgumentValue,
    ControlMode,
    WorkControlBinding,
    WorkItem,
)
from application.write_workflow import (
    OperationConflict,
    OperationRecord,
    OperationStatus,
    WriteWorkflowError,
)
from core.identity import ConversationId, TenantId, UserId


_STATE_EVENT = "target.conversation_state.changed.v1"
_OPERATION_EVENT = "target.write_operation.changed.v1"
_OPERATION_FACTS = TypeAdapter(tuple[FactRecord, ...])


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
                    item.operation_fingerprint,
                    OperationStatus.PLANNED,
                    1,
                    0,
                    reason_code="OPERATION_REGISTERED",
                )
                _append_operation(connection, self.scope, current)
            elif current.work_item_fingerprint != item.operation_fingerprint:
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
                "slot_bindings": [
                    _binding_to_payload(binding) for binding in item.slot_bindings
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
                        **({"question_hint": item.question_hint} if item.question_hint is not None else {}),
                    }
                    for item in state.pending_interaction.requested_fields
                ],
                "workstream_versions": list(state.pending_interaction.workstream_versions),
                "suspended_work_items": [
                    _work_item_to_payload(item)
                    for item in state.pending_interaction.suspended_work_items
                ],
                "checkpoint_thread_id": state.pending_interaction.checkpoint_thread_id,
            }
            if state.pending_interaction else None
        ),
        "pending_approval": (
            {
                **{
                    key: value
                    for key, value in state.pending_approval.__dict__.items()
                    if key not in {"arguments", "argument_bindings", "suspended_work_items"}
                },
                "arguments": [
                    {"name": item.name, "value_json": item.value_json}
                    for item in state.pending_approval.arguments
                ],
                "control": dict(state.pending_approval.control.__dict__) if state.pending_approval.control else None,
                "suspended_work_items": [_work_item_to_payload(work) for work in state.pending_approval.suspended_work_items],
                "argument_bindings": [
                    _binding_to_payload(binding)
                    for binding in state.pending_approval.argument_bindings
                ],
            }
            if state.pending_approval else None
        ),
        "resume_bindings": [dict(item.__dict__) for item in state.resume_bindings],
        "consumed_signal_ids": list(state.consumed_signal_ids),
        "accepted_approvals": [
            {
                **{
                    key: value for key, value in item.__dict__.items()
                    if key not in {"arguments", "argument_bindings", "suspended_work_items"}
                },
                "arguments": [
                    {"name": arg.name, "value_json": arg.value_json}
                    for arg in item.arguments
                ],
                "control": dict(item.control.__dict__) if item.control else None,
                "suspended_work_items": [_work_item_to_payload(work) for work in item.suspended_work_items],
                "argument_bindings": [
                    _binding_to_payload(binding) for binding in item.argument_bindings
                ],
            }
            for item in state.accepted_approvals
        ],
        "work_controls": [
            {
                "control_id": item.control_id,
                "revision": item.revision,
                "work_item_id": item.work_item_id,
                "invocation_key": item.invocation_key,
                "owner_agent": item.owner_agent,
                "objective": item.objective,
                "status": item.status.value,
                "state_snapshot_version": item.state_snapshot_version,
            }
            for item in state.work_controls
        ],
    }


def conversation_state_from_payload(raw: Mapping[str, object]) -> ConversationState:
    payload = dict(raw)
    if payload.get("schema_version") not in {
        "conversation-state-v2", "conversation-state-v3",
    }:
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
                tuple(
                    _binding_from_payload(binding)
                    for binding in item.get("slot_bindings", ())
                ),
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
                        item.get("question_hint"),
                    )
                    for item in pending_raw.get("requested_fields", ())
                ),
                tuple(
                    (str(item[0]), int(item[1]))
                    for item in pending_raw.get("workstream_versions", ())
                ),
                tuple(
                    _work_item_from_payload(item)
                    for item in pending_raw.get("suspended_work_items", ())
                ),
                (
                    str(pending_raw["checkpoint_thread_id"])
                    if pending_raw.get("checkpoint_thread_id") is not None else None
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
                str(approval_raw["target_entity_ref"]),
                str(approval_raw["target_entity_version"]),
                str(approval_raw["expires_at"]),
                tuple(
                    ArgumentValue(str(item["name"]), str(item["value_json"]))
                    for item in approval_raw.get("arguments", ())
                ),
                (
                    str(approval_raw["checkpoint_thread_id"])
                    if approval_raw.get("checkpoint_thread_id") is not None else None
                ),
                tuple(
                    _binding_from_payload(binding)
                    for binding in approval_raw.get("argument_bindings", ())
                ),
                tuple(_work_item_from_payload(work) for work in approval_raw.get("suspended_work_items", ())),
                approval_raw.get("origin_work_item_id"),
                WorkControlBinding(**approval_raw["control"]) if approval_raw.get("control") else None,
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
        "conversation-state-v3",
        ConversationOwner(str(payload.get("owner", ConversationOwner.AUTOMATION.value))),
        (
            str(payload["human_ticket_ref"])
            if payload.get("human_ticket_ref") is not None else None
        ),
        tuple(
            AcceptedApprovalState(
                str(item["approval_id"]),
                int(item["version"]),
                str(item["workstream_id"]),
                str(item["action_ref"]),
                str(item["operation_key"]),
                str(item["target_entity_ref"]),
                str(item["target_entity_version"]),
                tuple(
                    ArgumentValue(str(arg["name"]), str(arg["value_json"]))
                    for arg in item.get("arguments", ())
                ),
                tuple(
                    _binding_from_payload(binding)
                    for binding in item.get("argument_bindings", ())
                ),
                tuple(_work_item_from_payload(work) for work in item.get("suspended_work_items", ())),
                item.get("origin_work_item_id"),
                WorkControlBinding(**item["control"]) if item.get("control") else None,
            )
            for item in payload.get("accepted_approvals", ())
        ),
        tuple(
            WorkControlState(
                str(item["control_id"]),
                int(item["revision"]),
                str(item["work_item_id"]),
                str(item["invocation_key"]),
                str(item["owner_agent"]),
                str(item["objective"]),
                WorkControlStatus(str(item["status"])),
                int(item.get("state_snapshot_version") or 0),
            )
            for item in payload.get("work_controls", ())
        ),
    )


def _work_item_to_payload(item: WorkItem) -> dict[str, object]:
    return {
        "work_item_id": item.work_item_id,
        "owner_agent": item.owner_agent,
        "objective": item.objective,
        "control_mode": item.control_mode.value,
        "allowed_tools": list(item.allowed_tools),
        "allowed_skills": list(item.allowed_skills),
        "arguments": [
            {"name": value.name, "value_json": value.value_json}
            for value in item.arguments
        ],
        "argument_bindings": [
            _binding_to_payload(value) for value in item.argument_bindings
        ],
        "requirement_ids": list(item.requirement_ids),
        "dependencies": list(item.dependencies),
        "allowed_actions": list(item.allowed_actions),
        "continuation_of": item.continuation_of,
        "effect": item.effect.value,
        "risk": item.risk.value,
        "expected_output_schema": item.expected_output_schema,
        "verification_profile": item.verification_profile,
        "state_snapshot_version": item.state_snapshot_version,
        "registry_fingerprint": item.registry_fingerprint,
        "timeout_seconds": item.timeout_seconds,
        "max_steps": item.max_steps,
        "skill_hint": item.skill_hint,
        "flow_ref": item.flow_ref,
        "operation_key": item.operation_key,
        "approval_binding": item.approval_binding,
        "target_entity_version": item.target_entity_version,
        "aggregate_ref": item.aggregate_ref,
        "action_ref": item.action_ref,
        "approval_policy": (
            item.approval_policy.value if item.approval_policy else None
        ),
        "reconciliation": (
            {
                "tool_id": item.reconciliation.tool_id,
                "requirement_id": item.reconciliation.requirement_id,
                "operation_key_argument": item.reconciliation.operation_key_argument,
                "operation_key_field": item.reconciliation.operation_key_field,
                "passthrough_arguments": list(item.reconciliation.passthrough_arguments),
                "receipt_id_field": item.reconciliation.receipt_id_field,
            }
            if item.reconciliation else None
        ),
        "control": (
            {
                "control_id": item.control.control_id,
                "revision": item.control.revision,
            }
            if item.control else None
        ),
    }


def _work_item_from_payload(raw: Mapping[str, object]) -> WorkItem:
    reconciliation_raw = raw.get("reconciliation")
    reconciliation = (
        ActionReconciliationDefinition(
            str(reconciliation_raw["tool_id"]),
            str(reconciliation_raw["requirement_id"]),
            str(reconciliation_raw["operation_key_argument"]),
            str(reconciliation_raw["operation_key_field"]),
            tuple(str(value) for value in reconciliation_raw.get(
                "passthrough_arguments", (),
            )),
            str(reconciliation_raw["receipt_id_field"]),
        )
        if isinstance(reconciliation_raw, Mapping) else None
    )
    approval_policy = raw.get("approval_policy")
    control_raw = raw.get("control")
    return WorkItem(
        str(raw["work_item_id"]),
        str(raw["owner_agent"]),
        str(raw["objective"]),
        ControlMode(str(raw["control_mode"])),
        tuple(str(value) for value in raw.get("allowed_tools", ())),
        tuple(str(value) for value in raw.get("allowed_skills", ())),
        tuple(
            ArgumentValue(str(value["name"]), str(value["value_json"]))
            for value in raw.get("arguments", ())
        ),
        tuple(str(value) for value in raw.get("requirement_ids", ())),
        tuple(str(value) for value in raw.get("dependencies", ())),
        CapabilityEffect(str(raw["effect"])),
        CapabilityRisk(str(raw["risk"])),
        str(raw["expected_output_schema"]),
        str(raw["verification_profile"]),
        int(raw["state_snapshot_version"]),
        str(raw["registry_fingerprint"]),
        raw["timeout_seconds"],
        int(raw["max_steps"]),
        str(raw["skill_hint"]) if raw.get("skill_hint") is not None else None,
        str(raw["flow_ref"]) if raw.get("flow_ref") is not None else None,
        str(raw["operation_key"]) if raw.get("operation_key") is not None else None,
        str(raw["approval_binding"]) if raw.get("approval_binding") is not None else None,
        (
            str(raw["target_entity_version"])
            if raw.get("target_entity_version") is not None else None
        ),
        reconciliation,
        str(raw["aggregate_ref"]) if raw.get("aggregate_ref") is not None else None,
        str(raw["action_ref"]) if raw.get("action_ref") is not None else None,
        ApprovalPolicy(str(approval_policy)) if approval_policy is not None else None,
        tuple(
            _binding_from_payload(value)
            for value in raw.get("argument_bindings", ())
        ),
        (
            WorkControlBinding(
                str(control_raw["control_id"]), int(control_raw["revision"]),
            )
            if isinstance(control_raw, Mapping) else None
        ),
        tuple(str(value) for value in raw.get("allowed_actions", ())),
        raw.get("continuation_of"),
    )


def _binding_to_payload(value: EntityBinding) -> dict[str, object]:
    return {
        "field_name": value.field_name,
        "value_json": value.value_json,
        "source": value.source.value,
        "source_ref": value.source_ref,
        "tenant_id": value.tenant_id,
        "user_id": value.user_id,
        "conversation_id": value.conversation_id,
        "priority": value.priority,
        "source_version": value.source_version,
        "workstream_id": value.workstream_id,
        "valid_until": value.valid_until,
        "type_selection": value.type_selection,
    }


def _binding_from_payload(value: Mapping[str, object]) -> EntityBinding:
    return EntityBinding(
        field_name=str(value["field_name"]),
        value_json=str(value["value_json"]),
        source=BindingSource(str(value["source"])),
        source_ref=str(value["source_ref"]),
        tenant_id=str(value["tenant_id"]),
        user_id=str(value["user_id"]),
        conversation_id=str(value["conversation_id"]),
        priority=int(value["priority"]),
        type_selection=value.get("type_selection"),
        source_version=(
            int(value["source_version"])
            if value.get("source_version") is not None else None
        ),
        workstream_id=(
            str(value["workstream_id"])
            if value.get("workstream_id") is not None else None
        ),
        valid_until=(
            str(value["valid_until"])
            if value.get("valid_until") is not None else None
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
        "facts": _OPERATION_FACTS.dump_python(record.facts, mode="json"),
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
        _OPERATION_FACTS.validate_python(payload.get("facts", [])),
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
    identity = json.dumps(
        [str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id),
         event_type, logical_key],
        ensure_ascii=False, separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
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
