"""Atomic PostgreSQL write boundary for an accepted ticket resolution."""

from __future__ import annotations

import hashlib
import json

from psycopg.types.json import Jsonb

from application.case_resolution import (
    CASE_RESOLUTION_ACCEPTED,
    AcceptCaseResolution,
    AcceptedCaseResolution,
    CaseOwnerMismatch,
    CaseResolutionConflict,
    CaseResolutionSourceUnavailable,
)
from application.conversation_store import content_hash
from application.data_location_registry import DataSubjectRef
from core.auth import Principal
from services.ticket_service import (
    InvalidTransitionError,
    LEGAL_TRANSITIONS,
    TicketNotFoundError,
    TicketStatus,
)


def accept_case_resolution(
    pool,
    ticket_id: str,
    command: AcceptCaseResolution,
    *,
    principal: Principal,
) -> tuple[AcceptedCaseResolution, bool]:
    """Accept one resolution and resolve its ticket in the same transaction."""
    ticket_id = _required(ticket_id, "ticket_id")
    if not isinstance(principal, Principal):
        raise TypeError("authenticated principal is required")
    case_owner = _required(principal.subject, "case_owner")

    with pool.transaction() as connection:
        ticket = connection.execute("""
            SELECT user_id,conversation_id,request_id,question,status,
                   assignee,version,identity_metadata
            FROM dialogpilot_app.handoff_tickets
            WHERE ticket_id=%s FOR UPDATE
        """, (ticket_id,)).fetchone()
        if ticket is None:
            raise TicketNotFoundError(f"ticket not found: {ticket_id}")
        if str(ticket[5] or "") != case_owner:
            raise CaseOwnerMismatch("authenticated principal is not the case owner")

        user_id = str(ticket[0])
        conversation_id = str(ticket[1])
        request_id = str(ticket[2])
        metadata = dict(ticket[7] or {})
        tenant_id = _required(metadata.get("tenant_id"), "ticket tenant_id")
        source_invocation_key = str(metadata.get("invocation_key") or "").strip()
        if not source_invocation_key:
            raise CaseResolutionSourceUnavailable(
                "ticket has no trusted source invocation"
            )
        expected_scope = (tenant_id, user_id, conversation_id, request_id)
        metadata_scope = (
            str(metadata.get("tenant_id") or ""),
            str(metadata.get("user_id") or ""),
            str(metadata.get("conversation_id") or ""),
            str(metadata.get("request_id") or ""),
        )
        invocation = connection.execute("""
            SELECT tenant_id,user_id,conversation_id,request_id
            FROM dialogpilot_app.workflow_invocations
            WHERE invocation_key=%s
        """, (source_invocation_key,)).fetchone()
        if metadata_scope != expected_scope or invocation != expected_scope:
            raise CaseResolutionSourceUnavailable(
                "ticket source invocation is outside its conversation scope"
            )
        request_fingerprint = _hash({
            "ticket_id": ticket_id,
            "tenant_id": tenant_id,
            "case_owner": case_owner,
            "command_fingerprint": command.fingerprint,
        })
        operation_key = _stable(
            "case-resolution-operation", tenant_id, ticket_id,
            command.idempotency_key,
        )
        verification_ref = _stable(
            "case-resolution", tenant_id, ticket_id, command.idempotency_key,
        )
        existing = connection.execute("""
            SELECT event_id,event_type,payload
            FROM dialogpilot_app.conversation_events
            WHERE operation_key=%s
        """, (operation_key,)).fetchone()
        if existing is not None:
            if existing[1] != CASE_RESOLUTION_ACCEPTED:
                raise CaseResolutionConflict(
                    "case resolution operation key belongs to another fact"
                )
            accepted = AcceptedCaseResolution.from_event(
                str(existing[0]), dict(existing[2]),
            )
            if accepted.request_fingerprint != request_fingerprint:
                raise CaseResolutionConflict(
                    "case resolution idempotency key was reused"
                )
            return accepted, True

        current = TicketStatus(str(ticket[4]))
        if TicketStatus.RESOLVED not in LEGAL_TRANSITIONS[current]:
            raise InvalidTransitionError(current, TicketStatus.RESOLVED)
        if int(ticket[6]) != command.expected_ticket_version:
            raise CaseResolutionConflict("ticket version changed")

        conversation = connection.execute("""
            SELECT next_event_seq,deletion_epoch,deleted_at
            FROM dialogpilot_app.conversations
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            FOR UPDATE
        """, expected_scope[:3]).fetchone()
        if conversation is None or conversation[2] is not None:
            raise CaseResolutionSourceUnavailable("ticket conversation is unavailable")
        source_turns = connection.execute("""
            SELECT turn_key
            FROM dialogpilot_app.conversation_turns
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
              AND invocation_key=%s
              AND role IN ('inbound','assistant','human')
            ORDER BY seq
        """, (*expected_scope[:3], source_invocation_key)).fetchall()
        source_turn_refs = tuple(str(row[0]) for row in source_turns)
        if not source_turn_refs:
            raise CaseResolutionSourceUnavailable("ticket source turns are unavailable")

        prior = int(connection.execute("""
            SELECT count(*)
            FROM dialogpilot_app.conversation_events
            WHERE event_type=%s AND payload->>'ticket_id'=%s
        """, (CASE_RESOLUTION_ACCEPTED, ticket_id)).fetchone()[0])
        accepted_at = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
        accepted = AcceptedCaseResolution(
            verification_ref=verification_ref,
            ticket_id=ticket_id,
            revision=prior + 1,
            subject=DataSubjectRef(tenant_id, user_id, conversation_id),
            source_deletion_epoch=int(conversation[1]),
            source_invocation_key=source_invocation_key,
            source_turn_refs=source_turn_refs,
            problem=str(ticket[3]),
            resolution=command.resolution,
            authoritative_outcomes=command.authoritative_outcomes,
            case_owner=case_owner,
            accepted_at=accepted_at.isoformat(),
            request_fingerprint=request_fingerprint,
        )
        payload = accepted.to_event_payload()
        connection.execute("""
            INSERT INTO dialogpilot_app.conversation_events (
                event_id,operation_key,tenant_id,user_id,conversation_id,
                seq,event_type,payload,content_sha256,request_id,
                invocation_key,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
                verification_ref,
                operation_key,
                *expected_scope[:3],
                int(conversation[0]),
                CASE_RESOLUTION_ACCEPTED,
                Jsonb(payload),
                content_hash({
                    "event_type": CASE_RESOLUTION_ACCEPTED,
                    "payload": payload,
                }),
                request_id,
                source_invocation_key,
                accepted_at,
        ))
        connection.execute("""
            UPDATE dialogpilot_app.conversations
            SET next_event_seq=next_event_seq+1,updated_at=%s
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (accepted_at, *expected_scope[:3]))
        updated = connection.execute("""
            UPDATE dialogpilot_app.handoff_tickets
            SET status='resolved',version=version+1,
                updated_at=%s
            WHERE ticket_id=%s AND version=%s AND status=%s
        """, (
                accepted_at,
                ticket_id,
                command.expected_ticket_version,
                current.value,
        ))
        if updated.rowcount != 1:
            raise CaseResolutionConflict("ticket resolution CAS failed")
        connection.execute("""
            INSERT INTO dialogpilot_app.handoff_ticket_events (
                ticket_id,from_status,to_status,actor,note
            ) VALUES (%s,%s,'resolved',%s,%s)
        """, (
                ticket_id,
                current.value,
                case_owner,
                f"accepted resolution {verification_ref}",
        ))
    return accepted, False


def _required(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _stable(namespace: str, *parts: object) -> str:
    return f"{namespace}:v1:{_hash({'parts': list(map(str, parts))})}"


def _hash(value: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
