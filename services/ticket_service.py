"""Persistent human-handoff tickets with a closed state-transition contract."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


LEGAL_TRANSITIONS = {
    TicketStatus.OPEN: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
    TicketStatus.IN_PROGRESS: {
        TicketStatus.WAITING_CUSTOMER,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    },
    TicketStatus.WAITING_CUSTOMER: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    },
    TicketStatus.RESOLVED: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
    TicketStatus.CLOSED: set(),
}


class TicketError(Exception):
    """Base exception for typed ticket failures."""


class TicketNotFoundError(TicketError):
    pass


class InvalidTransitionError(TicketError):
    def __init__(self, current: TicketStatus, target: TicketStatus):
        super().__init__(f"illegal ticket transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


class IdempotencyConflictError(TicketError):
    pass


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    idempotency_key: str
    user_id: str
    conv_id: str
    request_id: str
    question: str
    published_response: str
    reason: str
    priority: TicketPriority
    status: TicketStatus
    agent_type: str
    intent: str
    verification_status: str
    assignee: Optional[str]
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        data["status"] = self.status.value
        return data


class TicketService:
    """Authoritative owner of ticket persistence, idempotency, and lifecycle."""

    def __init__(self, database_path: str):
        self._path = Path(database_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

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
    ) -> Tuple[Ticket, bool]:
        values = {
            "idempotency_key": self._required(idempotency_key, "idempotency_key"),
            "user_id": self._required(user_id, "user_id"),
            "conv_id": self._required(conv_id, "conv_id"),
            "request_id": self._required(request_id, "request_id"),
            "question": self._required(question, "question"),
            "published_response": (published_response or "").strip(),
            "reason": self._required(reason, "reason"),
            "priority": TicketPriority(priority),
            "agent_type": (agent_type or "general").strip(),
            "intent": (intent or "other").strip(),
            "verification_status": (verification_status or "unknown").strip(),
        }
        # Idempotency identifies the client operation, not nondeterministic LLM
        # output. A safe retry may produce different wording but must still
        # resolve to the first persisted handoff ticket.
        fingerprint = self._fingerprint(
            {
                "idempotency_key": values["idempotency_key"],
                "user_id": values["user_id"],
                "conv_id": values["conv_id"],
                "request_id": values["request_id"],
                "question": values["question"],
            }
        )
        now = self._now()
        ticket_id = uuid.uuid4().hex

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM tickets WHERE idempotency_key = ?",
                (values["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with different ticket content"
                    )
                return self._row_to_ticket(existing), False

            conn.execute(
                """
                INSERT INTO tickets (
                    ticket_id, idempotency_key, request_fingerprint, user_id,
                    conv_id, request_id, question, published_response, reason,
                    priority, status, agent_type, intent, verification_status,
                    assignee, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    ticket_id,
                    values["idempotency_key"],
                    fingerprint,
                    values["user_id"],
                    values["conv_id"],
                    values["request_id"],
                    values["question"],
                    values["published_response"],
                    values["reason"],
                    values["priority"].value,
                    TicketStatus.OPEN.value,
                    values["agent_type"],
                    values["intent"],
                    values["verification_status"],
                    now,
                    now,
                ),
            )
            self._insert_event(
                conn,
                ticket_id=ticket_id,
                from_status=None,
                to_status=TicketStatus.OPEN,
                actor="system",
                note="ticket created",
                created_at=now,
            )
            row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
            return self._row_to_ticket(row), True

    def get_ticket(self, ticket_id: str) -> Ticket:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
        if row is None:
            raise TicketNotFoundError(f"ticket not found: {ticket_id}")
        return self._row_to_ticket(row)

    def get_ticket_view(self, ticket_id: str) -> Dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        data = ticket.to_dict()
        data["events"] = self.get_events(ticket_id)
        return data

    def list_tickets(
        self,
        *,
        user_id: Optional[str] = None,
        status: Optional[TicketStatus] = None,
        limit: int = 50,
    ) -> List[Ticket]:
        limit = max(1, min(int(limit), 200))
        clauses: List[str] = []
        params: List[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(TicketStatus(status).value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tickets{where} ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_ticket(row) for row in rows]

    def transition(
        self,
        ticket_id: str,
        target: TicketStatus,
        *,
        actor: str,
        note: str = "",
        assignee: Optional[str] = None,
    ) -> Ticket:
        target = TicketStatus(target)
        actor = self._required(actor, "actor")
        note = (note or "").strip()[:1000]
        assignee = (assignee or "").strip() or None

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise TicketNotFoundError(f"ticket not found: {ticket_id}")
            current = TicketStatus(row["status"])
            if target is current:
                return self._row_to_ticket(row)
            if target not in LEGAL_TRANSITIONS[current]:
                raise InvalidTransitionError(current, target)

            now = self._now()
            next_assignee = assignee or row["assignee"]
            if target is TicketStatus.IN_PROGRESS and not next_assignee:
                next_assignee = actor
            conn.execute(
                "UPDATE tickets SET status = ?, assignee = ?, updated_at = ? WHERE ticket_id = ?",
                (target.value, next_assignee, now, ticket_id),
            )
            self._insert_event(
                conn,
                ticket_id=ticket_id,
                from_status=current,
                to_status=target,
                actor=actor,
                note=note,
                created_at=now,
            )
            updated = conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            return self._row_to_ticket(updated)

    def get_events(self, ticket_id: str) -> List[Dict[str, Any]]:
        # Verify the parent exists so an empty event list is not confused with a missing ticket.
        self.get_ticket(ticket_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, from_status, to_status, actor, note, created_at
                FROM ticket_events WHERE ticket_id = ? ORDER BY event_id ASC
                """,
                (ticket_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    published_response TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    assignee TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tickets_user_created
                    ON tickets(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tickets_status_created
                    ON tickets(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS ticket_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        *,
        ticket_id: str,
        from_status: Optional[TicketStatus],
        to_status: TicketStatus,
        actor: str,
        note: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO ticket_events (
                ticket_id, from_status, to_status, actor, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                from_status.value if from_status else None,
                to_status.value,
                actor,
                note,
                created_at,
            ),
        )

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> Ticket:
        return Ticket(
            ticket_id=row["ticket_id"],
            idempotency_key=row["idempotency_key"],
            user_id=row["user_id"],
            conv_id=row["conv_id"],
            request_id=row["request_id"],
            question=row["question"],
            published_response=row["published_response"],
            reason=row["reason"],
            priority=TicketPriority(row["priority"]),
            status=TicketStatus(row["status"]),
            agent_type=row["agent_type"],
            intent=row["intent"],
            verification_status=row["verification_status"],
            assignee=row["assignee"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _fingerprint(values: Dict[str, Any]) -> str:
        serializable = {
            key: value.value if isinstance(value, Enum) else value
            for key, value in values.items()
        }
        raw = json.dumps(serializable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _required(value: str, field: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError(f"{field} must not be blank")
        return cleaned

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
