"""Atomic PostgreSQL publication and canonical delivery transition repository."""
from __future__ import annotations

import hashlib
import hmac
from typing import Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.admission_contract import FinalPublicationCommitted
from application.conversation_store import content_hash
from application.delivery_contract import (
    ConnectorCapability,
    DeliveryEvent,
    DeliveryState,
    DeliveryStatusV1,
    transition_delivery,
)
from application.publication import (
    FinalResponseCommand,
    HumanReplyCommand,
    InteractionRequestCommand,
    PublicationApplyStatus,
    PublicationCommand,
    PublicationKind,
    PublicationRecord,
    PublicationResult,
    command_fingerprint,
    publication_identity,
)
from core.identity import InvocationKey
from infrastructure.postgres import PostgresPool


class PublicationConflictError(RuntimeError):
    pass


class PublicationNotFoundError(RuntimeError):
    pass


class PostgresPublicationService:
    def __init__(
        self,
        pool: PostgresPool,
        *,
        resume_binding_secret: str,
        fault_hook: Callable[[str], None] | None = None,
    ):
        if not resume_binding_secret:
            raise ValueError("resume binding signing secret is required")
        self.pool = pool
        self.secret = resume_binding_secret.encode("utf-8")
        self.fault_hook = fault_hook or (lambda _stage: None)

    def select_final_response(self, command: FinalResponseCommand) -> PublicationResult:
        return self._publish(command)

    def publish_interaction_request(
        self, command: InteractionRequestCommand,
    ) -> PublicationResult:
        return self._publish(command)

    def publish_human_reply(self, command: HumanReplyCommand) -> PublicationResult:
        return self._publish(command)

    def final_commit(self, result: PublicationResult) -> FinalPublicationCommitted:
        if result.record.kind is not PublicationKind.FINAL_RESPONSE:
            raise ValueError("only final response is an execution terminal fact")
        with self.pool.transaction() as connection:
            row = connection.execute(
                "SELECT payload FROM dialogpilot_app.response_deliveries "
                "WHERE publication_id=%s", (result.record.publication_id,),
            ).fetchone()
        return FinalPublicationCommitted(
            response_id=result.record.publication_id,
            response=dict(row[0]),
            outbound_event_id=result.record.outbound_event_id,
            delivery_outbox_id=result.record.delivery_outbox_id,
        )

    def _publish(self, command: PublicationCommand) -> PublicationResult:
        publication_id, operation_key, outbox_id = publication_identity(command)
        fingerprint = command_fingerprint(command)
        kind, content, role, payload, event_type = self._parts(command)
        with self.pool.transaction() as connection:
            existing = self._by_id(connection, publication_id)
            if existing is not None:
                return self._replay(existing, fingerprint)
            scope = (command.tenant_id, command.user_id, command.conversation_id)
            conversation = connection.execute("""
                SELECT next_turn_seq, next_event_seq, next_publication_seq
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, scope).fetchone()
            if conversation is None:
                raise PublicationNotFoundError("conversation scope does not exist")
            existing = self._by_id(connection, publication_id)
            if existing is not None:
                return self._replay(existing, fingerprint)
            if isinstance(command, (FinalResponseCommand, InteractionRequestCommand)):
                invocation = connection.execute("""
                    SELECT 1 FROM dialogpilot_app.workflow_invocations
                    WHERE invocation_key=%s AND tenant_id=%s AND user_id=%s
                      AND conversation_id=%s
                """, (str(command.invocation_key), *scope)).fetchone()
                if invocation is None:
                    raise PublicationNotFoundError("invocation scope does not exist")
            if isinstance(command, (FinalResponseCommand, InteractionRequestCommand)):
                self._assert_work_controls(connection, command)

            turn_key = _stable_id("publication-turn", publication_id)
            turn_id = _stable_id("publication-turn-id", publication_id)
            event_id = _stable_id("publication-event", publication_id)
            event_operation = _stable_id("publication-event-operation", publication_id)
            content_sha = content_hash({"kind": kind.value, "payload": payload})
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_turns (
                    turn_key, turn_id, tenant_id, user_id, conversation_id,
                    seq, role, content, content_sha256, invocation_key,
                    metadata, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                turn_key, turn_id, *scope, int(conversation[0]), role,
                content, content_sha,
                str(getattr(command, "invocation_key", "")) or None,
                Jsonb({"publication_id": publication_id, "publication_kind": kind.value}),
                command.created_at,
            ))
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET next_turn_seq=next_turn_seq+1, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (command.created_at, *scope))
            self.fault_hook("after_outbound_turn")

            event_payload = {
                "publication_id": publication_id,
                "publication_kind": kind.value,
                "outbound_turn_key": turn_key,
                "projection_disposition": command.projection_disposition.value,
            }
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_events (
                    event_id, operation_key, tenant_id, user_id, conversation_id,
                    seq, event_type, payload, content_sha256, invocation_key,
                    created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                event_id, event_operation, *scope, int(conversation[1]), event_type,
                Jsonb(event_payload), content_hash({
                    "event_type": event_type, "payload": event_payload,
                }), str(getattr(command, "invocation_key", "")) or None,
                command.created_at,
            ))
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET next_event_seq=next_event_seq+1, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (command.created_at, *scope))
            self.fault_hook("after_outbound_event")

            extra = self._extra_columns(command, publication_id)
            connection.execute("""
                INSERT INTO dialogpilot_app.response_deliveries (
                    publication_id, publication_kind, delivery_operation_key,
                    tenant_id, user_id, conversation_id, seq, invocation_key,
                    payload, content_sha256, status, created_at, command_fingerprint,
                    outbound_turn_key, outbound_event_id, candidate_id,
                    verifier_status, verification, evidence_sha256, bundle_version,
                    index_manifest_sha256, signal_id, signal_version,
                    resume_binding_signature, ticket_id, handoff_id, human_message_id,
                    connector_capability, max_attempts, next_attempt_at,
                    retry_policy_version, reconcile_deadline
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SELECTED',%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
            """, (
                publication_id, kind.value, operation_key, *scope,
                int(conversation[2]),
                str(getattr(command, "invocation_key", "")) or None,
                Jsonb(payload), content_sha, command.created_at, fingerprint,
                turn_key, event_id,
                extra["candidate_id"], extra["verifier_status"],
                Jsonb(extra["verification"]) if extra["verification"] is not None else None,
                extra["evidence_sha256"], extra["bundle_version"],
                extra["index_manifest_sha256"], extra["signal_id"],
                extra["signal_version"], extra["resume_binding_signature"],
                extra["ticket_id"], extra["handoff_id"], extra["human_message_id"],
                command.policy.connector_capability.value,
                command.policy.max_attempts, command.policy.next_attempt_at,
                command.policy.retry_policy_version, command.policy.reconcile_deadline,
            ))
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET next_publication_seq=next_publication_seq+1, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (command.created_at, *scope))
            self.fault_hook("after_delivery_fact")

            connection.execute("""
                INSERT INTO dialogpilot_app.delivery_outbox (
                    outbox_id, publication_id, delivery_operation_key, payload,
                    available_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                outbox_id, publication_id, operation_key, Jsonb({
                    "publication_id": publication_id,
                    "publication_kind": kind.value,
                    "delivery_operation_key": operation_key,
                }), command.policy.next_attempt_at or command.created_at,
                command.created_at,
            ))
            self.fault_hook("after_delivery_outbox")
            row = self._by_id(connection, publication_id)
            return PublicationResult(PublicationApplyStatus.APPLIED, self._record(row))

    @staticmethod
    def _assert_work_controls(connection, command: FinalResponseCommand | InteractionRequestCommand) -> None:
        """Validate the target revision while holding the conversation row lock."""
        if not command.expected_work_controls:
            return
        row = connection.execute("""
            SELECT payload
            FROM dialogpilot_app.conversation_events
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
              AND event_type='target.conversation_state.changed.v1'
            ORDER BY seq DESC LIMIT 1
        """, (
            command.tenant_id, command.user_id, command.conversation_id,
        )).fetchone()
        if row is None:
            raise PublicationConflictError("publication lacks work control state")
        current = {
            str(item.get("control_id")): (
                int(item.get("revision") or 0), str(item.get("status") or "")
            )
            for item in dict(row[0] or {}).get("work_controls", ())
        }
        if any(
            current.get(binding.control_id)
            != (binding.revision, "ACTIVE")
            for binding in command.expected_work_controls
        ):
            raise PublicationConflictError("publication work control is stale")

    @staticmethod
    def _parts(command: PublicationCommand):
        if isinstance(command, FinalResponseCommand):
            return (
                PublicationKind.FINAL_RESPONSE,
                command.response_text,
                "assistant",
                {
                    "response_id": publication_identity(command)[0],
                    "response": command.response_text,
                    "producer": command.producer,
                    "verification": dict(command.verification),
                    "public_response": dict(command.public_response),
                    "execution_stages": [
                        dict(item) for item in command.execution_stages
                    ],
                },
                "FINAL_RESPONSE_SELECTED",
            )
        if isinstance(command, InteractionRequestCommand):
            return (
                PublicationKind.INTERACTION_REQUEST,
                command.challenge,
                "assistant",
                {
                    "challenge": command.challenge,
                    "signal_id": command.signal_id,
                    "signal_version": command.signal_version,
                    "resume_schema": dict(command.resume_schema),
                    "execution_stages": [dict(item) for item in command.execution_stages],
                    "related_signals": [{"signal_id": key, "signal_version": version}
                                        for key, version in command.related_signals],
                },
                "INTERACTION_REQUEST_PUBLISHED",
            )
        return (
            PublicationKind.HUMAN_REPLY,
            command.text,
            "human",
            {
                "text": command.text,
                "ticket_id": command.ticket_id,
                "handoff_id": command.handoff_id,
                "human_message_id": command.human_message_id,
            },
            "HUMAN_REPLY_PUBLISHED",
        )

    def _extra_columns(self, command: PublicationCommand, publication_id: str):
        result = {
            "candidate_id": None, "verifier_status": None, "verification": None,
            "evidence_sha256": None, "bundle_version": None,
            "index_manifest_sha256": None, "signal_id": None,
            "signal_version": None, "resume_binding_signature": None,
            "ticket_id": None, "handoff_id": None, "human_message_id": None,
        }
        if isinstance(command, FinalResponseCommand):
            result.update({
                "candidate_id": command.candidate_id,
                "verifier_status": command.verifier_status,
                "verification": dict(command.verification),
                "evidence_sha256": command.evidence_sha256,
                "bundle_version": command.bundle_version,
                "index_manifest_sha256": command.index_manifest_sha256,
            })
        elif isinstance(command, InteractionRequestCommand):
            result.update({
                "signal_id": command.signal_id,
                "signal_version": command.signal_version,
                "resume_binding_signature": self._resume_signature(
                    publication_id, command.signal_id, command.signal_version,
                ),
            })
        else:
            result.update({
                "ticket_id": command.ticket_id,
                "handoff_id": command.handoff_id,
                "human_message_id": command.human_message_id,
            })
        # Internal evidence belongs to the private column, never delivery payload.
        evidence = getattr(command, "knowledge_evidence", ())
        if evidence:
            result["verification"] = {
                **(result["verification"] or {}),
                "knowledge_evidence": list(evidence),
            }
        return result

    def _resume_signature(self, publication_id: str, signal_id: str, version: int) -> str:
        payload = f"v1\0{publication_id}\0{signal_id}\0{version}".encode("utf-8")
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _by_id(connection, publication_id: str):
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT delivery_outbox.outbox_id, response_deliveries.* "
                "FROM dialogpilot_app.response_deliveries "
                "LEFT JOIN dialogpilot_app.delivery_outbox USING (publication_id) "
                "WHERE publication_id=%s", (publication_id,),
            ).fetchone()

    @staticmethod
    def _replay(row, expected_fingerprint: str) -> PublicationResult:
        status = (
            PublicationApplyStatus.ALREADY_APPLIED
            if row["command_fingerprint"] == expected_fingerprint
            else PublicationApplyStatus.IDEMPOTENCY_CONFLICT
        )
        return PublicationResult(status, PostgresPublicationService._record(row))

    @staticmethod
    def _record(row) -> PublicationRecord:
        return PublicationRecord(
            publication_id=row["publication_id"],
            kind=PublicationKind(row["publication_kind"]),
            delivery_operation_key=row["delivery_operation_key"],
            tenant_id=row["tenant_id"], user_id=row["user_id"],
            conversation_id=row["conversation_id"], seq=int(row["seq"]),
            content_sha256=row["content_sha256"],
            status=DeliveryStatusV1(row["status"]),
            outbound_turn_key=row["outbound_turn_key"],
            outbound_event_id=row["outbound_event_id"],
            delivery_outbox_id=row["outbox_id"] or "",
            invocation_key=(
                InvocationKey(row["invocation_key"]) if row["invocation_key"] else None
            ),
        )


