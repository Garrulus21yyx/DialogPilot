"""Versioned HTTP/client projection for ChatOutcome v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from application.chat_contracts import Accepted, Cancelled, ChatOutcome, Completed, Conflict, Expired, Failed, HandedOff, NeedsInput, Reconciling, Rejected


@dataclass(frozen=True)
class HttpChatProjection:
    status_code: int
    body: Mapping[str, Any]


def project_chat_outcome(outcome: ChatOutcome) -> HttpChatProjection:
    if isinstance(outcome, Completed):
        return HttpChatProjection(200, dict(outcome.response))
    if isinstance(outcome, Accepted):
        return HttpChatProjection(202, {
            "outcome": "accepted",
            "workflow_run_id": outcome.workflow_run_id,
            "public_status": dict(outcome.public_status),
            "client_action": "poll_same_request",
            "retry_hint": "reuse_request_id",
        })
    if isinstance(outcome, NeedsInput):
        return HttpChatProjection(202, {
            "outcome": "needs_input",
            "workflow_run_id": outcome.workflow_run_id,
            "signal_id": outcome.signal_id,
            "kind": outcome.kind,
            "expires_at": outcome.expires_at,
            "interaction_publication_id": outcome.interaction_publication_id,
            "client_action": "reply_to_signal_schema",
            "retry_hint": "do_not_create_new_request",
        })
    if isinstance(outcome, Reconciling):
        return HttpChatProjection(202, {
            "outcome": "reconciling",
            "workflow_run_id": outcome.workflow_run_id,
            "public_status": dict(outcome.public_status),
            "next_poll_after": outcome.next_poll_after,
            "client_action": "poll_only",
            "retry_hint": "never_replay_write",
        })
    if isinstance(outcome, HandedOff):
        return HttpChatProjection(200, {
            "outcome": "handed_off",
            "ticket_id": outcome.ticket_id,
            "handoff_id": outcome.handoff_id,
            "client_action": "continue_with_human_support",
            "retry_hint": "do_not_restart_execution",
        })
    if isinstance(outcome, Cancelled):
        return HttpChatProjection(200, {
            "outcome": "cancelled",
            "workflow_run_id": outcome.workflow_run_id,
            "reason_code": outcome.reason_code,
            "client_action": "start_new_request_if_needed",
            "retry_hint": "do_not_retry_same_request",
        })
    if isinstance(outcome, Expired):
        return HttpChatProjection(410, {
            "outcome": "expired",
            "workflow_run_id": outcome.workflow_run_id,
            "stage": outcome.stage,
            "new_request_required": outcome.new_request_required,
            "client_action": "start_new_request",
            "retry_hint": "old_run_must_not_be_revived",
        })
    if isinstance(outcome, Conflict):
        return HttpChatProjection(409, {
            "outcome": "conflict",
            "code": outcome.code,
            "existing_invocation": dict(outcome.existing_invocation),
            "client_action": "read_existing_or_change_request_id",
            "retry_hint": "do_not_change_payload_for_same_key",
        })
    if isinstance(outcome, Rejected):
        return HttpChatProjection(422, {
            "outcome": "rejected",
            "code": outcome.code,
            "safe_message": outcome.safe_message,
            "client_action": "correct_request",
            "retry_hint": "retry_only_after_correction",
        })
    if isinstance(outcome, Failed):
        return HttpChatProjection(503 if outcome.retryable else 500, {
            "outcome": "failed",
            "code": outcome.code,
            "retryable": outcome.retryable,
            "correlation_id": outcome.correlation_id,
            "safe_message": outcome.safe_message,
            "client_action": "retry_same_request" if outcome.retryable else "contact_support",
            "retry_hint": "reuse_request_id" if outcome.retryable else "do_not_blind_retry",
            **({"response_id": outcome.response_id} if outcome.response_id else {}),
        })
    raise TypeError(f"unsupported ChatOutcome v1: {type(outcome).__name__}")
