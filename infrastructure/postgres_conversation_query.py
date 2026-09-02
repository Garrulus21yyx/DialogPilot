"""Scoped PostgreSQL transcript/status projection and audited close command."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Mapping

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.admission_contract import AdmissionStatus, ExecutionStatus
from application.conversation_query import (
    ConversationCloseResult,
    InvocationStatusView,
    ProjectionProgressView,
    TranscriptTurnView,
)
from application.conversation_store import content_hash
from application.delivery_contract import DeliveryStatusV1
from infrastructure.postgres import PostgresPool


class ConversationQueryNotFound(RuntimeError):
    pass


class ConversationQueryAccessDenied(RuntimeError):
    pass


class PublicTranscriptRedactor:
    _BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
    _SECRET = re.compile(
        r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*([^\s,;]{4,})"
    )
    _EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
    _CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

    def redact(self, value: str) -> str:
        text = self._BEARER.sub("[REDACTED_CREDENTIAL]", str(value))
        text = self._SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
        text = self._EMAIL.sub("[REDACTED_EMAIL]", text)
        return self._CARD.sub("[REDACTED_CARD]", text)


class PostgresConversationQueryService:
    def __init__(
        self,
        pool: PostgresPool,
        *,
        runtime_reader: Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
        | None = None,
        ticket_reader: Callable[[str, str], Mapping[str, Any] | None] | None = None,
        redactor: PublicTranscriptRedactor | None = None,
    ):
        self.pool = pool
        self.runtime_reader = runtime_reader or (lambda _invocation: None)
        self.ticket_reader = ticket_reader or (lambda _user, _conversation: None)
        self.redactor = redactor or PublicTranscriptRedactor()

    def list_turns(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        after_seq: int = 0,
        limit: int = 100,
    ) -> tuple[TranscriptTurnView, ...]:
        after_seq = max(0, int(after_seq))
        limit = max(1, min(200, int(limit)))
        with self.pool.transaction() as connection:
            self._require_scope(connection, tenant_id, user_id, conversation_id)
            rows = connection.execute("""
                SELECT seq, role, content, created_at, request_id
                FROM dialogpilot_app.conversation_turns
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND seq > %s
                ORDER BY seq LIMIT %s
            """, (
                tenant_id, user_id, conversation_id, after_seq, limit,
            )).fetchall()
        return tuple(TranscriptTurnView(
            seq=int(row[0]), role=row[1], content=self.redactor.redact(row[2]),
            created_at=row[3].isoformat(), request_id=row[4],
        ) for row in rows)

    def projection_progress(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        required_projections: tuple[str, ...] = (
            "working_window", "thread_summary", "episodic_index", "fact_extraction",
        ),
    ) -> ProjectionProgressView:
        scope = (tenant_id, user_id, conversation_id)
        with self.pool.transaction() as connection:
            conversation = self._require_scope(connection, *scope)
            target = int(conversation["next_event_seq"]) - 1
            rows = connection.execute("""
                SELECT registry.projection_name,
                       COALESCE(watermark.last_event_seq, 0)
                FROM dialogpilot_app.projection_registry registry
                LEFT JOIN dialogpilot_app.projection_watermarks watermark
                  ON watermark.projection_name=registry.projection_name
                 AND watermark.generation=registry.generation
                 AND watermark.tenant_id=%s AND watermark.user_id=%s
                 AND watermark.conversation_id=%s
                WHERE registry.projection_name = ANY(%s)
            """, (*scope, list(required_projections))).fetchall()
        watermarks = {row[0]: int(row[1]) for row in rows}
        for projection in required_projections:
            watermarks.setdefault(projection, 0)
        return ProjectionProgressView(
            target_event_seq=target,
            watermarks=dict(sorted(watermarks.items())),
            caught_up=all(
                watermarks[projection] >= target for projection in required_projections
            ),
        )

    def invocation_status(
        self,
        invocation_key: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> InvocationStatusView:
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                invocation = cursor.execute("""
                    SELECT * FROM dialogpilot_app.workflow_invocations
                    WHERE invocation_key=%s
                """, (invocation_key,)).fetchone()
                if invocation is None:
                    raise ConversationQueryNotFound("invocation not found")
                if (
                    invocation["tenant_id"] != tenant_id
                    or invocation["user_id"] != user_id
                ):
                    raise ConversationQueryAccessDenied("invocation scope denied")
                publications = cursor.execute("""
                    SELECT publication_id, publication_kind, payload, status,
                           outbound_event_id
                    FROM dialogpilot_app.response_deliveries
                    WHERE invocation_key=%s
                    ORDER BY seq
                """, (invocation_key,)).fetchall()
        final = next((
            row for row in publications if row["publication_kind"] == "final_response"
        ), None)
        pending = next((
            row for row in reversed(publications)
            if row["publication_kind"] == "interaction_request"
        ), None)
        runtime = self.runtime_reader(invocation)
        execution_status = self._execution_status(invocation, runtime, final, pending)
        ticket = self.ticket_reader(user_id, invocation["conversation_id"])
        selected_delivery = final or pending
        return InvocationStatusView(
            invocation_key=invocation_key,
            workflow_run_id=invocation["workflow_run_id"],
            admission_status=AdmissionStatus(invocation["admission_status"]),
            execution_status=execution_status,
            runtime=runtime,
            pending_signal=(
                {
                    "publication_id": pending["publication_id"],
                    "signal_id": pending["payload"].get("signal_id"),
                    "signal_version": pending["payload"].get("signal_version"),
                    "challenge": pending["payload"].get("challenge"),
                }
                if pending is not None and final is None else None
            ),
            final_response=(
                {
                    "response_id": final["publication_id"],
                    "response": final["payload"].get("response"),
                    "outbound_event_id": final["outbound_event_id"],
                }
                if final is not None else None
            ),
            delivery_status=(
                DeliveryStatusV1(selected_delivery["status"])
                if selected_delivery is not None else None
            ),
            ticket=ticket,
        )

    def close_conversation(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        reason_code: str,
        actor: str,
        created_at: str,
    ) -> ConversationCloseResult:
        if not reason_code.strip() or not actor.strip():
            raise ValueError("close reason and actor are required")
        scope = (tenant_id, user_id, conversation_id)
        close_id = _stable_id("conversation-close", "\0".join(scope))
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                conversation = cursor.execute("""
                    SELECT next_event_seq, closed_at, deleted_at
                    FROM dialogpilot_app.conversations
                    WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                    FOR UPDATE
                """, scope).fetchone()
            if conversation is None:
                raise ConversationQueryNotFound("conversation not found")
            if conversation["deleted_at"] is not None:
                raise ConversationQueryNotFound("conversation not found")
            if conversation["closed_at"] is not None:
                return ConversationCloseResult(
                    close_id, True, conversation["closed_at"].isoformat(),
                )
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET closed_at=%s, updated_at=%s, next_event_seq=next_event_seq+1
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (created_at, created_at, *scope))
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_close_events (
                    close_id, tenant_id, user_id, conversation_id,
                    reason_code, actor, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (close_id, *scope, reason_code, actor, created_at))
            payload = {"close_id": close_id, "reason_code": reason_code}
            event_id = _stable_id("conversation-close-event", close_id)
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_events (
                    event_id, operation_key, tenant_id, user_id, conversation_id,
                    seq, event_type, payload, content_sha256, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,'CONVERSATION_CLOSED',%s,%s,%s)
            """, (
                event_id, _stable_id("conversation-close-operation", close_id),
                *scope, int(conversation["next_event_seq"]), Jsonb(payload),
                content_hash({
                    "event_type": "CONVERSATION_CLOSED", "payload": payload,
                }), created_at,
            ))
            return ConversationCloseResult(close_id, False, created_at)

    @staticmethod
    def _execution_status(invocation, runtime, final, pending):
        if final is not None:
            return ExecutionStatus.COMPLETED
        if pending is not None:
            return ExecutionStatus.WAITING
        if runtime:
            value = runtime.get("execution_status")
            if value:
                return ExecutionStatus(value)
            runtime_status = str(runtime.get("runtime_status") or "")
            if runtime_status == "RUNNING":
                return ExecutionStatus.RUNNING
            if runtime_status == "WAITING_APPROVAL":
                return ExecutionStatus.WAITING
            if runtime_status == "CANCELLED":
                return ExecutionStatus.CANCELLED
            if runtime_status in {
                "COMPLETED", "BLOCKED", "TOOL_ERROR", "MAX_STEPS",
                "EXPIRED", "UNAVAILABLE",
            }:
                return ExecutionStatus.FAILED
        return None

    @staticmethod
    def _require_scope(connection, tenant_id, user_id, conversation_id):
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute("""
                SELECT next_event_seq, deleted_at, closed_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (tenant_id, user_id, conversation_id)).fetchone()
            if row is not None:
                return row
            exists = cursor.execute(
                "SELECT 1 FROM dialogpilot_app.conversations "
                "WHERE conversation_id=%s LIMIT 1", (conversation_id,),
            ).fetchone()
        if exists:
            raise ConversationQueryAccessDenied("conversation scope denied")
        raise ConversationQueryNotFound("conversation not found")


def _stable_id(namespace: str, value: str) -> str:
    return f"{namespace}:v1:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
