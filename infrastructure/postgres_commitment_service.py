"""PostgreSQL single owner for explicit customer commitments."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from services.commitment_service import (
    Commitment,
    CommitmentIdempotencyConflictError,
    CommitmentNotFoundError,
    CommitmentStatus,
    CommitmentVersionConflictError,
    InvalidCommitmentTransitionError,
    LEGAL_COMMITMENT_TRANSITIONS,
)


_COLUMNS = """
    commitment_id,idempotency_key,user_id,conversation_id,ticket_id,kind,
    description,due_at,owner,source_kind,source_receipt_ref,status,version,
    retention_class,breached_at,escalated_at,fulfilled_at,archived_at,
    created_at,updated_at
"""
_FULFILLED = {CommitmentStatus.FULFILLED, CommitmentStatus.LATE_FULFILLED}


class PostgresCommitmentService:
    """Own commitment identity, state, due transitions and versioned receipts."""

    def __init__(self, pool, *, due_poll_seconds: float = 30.0):
        self.pool = pool
        self._due_poll_seconds = max(0.1, float(due_poll_seconds))
        self._worker_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._due_loop(), name="commitment-due-worker",
        )

    async def close(self) -> None:
        if self._worker_task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        await self._worker_task
        self._worker_task = None
        self._stop_event = None

    def create(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        conversation_id: str,
        kind: str,
        description: str,
        due_at: str | datetime,
        owner: str,
        source_kind: str,
        source_receipt_ref: str | None = None,
        ticket_id: str | None = None,
        retention_class: str = "support_standard",
    ) -> tuple[Commitment, bool]:
        source_kind = _required(source_kind, "source_kind")
        if source_kind not in {"manual", "business_action"}:
            raise ValueError("source_kind must be manual or business_action")
        receipt = str(source_receipt_ref or "").strip() or None
        if source_kind == "business_action" and receipt is None:
            raise ValueError("business_action commitment requires source_receipt_ref")
        due = _timestamp(due_at, "due_at")
        values = {
            "idempotency_key": _required(idempotency_key, "idempotency_key"),
            "user_id": _required(user_id, "user_id"),
            "conversation_id": _required(conversation_id, "conversation_id"),
            "ticket_id": str(ticket_id or "").strip() or None,
            "kind": _required(kind, "kind"),
            "description": _required(description, "description"),
            "due_at": due.isoformat(),
            "owner": _required(owner, "owner"),
            "source_kind": source_kind,
            "source_receipt_ref": receipt,
            "retention_class": _required(retention_class, "retention_class"),
        }
        fingerprint = _fingerprint(values)
        commitment_id = uuid.uuid4().hex
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                INSERT INTO dialogpilot_app.commitments (
                    commitment_id,idempotency_key,request_fingerprint,user_id,
                    conversation_id,ticket_id,kind,description,due_at,owner,
                    source_kind,source_receipt_ref,retention_class
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(idempotency_key) DO NOTHING
                RETURNING {_COLUMNS}
            """, (
                commitment_id, values["idempotency_key"], fingerprint,
                values["user_id"], values["conversation_id"], values["ticket_id"],
                values["kind"], values["description"], due, values["owner"],
                source_kind, receipt, values["retention_class"],
            )).fetchone()
            if row is None:
                existing = connection.execute(f"""
                    SELECT {_COLUMNS},request_fingerprint
                    FROM dialogpilot_app.commitments WHERE idempotency_key=%s
                    FOR UPDATE
                """, (values["idempotency_key"],)).fetchone()
                if existing[-1] != fingerprint:
                    raise CommitmentIdempotencyConflictError(
                        "idempotency key was used with different commitment content"
                    )
                return _commitment(existing[:-1]), False
            connection.execute("""
                INSERT INTO dialogpilot_app.commitment_events (
                    commitment_id,version,from_status,to_status,actor,receipt_ref,note
                ) VALUES (%s,1,NULL,'scheduled',%s,%s,'commitment created')
            """, (commitment_id, values["owner"], receipt))
        return _commitment(row), True

    def get(self, commitment_id: str) -> Commitment:
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT {_COLUMNS} FROM dialogpilot_app.commitments
                WHERE commitment_id=%s
            """, (commitment_id,)).fetchone()
        if row is None:
            raise CommitmentNotFoundError(f"commitment not found: {commitment_id}")
        return _commitment(row)

    def list_for_user(
        self, *, user_id: str, active_only: bool = False, limit: int = 50,
    ) -> list[Commitment]:
        clause = " AND status!='archived'" if active_only else ""
        with self.pool.transaction() as connection:
            rows = connection.execute(f"""
                SELECT {_COLUMNS} FROM dialogpilot_app.commitments
                WHERE user_id=%s{clause}
                ORDER BY due_at,commitment_id LIMIT %s
            """, (_required(user_id, "user_id"), max(1, min(int(limit), 200)))).fetchall()
        return [_commitment(row) for row in rows]

    def transition(
        self,
        commitment_id: str,
        target: CommitmentStatus,
        *,
        expected_version: int,
        actor: str,
        receipt_ref: str | None = None,
        note: str = "",
    ) -> Commitment:
        target = CommitmentStatus(target)
        actor = _required(actor, "actor")
        receipt = str(receipt_ref or "").strip() or None
        if target in _FULFILLED and receipt is None:
            raise ValueError("fulfillment requires receipt_ref")
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT {_COLUMNS} FROM dialogpilot_app.commitments
                WHERE commitment_id=%s FOR UPDATE
            """, (commitment_id,)).fetchone()
            if row is None:
                raise CommitmentNotFoundError(f"commitment not found: {commitment_id}")
            current = CommitmentStatus(row[11])
            version = int(row[12])
            if version != int(expected_version):
                raise CommitmentVersionConflictError(
                    f"expected version {expected_version}, current version {version}"
                )
            if target not in LEGAL_COMMITMENT_TRANSITIONS[current]:
                raise InvalidCommitmentTransitionError(current, target)
            next_version = version + 1
            changed_at = datetime.now(timezone.utc)
            updates = {
                "breached": "breached_at=COALESCE(breached_at,%s)",
                "escalated": "escalated_at=COALESCE(escalated_at,%s)",
                "fulfilled": "fulfilled_at=COALESCE(fulfilled_at,%s)",
                "late_fulfilled": "fulfilled_at=COALESCE(fulfilled_at,%s)",
                "archived": "archived_at=COALESCE(archived_at,%s)",
            }
            timestamp_update = updates.get(target.value)
            extra = f",{timestamp_update}" if timestamp_update else ""
            params: list[Any] = [target.value, next_version]
            if timestamp_update:
                params.append(changed_at)
            params.append(commitment_id)
            updated = connection.execute(f"""
                UPDATE dialogpilot_app.commitments
                SET status=%s,version=%s,updated_at=transaction_timestamp(){extra}
                WHERE commitment_id=%s RETURNING {_COLUMNS}
            """, tuple(params)).fetchone()
            connection.execute("""
                INSERT INTO dialogpilot_app.commitment_events (
                    commitment_id,version,from_status,to_status,actor,receipt_ref,note
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (
                commitment_id, next_version, current.value, target.value,
                actor, receipt, str(note or "")[:1000],
            ))
        return _commitment(updated)

    def breach_due(self, *, now: str | datetime | None = None, limit: int = 100) -> list[Commitment]:
        due_before = _timestamp(now or datetime.now(timezone.utc), "now")
        changed: list[Commitment] = []
        with self.pool.transaction() as connection:
            rows = connection.execute(f"""
                SELECT {_COLUMNS} FROM dialogpilot_app.commitments
                WHERE status='scheduled' AND due_at<=%s
                ORDER BY due_at,commitment_id FOR UPDATE SKIP LOCKED LIMIT %s
            """, (due_before, max(1, min(int(limit), 1000)))).fetchall()
            for row in rows:
                next_version = int(row[12]) + 1
                updated = connection.execute(f"""
                    UPDATE dialogpilot_app.commitments
                    SET status='breached',version=%s,breached_at=%s,
                        updated_at=transaction_timestamp()
                    WHERE commitment_id=%s RETURNING {_COLUMNS}
                """, (next_version, due_before, row[0])).fetchone()
                connection.execute("""
                    INSERT INTO dialogpilot_app.commitment_events (
                        commitment_id,version,from_status,to_status,actor,note
                    ) VALUES (%s,%s,'scheduled','breached','commitment-due-worker',
                              'due_at elapsed')
                """, (row[0], next_version))
                changed.append(_commitment(updated))
        return changed

    def breached_refs(self, *, user_id: str) -> tuple[str, ...]:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT commitment_id,version FROM dialogpilot_app.commitments
                WHERE user_id=%s AND status IN ('breached','escalated')
                ORDER BY due_at,commitment_id
            """, (_required(user_id, "user_id"),)).fetchall()
        return tuple(f"breached:commitment:{row[0]}:v{row[1]}" for row in rows)

    def events(self, commitment_id: str) -> list[dict[str, Any]]:
        self.get(commitment_id)
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT version,from_status,to_status,actor,receipt_ref,note,created_at
                FROM dialogpilot_app.commitment_events
                WHERE commitment_id=%s ORDER BY version
            """, (commitment_id,)).fetchall()
        return [{
            "version": int(row[0]), "from_status": row[1], "to_status": row[2],
            "actor": row[3], "receipt_ref": row[4], "note": row[5],
            "created_at": row[6].isoformat(),
        } for row in rows]

    async def _due_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            await asyncio.to_thread(self.breach_due)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._due_poll_seconds,
                )
            except TimeoutError:
                pass


def _commitment(row) -> Commitment:
    def iso(value):
        return value.isoformat() if value is not None else None

    return Commitment(
        commitment_id=row[0], idempotency_key=row[1], user_id=row[2],
        conversation_id=row[3], ticket_id=row[4], kind=row[5], description=row[6],
        due_at=iso(row[7]), owner=row[8], source_kind=row[9],
        source_receipt_ref=row[10], status=CommitmentStatus(row[11]),
        version=int(row[12]), retention_class=row[13], breached_at=iso(row[14]),
        escalated_at=iso(row[15]), fulfilled_at=iso(row[16]), archived_at=iso(row[17]),
        created_at=iso(row[18]), updated_at=iso(row[19]),
    )


def _required(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _timestamp(value: str | datetime, field: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if result.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return result.astimezone(timezone.utc)


def _fingerprint(values: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