class PostgresDeliveryRepository:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def apply_event(
        self,
        *,
        publication_id: str,
        receipt_id: str,
        event: DeliveryEvent,
        payload: dict,
        created_at: str,
    ) -> PublicationRecord:
        payload_sha = content_hash({"event": event.value, "payload": payload})
        with self.pool.transaction() as connection:
            row = self._locked(connection, publication_id)
            if row is None:
                raise PublicationNotFoundError(publication_id)
            existing = connection.execute("""
                SELECT payload_sha256 FROM dialogpilot_app.delivery_receipts
                WHERE receipt_id=%s
            """, (receipt_id,)).fetchone()
            if existing is not None:
                if existing[0] != payload_sha:
                    raise PublicationConflictError("receipt ID content changed")
                return PostgresPublicationService._record(
                    PostgresPublicationService._by_id(connection, publication_id)
                )
            state = DeliveryState(
                DeliveryStatusV1(row["status"]), int(row["attempt"]),
                int(row["max_attempts"]),
                ConnectorCapability(row["connector_capability"]),
                row["retry_policy_version"], row["reconcile_deadline"].isoformat(),
            )
            result = transition_delivery(state, event)
            delivered_at = row["delivered_at"] or (
                created_at if result.state.status in {
                    DeliveryStatusV1.DELIVERED, DeliveryStatusV1.READ,
                } else None
            )
            read_at = row["read_at"] or (
                created_at if result.state.status is DeliveryStatusV1.READ
                else None
            )
            connection.execute("""
                UPDATE dialogpilot_app.response_deliveries
                SET status=%s, attempt=%s, delivered_at=%s, read_at=%s,
                    version=version+1,
                    last_error_code=CASE WHEN %s IN ('FAILED','DELIVERY_UNCERTAIN')
                                         THEN %s ELSE last_error_code END
                WHERE publication_id=%s
            """, (
                result.state.status.value, result.state.attempt,
                delivered_at, read_at, result.state.status.value,
                result.reason_code, publication_id,
            ))
            connection.execute("""
                INSERT INTO dialogpilot_app.delivery_receipts (
                    receipt_id, publication_id, delivery_event, payload,
                    payload_sha256, resulting_status, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (
                receipt_id, publication_id, event.value, Jsonb(payload), payload_sha,
                result.state.status.value, created_at,
            ))
            return PostgresPublicationService._record(
                PostgresPublicationService._by_id(connection, publication_id)
            )

    @staticmethod
    def _locked(connection, publication_id: str):
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT NULL::text AS outbox_id, * FROM "
                "dialogpilot_app.response_deliveries WHERE publication_id=%s "
                "FOR UPDATE", (publication_id,),
            ).fetchone()


def _stable_id(namespace: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{namespace}:v1:{digest}"
