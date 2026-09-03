"""HTTP boundary for authenticated case-owner resolution acceptance."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel, Field

from application.case_resolution import (
    AcceptCaseResolution,
    CaseOwnerMismatch,
    CaseResolutionConflict,
    CaseResolutionSourceUnavailable,
)
from core.auth import Principal
from services.ticket_service import (
    InvalidTransitionError,
    TicketNotFoundError,
    TicketService,
)


class TicketResolutionAcceptRequest(BaseModel):
    """Owner-provided, version-bound resolution fact."""

    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_ticket_version: int = Field(ge=1)
    resolution: str = Field(min_length=1, max_length=10000)
    authoritative_outcomes: list[str] = Field(min_length=1, max_length=20)


async def accept_ticket_resolution_request(
    ticket_service: TicketService | None,
    ticket_id: str,
    body: TicketResolutionAcceptRequest,
    *,
    principal: Principal,
) -> dict[str, object]:
    if ticket_service is None:
        raise HTTPException(503, "工单服务未就绪")
    try:
        command = AcceptCaseResolution(
            idempotency_key=body.idempotency_key,
            expected_ticket_version=body.expected_ticket_version,
            resolution=body.resolution,
            authoritative_outcomes=tuple(body.authoritative_outcomes),
        )
        accepted, replayed = await asyncio.to_thread(
            ticket_service.accept_resolution,
            ticket_id,
            command,
            principal=principal,
        )
    except TicketNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CaseOwnerMismatch as exc:
        raise HTTPException(403, str(exc)) from exc
    except (
        CaseResolutionConflict,
        CaseResolutionSourceUnavailable,
        InvalidTransitionError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "replayed": replayed,
        "accepted_resolution": {
            "verification_ref": accepted.verification_ref,
            **accepted.to_event_payload(),
        },
    }
