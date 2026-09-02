import itertools

import pytest

from application.delivery_contract import (
    ConnectorCapability,
    DeliveryContractError,
    DeliveryEvent,
    DeliveryState,
    DeliveryStatusV1,
    InvalidDeliveryTransition,
    transition_delivery,
)


def _state(status, capability=ConnectorCapability.IDEMPOTENT_SEND, attempt=0, maximum=3):
    return DeliveryState(status, attempt, maximum, capability, "retry-v1", "later")


def test_every_state_event_pair_is_a_typed_transition_or_typed_rejection():
    for status, capability, event in itertools.product(
        DeliveryStatusV1, ConnectorCapability, DeliveryEvent,
    ):
        attempt = 1 if status is not DeliveryStatusV1.SELECTED else 0
        state = _state(status, capability, attempt)
        try:
            result = transition_delivery(state, event)
            assert isinstance(result.state.status, DeliveryStatusV1)
            assert result.state.attempt >= state.attempt
        except InvalidDeliveryTransition:
            pass


def test_send_disconnect_requires_safe_connector_before_any_resend():
    delivering = _state(DeliveryStatusV1.DELIVERING, attempt=1)
    unknown = transition_delivery(delivering, DeliveryEvent.SEND_DISCONNECTED).state
    assert unknown.status is DeliveryStatusV1.OUTCOME_UNKNOWN
    retry = transition_delivery(unknown, DeliveryEvent.RETRY_DUE).state
    assert retry.status is DeliveryStatusV1.DELIVERING
    assert retry.attempt == 2

    unsafe = _state(
        DeliveryStatusV1.DELIVERING, ConnectorCapability.NONE, attempt=1,
    )
    assert transition_delivery(
        unsafe, DeliveryEvent.SEND_DISCONNECTED,
    ).state.status is DeliveryStatusV1.DELIVERY_UNCERTAIN


def test_receipt_query_connector_cannot_resend_until_not_delivered_is_authoritative():
    unknown = _state(
        DeliveryStatusV1.OUTCOME_UNKNOWN,
        ConnectorCapability.QUERY_RECEIPT,
        attempt=1,
    )
    with pytest.raises(InvalidDeliveryTransition, match="queried"):
        transition_delivery(unknown, DeliveryEvent.RETRY_DUE)
    selected = transition_delivery(
        unknown, DeliveryEvent.RECEIPT_NOT_DELIVERED,
    )
    assert selected.state.status is DeliveryStatusV1.SELECTED
    assert selected.resend_allowed is True


def test_read_and_delivered_receipts_are_monotonic_and_conflicts_fail_closed():
    delivered = _state(DeliveryStatusV1.DELIVERED, attempt=1)
    read = transition_delivery(delivered, DeliveryEvent.READ_ACK).state
    assert read.status is DeliveryStatusV1.READ
    assert transition_delivery(
        read, DeliveryEvent.RECEIPT_DELIVERED,
    ).state.status is DeliveryStatusV1.READ
    assert transition_delivery(
        read, DeliveryEvent.READ_ACK,
    ).state.status is DeliveryStatusV1.READ
    with pytest.raises(InvalidDeliveryTransition, match="conflicts"):
        transition_delivery(read, DeliveryEvent.RECEIPT_NOT_DELIVERED)


def test_retry_exhaustion_is_deterministic_and_never_increments_past_max():
    selected = _state(DeliveryStatusV1.SELECTED, attempt=3, maximum=3)
    result = transition_delivery(selected, DeliveryEvent.SEND_STARTED)
    assert result.state.status is DeliveryStatusV1.FAILED
    assert result.state.attempt == 3
    assert result.reason_code == "RETRY_EXHAUSTED"


def test_invalid_attempt_contract_fails_closed():
    with pytest.raises(DeliveryContractError, match="attempt bounds"):
        _state(DeliveryStatusV1.SELECTED, attempt=4, maximum=3)
