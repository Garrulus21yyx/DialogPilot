"""Canonical ResponseDelivery lifecycle and connector capability algebra."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeliveryContractError(ValueError):
    pass


class InvalidDeliveryTransition(DeliveryContractError):
    pass


class DeliveryStatusV1(str, Enum):
    SELECTED = "SELECTED"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    DELIVERY_UNCERTAIN = "DELIVERY_UNCERTAIN"
    FAILED = "FAILED"
    READ = "READ"


class ConnectorCapability(str, Enum):
    IDEMPOTENT_SEND = "IDEMPOTENT_SEND"
    QUERY_RECEIPT = "QUERY_RECEIPT"
    NONE = "NONE"


class DeliveryEvent(str, Enum):
    SEND_STARTED = "SEND_STARTED"
    SEND_CONFIRMED = "SEND_CONFIRMED"
    SEND_DISCONNECTED = "SEND_DISCONNECTED"
    RECEIPT_DELIVERED = "RECEIPT_DELIVERED"
    RECEIPT_NOT_DELIVERED = "RECEIPT_NOT_DELIVERED"
    RECEIPT_UNKNOWN = "RECEIPT_UNKNOWN"
    READ_ACK = "READ_ACK"
    RETRY_DUE = "RETRY_DUE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    RECONCILE_DEADLINE = "RECONCILE_DEADLINE"
    DETERMINISTIC_FAILURE = "DETERMINISTIC_FAILURE"


@dataclass(frozen=True)
class DeliveryState:
    status: DeliveryStatusV1
    attempt: int
    max_attempts: int
    connector_capability: ConnectorCapability
    retry_policy_version: str
    reconcile_deadline: str

    def __post_init__(self) -> None:
        if self.attempt < 0 or self.max_attempts < 1 or self.attempt > self.max_attempts:
            raise DeliveryContractError("invalid delivery attempt bounds")
        if not self.retry_policy_version.strip() or not self.reconcile_deadline.strip():
            raise DeliveryContractError("retry policy and reconcile deadline are required")


@dataclass(frozen=True)
class DeliveryTransition:
    state: DeliveryState
    reason_code: str
    resend_allowed: bool = False


def transition_delivery(
    state: DeliveryState,
    event: DeliveryEvent,
) -> DeliveryTransition:
    status = state.status

    if event is DeliveryEvent.READ_ACK:
        if status in {
            DeliveryStatusV1.DELIVERING,
            DeliveryStatusV1.DELIVERED,
            DeliveryStatusV1.OUTCOME_UNKNOWN,
            DeliveryStatusV1.DELIVERY_UNCERTAIN,
            DeliveryStatusV1.READ,
        }:
            return _to(state, DeliveryStatusV1.READ, "READ_IS_MONOTONIC")
        raise InvalidDeliveryTransition(f"READ_ACK is invalid from {status.value}")

    if event in {DeliveryEvent.SEND_CONFIRMED, DeliveryEvent.RECEIPT_DELIVERED}:
        if status in {
            DeliveryStatusV1.SELECTED,
            DeliveryStatusV1.DELIVERING,
            DeliveryStatusV1.OUTCOME_UNKNOWN,
            DeliveryStatusV1.DELIVERY_UNCERTAIN,
            DeliveryStatusV1.DELIVERED,
        }:
            return _to(state, DeliveryStatusV1.DELIVERED, "DELIVERED_RECEIPT_WINS")
        if status is DeliveryStatusV1.READ:
            return _to(state, DeliveryStatusV1.READ, "LATE_DELIVERED_AFTER_READ")
        raise InvalidDeliveryTransition(
            f"delivered receipt conflicts with {status.value}"
        )

    if event is DeliveryEvent.RECEIPT_NOT_DELIVERED:
        if status in {
            DeliveryStatusV1.OUTCOME_UNKNOWN,
            DeliveryStatusV1.DELIVERY_UNCERTAIN,
        }:
            return _to(
                state, DeliveryStatusV1.SELECTED,
                "AUTHORITATIVE_NOT_DELIVERED", resend_allowed=True,
            )
        if status is DeliveryStatusV1.SELECTED:
            return _to(state, status, "NOT_DELIVERED_ALREADY_SELECTED", True)
        raise InvalidDeliveryTransition(
            f"NOT_DELIVERED conflicts with authoritative {status.value}"
        )

    if event is DeliveryEvent.SEND_STARTED:
        if status is not DeliveryStatusV1.SELECTED:
            raise InvalidDeliveryTransition(f"SEND_STARTED is invalid from {status.value}")
        return _start_attempt(state, "SEND_ATTEMPT_STARTED")

    if event is DeliveryEvent.SEND_DISCONNECTED:
        if status is not DeliveryStatusV1.DELIVERING:
            raise InvalidDeliveryTransition(
                f"SEND_DISCONNECTED is invalid from {status.value}"
            )
        target = (
            DeliveryStatusV1.DELIVERY_UNCERTAIN
            if state.connector_capability is ConnectorCapability.NONE
            else DeliveryStatusV1.OUTCOME_UNKNOWN
        )
        return _to(state, target, "SEND_OUTCOME_UNKNOWN")

    if event is DeliveryEvent.RECEIPT_UNKNOWN:
        if status not in {
            DeliveryStatusV1.OUTCOME_UNKNOWN,
            DeliveryStatusV1.DELIVERY_UNCERTAIN,
        }:
            raise InvalidDeliveryTransition(
                f"RECEIPT_UNKNOWN is invalid from {status.value}"
            )
        return _to(state, status, "RECEIPT_STILL_UNKNOWN")

    if event is DeliveryEvent.RETRY_DUE:
        if status is DeliveryStatusV1.SELECTED:
            return _start_attempt(state, "KNOWN_NOT_DELIVERED_RETRY")
        if status is not DeliveryStatusV1.OUTCOME_UNKNOWN:
            raise InvalidDeliveryTransition(f"RETRY_DUE is invalid from {status.value}")
        if state.connector_capability is ConnectorCapability.IDEMPOTENT_SEND:
            return _start_attempt(state, "IDEMPOTENT_RESEND")
        if state.connector_capability is ConnectorCapability.QUERY_RECEIPT:
            raise InvalidDeliveryTransition("receipt must be queried before resend")
        return _to(state, DeliveryStatusV1.DELIVERY_UNCERTAIN, "UNSAFE_TO_RESEND")

    if event in {DeliveryEvent.RETRY_EXHAUSTED, DeliveryEvent.DETERMINISTIC_FAILURE}:
        if status in {
            DeliveryStatusV1.SELECTED,
            DeliveryStatusV1.DELIVERING,
        }:
            return _to(state, DeliveryStatusV1.FAILED, event.value)
        if status is DeliveryStatusV1.FAILED:
            return _to(state, status, "FAILURE_ALREADY_RECORDED")
        raise InvalidDeliveryTransition(
            f"{event.value} is invalid from {status.value}"
        )

    if event is DeliveryEvent.RECONCILE_DEADLINE:
        if status is not DeliveryStatusV1.OUTCOME_UNKNOWN:
            raise InvalidDeliveryTransition(
                f"RECONCILE_DEADLINE is invalid from {status.value}"
            )
        return _to(state, DeliveryStatusV1.DELIVERY_UNCERTAIN, "DEADLINE_EXPIRED")

    raise InvalidDeliveryTransition(f"unsupported delivery event: {event!r}")


def _start_attempt(state: DeliveryState, reason: str) -> DeliveryTransition:
    if state.attempt >= state.max_attempts:
        return _to(state, DeliveryStatusV1.FAILED, "RETRY_EXHAUSTED")
    return DeliveryTransition(
        DeliveryState(
            status=DeliveryStatusV1.DELIVERING,
            attempt=state.attempt + 1,
            max_attempts=state.max_attempts,
            connector_capability=state.connector_capability,
            retry_policy_version=state.retry_policy_version,
            reconcile_deadline=state.reconcile_deadline,
        ),
        reason,
    )


def _to(
    state: DeliveryState,
    status: DeliveryStatusV1,
    reason: str,
    resend_allowed: bool = False,
) -> DeliveryTransition:
    return DeliveryTransition(
        DeliveryState(
            status=status,
            attempt=state.attempt,
            max_attempts=state.max_attempts,
            connector_capability=state.connector_capability,
            retry_policy_version=state.retry_policy_version,
            reconcile_deadline=state.reconcile_deadline,
        ),
        reason,
        resend_allowed,
    )
