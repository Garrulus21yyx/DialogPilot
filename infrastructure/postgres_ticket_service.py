"""PostgreSQL single owner for Handoff tickets, transitions and outbox."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Callable, Optional
import uuid

from psycopg.types.json import Jsonb

from application.case_resolution import AcceptCaseResolution, AcceptedCaseResolution
from core.auth import Principal
from infrastructure.postgres_case_resolution import accept_case_resolution
from services.ticket_service import (
    IdempotencyConflictError,
    InvalidTransitionError,
    LEGAL_TRANSITIONS,
    Ticket,
    TicketNotFoundError,
    TicketOutboxMessage,
    TicketPriority,
    TicketStatus,
)


logger = logging.getLogger(__name__)
_TICKET_COLUMNS = """
    ticket_id,idempotency_key,user_id,conversation_id,request_id,question,
    published_response,reason,priority,status,agent_type,intent,
    verification_status,assignee,created_at,updated_at,version,identity_metadata
"""


class PostgresTicketService:
    """Same domain contract as TicketService without a SQLite fallback."""

    def __init__(
        self,
        pool,
        *,
        dispatcher: Optional[Callable[[TicketOutboxMessage], None]] = None,
        dispatch_poll_seconds: float = 5.0,
        dispatch_lease_seconds: float = 30.0,
        dispatch_retry_base_seconds: float = 5.0,
        dispatch_retry_max_seconds: float = 300.0,
    ):
        self.pool = pool
        self._dispatcher = dispatcher
        self._dispatch_poll_seconds = max(0.05, float(dispatch_poll_seconds))
        self._dispatch_lease_seconds = max(1.0, float(dispatch_lease_seconds))
        self._dispatch_retry_base_seconds = max(0.05, float(dispatch_retry_base_seconds))
        self._dispatch_retry_max_seconds = max(
            self._dispatch_retry_base_seconds, float(dispatch_retry_max_seconds),
        )
        self._worker_id = uuid.uuid4().hex
        self._worker_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._dispatcher is None or self._worker_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._dispatch_loop(), name="postgres-ticket-outbox-dispatcher",
        )

    async def close(self) -> None:
        if self._worker_task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        await self._worker_task
        self._worker_task = None
        self._stop_event = None

    def create_ticket(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        conv_id: str,
        request_id: str,
        question: str,
        published_response: str,
        reason: str,
        priority: TicketPriority = TicketPriority.NORMAL,
        agent_type: str = "general",
        intent: str = "other",
        verification_status: str = "unknown",
        identity_metadata: Optional[dict[str, str]] = None,
    ) -> tuple[Ticket, bool]:
        identity = {
            str(key): str(value)
            for key, value in dict(identity_metadata or {}).items()
        }
        required = {
            "idempotency_key": idempotency_key, "user_id": user_id,
            "conv_id": conv_id, "request_id": request_id,
            "question": question, "reason": reason,
        }
        values = {key: _required(value, key) for key, value in required.items()}
        fingerprint = _fingerprint({
            **{key: values[key] for key in (
                "idempotency_key", "user_id", "conv_id", "request_id", "question",
            )},
            "identity_metadata": identity,
        })
        ticket_id, event_id = uuid.uuid4().hex, uuid.uuid4().hex
        priority = TicketPriority(priority)
        with self.pool.transaction() as connection:
            inserted = connection.execute(f"""
                INSERT INTO dialogpilot_app.handoff_tickets (
                    ticket_id,idempotency_key,request_fingerprint,user_id,
                    conversation_id,request_id,question,published_response,reason,
                    priority,status,agent_type,intent,verification_status,
                    identity_metadata
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING {_TICKET_COLUMNS}
            """, (
                ticket_id, values["idempotency_key"], fingerprint,
                values["user_id"], values["conv_id"], values["request_id"],
                values["question"], str(published_response or "").strip(),
                values["reason"], priority.value,
                str(agent_type or "general").strip(), str(intent or "other").strip(),
                str(verification_status or "unknown").strip(), Jsonb(identity),
            )).fetchone()
            if inserted is None:
                row = connection.execute(f"""
                    SELECT {_TICKET_COLUMNS},request_fingerprint
                    FROM dialogpilot_app.handoff_tickets WHERE idempotency_key=%s
                    FOR UPDATE
                """, (values["idempotency_key"],)).fetchone()
                if row[-1] != fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with different ticket content",
                    )
                return _ticket(row[:-1]), False
            connection.execute("""
                INSERT INTO dialogpilot_app.handoff_ticket_events (
                    ticket_id,from_status,to_status,actor,note
                ) VALUES (%s,NULL,'open','system','ticket created')
            """, (ticket_id,))
            payload = {
                **_ticket(inserted).to_dict(),
                "idempotency_key": values["idempotency_key"],
            }
            connection.execute("""
                INSERT INTO dialogpilot_app.handoff_ticket_outbox (
                    event_id,event_type,ticket_id,payload
                ) VALUES (%s,'ticket.created',%s,%s)
            """, (event_id, ticket_id, Jsonb(payload)))
            return _ticket(inserted), True

    def get_ticket(self, ticket_id: str) -> Ticket:
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT {_TICKET_COLUMNS} FROM dialogpilot_app.handoff_tickets
                WHERE ticket_id=%s
            """, (ticket_id,)).fetchone()
        if row is None:
            raise TicketNotFoundError(f"ticket not found: {ticket_id}")
        return _ticket(row)

    def get_ticket_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        conv_id: str,
    ) -> Ticket:
        """Resolve one exact operation without exposing another user's ticket."""
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT {_TICKET_COLUMNS} FROM dialogpilot_app.handoff_tickets
                WHERE idempotency_key=%s AND user_id=%s AND conversation_id=%s
            """, (
                _required(idempotency_key, "idempotency_key"),
                _required(user_id, "user_id"),
                _required(conv_id, "conv_id"),
            )).fetchone()
        if row is None:
            raise TicketNotFoundError("ticket operation not found")
        return _ticket(row)

    def get_ticket_view(self, ticket_id: str) -> dict[str, Any]:
        result = self.get_ticket(ticket_id).to_dict()
        result["events"] = self.get_events(ticket_id)
        return result

    def list_tickets(
        self, *, user_id: str | None = None,
        status: TicketStatus | None = None, limit: int = 50,
    ) -> list[Ticket]:
        clauses, params = [], []
        if user_id:
            clauses.append("user_id=%s")
            params.append(user_id)
        if status is not None:
            clauses.append("status=%s")
            params.append(TicketStatus(status).value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        with self.pool.transaction() as connection:
            rows = connection.execute(f"""
                SELECT {_TICKET_COLUMNS} FROM dialogpilot_app.handoff_tickets
                {where} ORDER BY created_at DESC,ticket_id LIMIT %s
            """, tuple(params)).fetchall()
        return [_ticket(row) for row in rows]

    def list_active_tickets(self, *, user_id: str, limit: int = 3) -> list[Ticket]:
        with self.pool.transaction() as connection:
            rows = connection.execute(f"""
                SELECT {_TICKET_COLUMNS} FROM dialogpilot_app.handoff_tickets
                WHERE user_id=%s AND status!='closed'
                ORDER BY updated_at DESC,created_at DESC,ticket_id LIMIT %s
            """, (_required(user_id, "user_id"), max(1, min(int(limit), 20)))).fetchall()
        return [_ticket(row) for row in rows]

    def accept_resolution(
        self,
        ticket_id: str,
        command: AcceptCaseResolution,
        *,
        principal: Principal,
    ) -> tuple[AcceptedCaseResolution, bool]:
        return accept_case_resolution(
            self.pool,
            ticket_id,
            command,
            principal=principal,
        )

    def transition(
        self, ticket_id: str, target: TicketStatus, *, actor: str,
        note: str = "", assignee: str | None = None,
    ) -> Ticket:
        target, actor = TicketStatus(target), _required(actor, "actor")
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT {_TICKET_COLUMNS} FROM dialogpilot_app.handoff_tickets
                WHERE ticket_id=%s FOR UPDATE
            """, (ticket_id,)).fetchone()
            if row is None:
                raise TicketNotFoundError(f"ticket not found: {ticket_id}")
            current = TicketStatus(row[9])
            if target is current:
                return _ticket(row)
            if target not in LEGAL_TRANSITIONS[current]:
                raise InvalidTransitionError(current, target)
            next_assignee = str(assignee or "").strip() or row[13]
            if target is TicketStatus.IN_PROGRESS and not next_assignee:
                next_assignee = actor
            updated = connection.execute(f"""
                UPDATE dialogpilot_app.handoff_tickets
                SET status=%s,assignee=%s,version=version+1,
                    updated_at=transaction_timestamp()
                WHERE ticket_id=%s RETURNING {_TICKET_COLUMNS}
            """, (target.value, next_assignee, ticket_id)).fetchone()
            connection.execute("""
                INSERT INTO dialogpilot_app.handoff_ticket_events (
                    ticket_id,from_status,to_status,actor,note
                ) VALUES (%s,%s,%s,%s,%s)
            """, (ticket_id, current.value, target.value, actor, str(note or "")[:1000]))
        return _ticket(updated)

    def get_events(self, ticket_id: str) -> list[dict[str, Any]]:
        self.get_ticket(ticket_id)
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT event_id,from_status,to_status,actor,note,created_at
                FROM dialogpilot_app.handoff_ticket_events
                WHERE ticket_id=%s ORDER BY event_id
            """, (ticket_id,)).fetchall()
        return [{
            "event_id": row[0], "from_status": row[1], "to_status": row[2],
            "actor": row[3], "note": row[4], "created_at": row[5].isoformat(),
        } for row in rows]

    def outbox_stats(self) -> dict[str, Any]:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT count(*) FILTER (WHERE delivered_at IS NULL),
                       count(*) FILTER (WHERE delivered_at IS NOT NULL),
                       count(*) FILTER (
                           WHERE delivered_at IS NULL
                             AND available_at<=transaction_timestamp()
                       )
                FROM dialogpilot_app.handoff_ticket_outbox
            """).fetchone()
        return {
            "dispatcher_configured": self._dispatcher is not None,
            "worker_running": self._worker_task is not None and not self._worker_task.done(),
            "pending": int(row[0]), "delivered": int(row[1]), "due": int(row[2]),
        }

    def dispatch_once(self) -> bool:
        if self._dispatcher is None:
            return False
        message = self._claim_outbox_message()
        if message is None:
            return False
        try:
            self._dispatcher(message)
        except Exception as exc:
            self._finish_outbox(message, error=exc)
            logger.warning("handoff outbox delivery failed event_id=%s", message.event_id)
        else:
            self._finish_outbox(message)
        return True

    def _claim_outbox_message(self) -> TicketOutboxMessage | None:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT event_id,event_type,ticket_id,payload,attempts,created_at
                FROM dialogpilot_app.handoff_ticket_outbox
                WHERE delivered_at IS NULL AND available_at<=transaction_timestamp()
                  AND (lease_until IS NULL OR lease_until<=transaction_timestamp())
                ORDER BY created_at,event_id FOR UPDATE SKIP LOCKED LIMIT 1
            """).fetchone()
            if row is None:
                return None
            attempt = int(row[4]) + 1
            connection.execute("""
                UPDATE dialogpilot_app.handoff_ticket_outbox
                SET claimed_by=%s,attempts=%s,
                    lease_until=transaction_timestamp()+(%s*interval '1 second')
                WHERE event_id=%s
            """, (self._worker_id, attempt, self._dispatch_lease_seconds, row[0]))
        return TicketOutboxMessage(
            row[0], row[1], row[2], dict(row[3]), attempt, row[5].isoformat(),
        )

    def _finish_outbox(
        self, message: TicketOutboxMessage, *, error: Exception | None = None,
    ) -> None:
        with self.pool.transaction() as connection:
            if error is None:
                connection.execute("""
                    UPDATE dialogpilot_app.handoff_ticket_outbox
                    SET delivered_at=transaction_timestamp(),claimed_by=NULL,
                        lease_until=NULL,last_error=NULL
                    WHERE event_id=%s AND claimed_by=%s
                """, (message.event_id, self._worker_id))
                return
            delay = min(
                self._dispatch_retry_max_seconds,
                self._dispatch_retry_base_seconds * 2 ** max(0, message.attempts - 1),
            )
            connection.execute("""
                UPDATE dialogpilot_app.handoff_ticket_outbox
                SET available_at=transaction_timestamp()+(%s*interval '1 second'),
                    claimed_by=NULL,lease_until=NULL,last_error=%s
                WHERE event_id=%s AND claimed_by=%s
            """, (
                delay, f"{type(error).__name__}: {str(error)[:500]}",
                message.event_id, self._worker_id,
            ))

    async def _dispatch_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            handled = await asyncio.to_thread(self.dispatch_once)
            if handled:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._dispatch_poll_seconds,
                )
            except TimeoutError:
                pass


def _ticket(row) -> Ticket:
    return Ticket(
        ticket_id=row[0], idempotency_key=row[1], user_id=row[2], conv_id=row[3],
        request_id=row[4], question=row[5], published_response=row[6], reason=row[7],
        priority=TicketPriority(row[8]), status=TicketStatus(row[9]),
        agent_type=row[10], intent=row[11], verification_status=row[12],
        assignee=row[13], created_at=row[14].isoformat(), updated_at=row[15].isoformat(),
        version=int(row[16]), identity_metadata=dict(row[17]),
    )


def _required(value: str, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _fingerprint(values: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
