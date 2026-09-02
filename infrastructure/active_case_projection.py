"""TicketService adapter for typed ActiveCase read projections."""
from __future__ import annotations

import hashlib
import json

from application.active_case import (
    ActiveCase,
    ActiveCaseProjection,
    ActiveCaseState,
    opaque_refs,
)


class TicketServiceActiveCaseReader:
    def __init__(self, ticket_service):
        self.ticket_service = ticket_service

    def read(self, *, user_id: str, limit: int = 20) -> ActiveCaseProjection:
        try:
            tickets = self.ticket_service.list_active_tickets(
                user_id=user_id, limit=limit,
            )
        except Exception as exc:
            return ActiveCaseProjection(
                ActiveCaseState.UNAVAILABLE,
                reason_codes=(f"TICKET_SERVICE_{type(exc).__name__.upper()}",),
            )
        cases = tuple(self._case(ticket) for ticket in tickets)
        identities = [case.case_id for case in cases]
        if len(identities) != len(set(identities)):
            return ActiveCaseProjection(
                ActiveCaseState.CONFLICT,
                reason_codes=("DUPLICATE_CASE_ID",),
            )
        return ActiveCaseProjection(
            ActiveCaseState.CASES if cases else ActiveCaseState.NO_ACTIVE_CASE,
            cases,
        )

    @staticmethod
    def _case(ticket) -> ActiveCase:
        case_id = "case:ticket:v1:" + hashlib.sha256(
            json.dumps(
                ["ticket-case-v1", ticket.ticket_id], separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        tags = tuple(dict.fromkeys(filter(None, (
            str(ticket.intent or ""), str(ticket.agent_type or ""),
        ))))
        return ActiveCase(
            case_id=case_id, ticket_id=ticket.ticket_id,
            issue_summary=ticket.question,
            intent_or_topic_tags=tags,
            product_order_entity_refs=opaque_refs(ticket.identity_metadata),
            status=ticket.status.value,
            business_priority=ticket.priority.value,
            sla_and_commitment_refs=(), assignee=ticket.assignee or "",
            created_at=ticket.created_at, updated_at=ticket.updated_at,
            version=ticket.version,
        )
