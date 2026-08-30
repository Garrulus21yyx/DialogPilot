"""客户业务 Owner 的资格、版本、幂等与隔离合同。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import pytest

from services.customer_operations import (
    BusinessObjectNotFoundError,
    CustomerOperationsService,
    OperationIdempotencyConflictError,
    OrderStatus,
    RefundNotEligibleError,
    SecuritySeverity,
    StaleOrderVersionError,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def service(tmp_path):
    return CustomerOperationsService(str(tmp_path / "operations.db"), clock=lambda: NOW)


def seed_order(owner, *, order_id="order-1", user_id="user-1", status=OrderStatus.DELIVERED):
    return owner.upsert_order(
        order_id=order_id,
        user_id=user_id,
        item_name="机械键盘",
        amount_minor=39900,
        currency="cny",
        status=status,
        refundable_until="2026-09-15T00:00:00+00:00",
    )


def test_refund_eligibility_and_create_are_owned_by_one_transaction(tmp_path):
    owner = service(tmp_path)
    order = seed_order(owner)

    eligibility = owner.check_refund_eligibility(user_id="user-1", order_id="order-1")
    refund, created = owner.create_refund_request(
        idempotency_key="refund-op-1",
        user_id="user-1",
        order_id="order-1",
        expected_order_version=order.version,
        reason="按约退货",
    )
    retry, retry_created = owner.create_refund_request(
        idempotency_key="refund-op-1",
        user_id="user-1",
        order_id="order-1",
        expected_order_version=order.version,
        reason="按约退货",
    )

    assert eligibility.eligible is True
    assert eligibility.reason_code == "eligible"
    assert refund.refund_id == retry.refund_id
    assert created is True and retry_created is False
    assert owner.check_refund_eligibility(
        user_id="user-1", order_id="order-1"
    ).reason_code == "refund_already_requested"


def test_refund_rechecks_version_and_policy_instead_of_trusting_tool_params(tmp_path):
    owner = service(tmp_path)
    original = seed_order(owner)
    updated = seed_order(owner, status=OrderStatus.CANCELLED)

    with pytest.raises(StaleOrderVersionError):
        owner.create_refund_request(
            idempotency_key="stale",
            user_id="user-1",
            order_id="order-1",
            expected_order_version=original.version,
            reason="旧资格快照",
        )
    with pytest.raises(RefundNotEligibleError) as exc:
        owner.create_refund_request(
            idempotency_key="not-eligible",
            user_id="user-1",
            order_id="order-1",
            expected_order_version=updated.version,
            reason="订单已取消",
        )
    assert exc.value.reason_code == "order_status_cancelled"


@pytest.mark.parametrize(
    ("status", "refundable_until", "reason_code"),
    [
        (OrderStatus.PAID, "2026-09-15T00:00:00+00:00", "order_status_paid"),
        (OrderStatus.SHIPPED, "2026-09-15T00:00:00+00:00", "order_status_shipped"),
        (OrderStatus.CANCELLED, "2026-09-15T00:00:00+00:00", "order_status_cancelled"),
        (OrderStatus.DELIVERED, None, "refund_window_missing"),
        (OrderStatus.DELIVERED, "2026-08-01T00:00:00+00:00", "refund_window_expired"),
    ],
)
def test_refund_policy_algebra_is_closed(tmp_path, status, refundable_until, reason_code):
    owner = service(tmp_path)
    owner.upsert_order(
        order_id="order-1", user_id="user-1", item_name="机械键盘",
        amount_minor=39900, currency="CNY", status=status,
        refundable_until=refundable_until,
    )
    result = owner.check_refund_eligibility(user_id="user-1", order_id="order-1")
    assert result.eligible is False
    assert result.reason_code == reason_code


def test_concurrent_refund_requests_commit_at_most_once(tmp_path):
    owner = service(tmp_path)
    order = seed_order(owner)
    barrier = threading.Barrier(2)

    def attempt(index):
        barrier.wait()
        try:
            refund, created = owner.create_refund_request(
                idempotency_key=f"concurrent-{index}", user_id="user-1",
                order_id=order.order_id, expected_order_version=order.version,
                reason=f"并发申请 {index}",
            )
            return ("created", refund.refund_id, created)
        except RefundNotEligibleError as exc:
            return ("rejected", exc.reason_code, False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))

    assert sum(outcome[0] == "created" for outcome in outcomes) == 1
    assert sum(outcome[:2] == ("rejected", "refund_already_requested") for outcome in outcomes) == 1


def test_cross_user_reads_fail_closed_and_idempotency_conflicts_are_typed(tmp_path):
    owner = service(tmp_path)
    order = seed_order(owner)

    with pytest.raises(BusinessObjectNotFoundError):
        owner.get_order_for_user(user_id="user-2", order_id=order.order_id)
    owner.create_refund_request(
        idempotency_key="same-key",
        user_id="user-1",
        order_id=order.order_id,
        expected_order_version=order.version,
        reason="原因一",
    )
    with pytest.raises(OperationIdempotencyConflictError):
        owner.create_refund_request(
            idempotency_key="same-key",
            user_id="user-1",
            order_id=order.order_id,
            expected_order_version=order.version,
            reason="原因二",
        )


def test_security_events_are_append_only_and_user_scoped(tmp_path):
    owner = service(tmp_path)
    owner.record_security_event(
        user_id="user-1", event_type="new_device_login",
        severity=SecuritySeverity.WARNING, summary="柏林的新设备登录",
    )
    owner.record_security_event(
        user_id="user-2", event_type="password_changed",
        severity=SecuritySeverity.INFO, summary="密码已修改",
    )

    events = owner.list_security_events(user_id="user-1", limit=20)
    assert [event["event_type"] for event in events] == ["new_device_login"]
    assert "user_id" not in events[0]
