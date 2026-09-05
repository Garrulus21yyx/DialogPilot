"""客户业务 Owner 的资格、版本、幂等与隔离合同。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import pytest

from services.customer_operations import (
    AccountStatus,
    BusinessObjectNotFoundError,
    CustomerOperationsService,
    OperationIdempotencyConflictError,
    OrderNotCancellableError,
    OrderStatus,
    RefundNotEligibleError,
    SecuritySeverity,
    ShippingAddressNotChangeableError,
    StaleOrderVersionError,
    StaleAccountVersionError,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def service(customer_operations):
    return CustomerOperationsService(
        customer_operations.pool,
        tenant_id=customer_operations.tenant_id,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize("action", ["refund", "cancel", "address", "freeze"])
def test_same_business_ids_and_operation_keys_are_tenant_isolated(
    customer_operations, action
):
    owners = [
        CustomerOperationsService(
            customer_operations.pool, tenant_id=tenant, clock=lambda: NOW
        )
        for tenant in ("tenant-a", "tenant-b")
    ]
    for owner in owners:
        seed_order(
            owner,
            status=OrderStatus.DELIVERED if action == "refund" else OrderStatus.PAID,
        )
        owner.record_security_event(
            user_id="user-1",
            event_type="login",
            severity=SecuritySeverity.CRITICAL,
            summary="reported",
        )

    def execute(owner):
        common = {"idempotency_key": "same-operation", "user_id": "user-1"}
        if action == "freeze":
            return owner.freeze_account(**common, expected_account_version=1)
        common.update(order_id="order-1", expected_order_version=1)
        if action == "refund":
            return owner.create_refund_request(**common, reason="return")
        if action == "cancel":
            return owner.cancel_order(**common)
        return owner.change_shipping_address(**common, new_address="new address")

    first, second = [execute(owner) for owner in owners]
    assert first[1] and second[1]
    assert first[0] != second[0]
    assert execute(owners[0]) == (first[0], False)
    assert execute(owners[1]) == (second[0], False)
    third = CustomerOperationsService(customer_operations.pool, tenant_id="tenant-c")
    with pytest.raises(BusinessObjectNotFoundError):
        third.get_order_for_user(user_id="user-1", order_id="order-1")


def test_independent_owners_serialize_order_versions_in_postgres(customer_operations):
    def upsert(_):
        owner = service(customer_operations)
        return seed_order(owner).version

    with ThreadPoolExecutor(max_workers=8) as workers:
        versions = list(workers.map(upsert, range(8)))
    assert sorted(versions) == list(range(1, 9))
    assert (
        customer_operations.get_order_for_user(
            user_id="user-1", order_id="order-1"
        ).version
        == 8
    )


def test_competing_order_actions_share_one_database_version_boundary(
    customer_operations,
):
    first, second = service(customer_operations), service(customer_operations)
    seed_order(first, status=OrderStatus.PAID)
    barrier = threading.Barrier(2)

    def execute(action):
        barrier.wait()
        try:
            common = dict(
                idempotency_key=action,
                user_id="user-1",
                order_id="order-1",
                expected_order_version=1,
            )
            return (
                first.cancel_order(**common)
                if action == "cancel"
                else second.change_shipping_address(**common, new_address="new address")
            )
        except StaleOrderVersionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(execute, ("cancel", "address")))
    assert sum(result is not None for result in results) == 1
    assert first.get_order_for_user(user_id="user-1", order_id="order-1").version == 2


def seed_order(
    owner, *, order_id="order-1", user_id="user-1", status=OrderStatus.DELIVERED
):
    return owner.upsert_order(
        order_id=order_id,
        user_id=user_id,
        item_name="机械键盘",
        amount_minor=39900,
        currency="cny",
        status=status,
        refundable_until="2026-09-15T00:00:00+00:00",
    )


def test_refund_eligibility_and_create_are_owned_by_one_transaction(
    customer_operations,
):
    owner = service(customer_operations)
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
    assert owner.get_refund_status(user_id="user-1", order_id="order-1") == refund
    assert (
        owner.get_refund_status_for_operation(
            user_id="user-1",
            order_id="order-1",
            idempotency_key="refund-op-1",
        )
        == refund
    )
    with pytest.raises(BusinessObjectNotFoundError):
        owner.get_refund_status_for_operation(
            user_id="user-1",
            order_id="order-1",
            idempotency_key="another-op",
        )
    assert created is True and retry_created is False
    assert (
        owner.check_refund_eligibility(user_id="user-1", order_id="order-1").reason_code
        == "refund_already_requested"
    )


def test_refund_rechecks_version_and_policy_instead_of_trusting_tool_params(
    customer_operations,
):
    owner = service(customer_operations)
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
def test_refund_policy_algebra_is_closed(
    customer_operations, status, refundable_until, reason_code
):
    owner = service(customer_operations)
    owner.upsert_order(
        order_id="order-1",
        user_id="user-1",
        item_name="机械键盘",
        amount_minor=39900,
        currency="CNY",
        status=status,
        refundable_until=refundable_until,
    )
    result = owner.check_refund_eligibility(user_id="user-1", order_id="order-1")
    assert result.eligible is False
    assert result.reason_code == reason_code


def test_concurrent_refund_requests_commit_at_most_once(customer_operations):
    owner = service(customer_operations)
    order = seed_order(owner)
    barrier = threading.Barrier(2)

    def attempt(index):
        barrier.wait()
        try:
            refund, created = owner.create_refund_request(
                idempotency_key=f"concurrent-{index}",
                user_id="user-1",
                order_id=order.order_id,
                expected_order_version=order.version,
                reason=f"并发申请 {index}",
            )
            return ("created", refund.refund_id, created)
        except RefundNotEligibleError as exc:
            return ("rejected", exc.reason_code, False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))

    assert sum(outcome[0] == "created" for outcome in outcomes) == 1
    assert (
        sum(
            outcome[:2] == ("rejected", "refund_already_requested")
            for outcome in outcomes
        )
        == 1
    )


def test_cross_user_reads_fail_closed_and_idempotency_conflicts_are_typed(
    customer_operations,
):
    owner = service(customer_operations)
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
    with pytest.raises(BusinessObjectNotFoundError):
        owner.get_refund_status(user_id="user-2", order_id=order.order_id)
    with pytest.raises(OperationIdempotencyConflictError):
        owner.create_refund_request(
            idempotency_key="same-key",
            user_id="user-1",
            order_id=order.order_id,
            expected_order_version=order.version,
            reason="原因二",
        )


def test_security_events_are_append_only_and_user_scoped(customer_operations):
    owner = service(customer_operations)
    owner.record_security_event(
        user_id="user-1",
        event_type="new_device_login",
        severity=SecuritySeverity.WARNING,
        summary="柏林的新设备登录",
    )
    owner.record_security_event(
        user_id="user-2",
        event_type="password_changed",
        severity=SecuritySeverity.INFO,
        summary="密码已修改",
    )

    events = owner.list_security_events(user_id="user-1", limit=20)
    assert [event["event_type"] for event in events] == ["new_device_login"]
    assert "user_id" not in events[0]
    assert (
        owner.get_account_security_state(
            user_id="user-1",
        ).status
        is AccountStatus.ACTIVE
    )


def test_account_freeze_is_versioned_idempotent_and_operation_queryable(
    customer_operations,
):
    owner = service(customer_operations)
    owner.record_security_event(
        user_id="user-1",
        event_type="suspicious_login",
        severity=SecuritySeverity.CRITICAL,
        summary="未知设备登录",
    )
    account = owner.get_account_security_state(user_id="user-1")

    freeze, created = owner.freeze_account(
        idempotency_key="freeze-op-1",
        user_id="user-1",
        expected_account_version=account.version,
    )
    replay, replay_created = owner.freeze_account(
        idempotency_key="freeze-op-1",
        user_id="user-1",
        expected_account_version=account.version,
    )

    assert created is True and replay_created is False
    assert replay == freeze
    assert freeze.account_version == account.version + 1
    assert (
        owner.get_account_security_state(
            user_id="user-1",
        ).status
        is AccountStatus.FROZEN
    )
    assert (
        owner.get_account_freeze_for_operation(
            user_id="user-1",
            idempotency_key="freeze-op-1",
        )
        == freeze
    )
    with pytest.raises(BusinessObjectNotFoundError):
        owner.get_account_freeze_for_operation(
            user_id="user-2",
            idempotency_key="freeze-op-1",
        )
    with pytest.raises(StaleAccountVersionError):
        owner.freeze_account(
            idempotency_key="freeze-op-2",
            user_id="user-1",
            expected_account_version=account.version,
        )


def test_order_cancellation_is_versioned_idempotent_and_operation_queryable(
    customer_operations,
):
    owner = service(customer_operations)
    order = seed_order(owner, status=OrderStatus.PAID)

    cancellation, created = owner.cancel_order(
        idempotency_key="cancel-op-1",
        user_id="user-1",
        order_id=order.order_id,
        expected_order_version=order.version,
    )
    replay, replay_created = owner.cancel_order(
        idempotency_key="cancel-op-1",
        user_id="user-1",
        order_id=order.order_id,
        expected_order_version=order.version,
    )

    assert created is True and replay_created is False
    assert replay == cancellation
    assert cancellation.status == OrderStatus.CANCELLED.value
    assert cancellation.order_version == order.version + 1
    assert (
        owner.get_order_for_user(
            user_id="user-1",
            order_id=order.order_id,
        ).status
        is OrderStatus.CANCELLED
    )
    assert (
        owner.get_order_cancellation_for_operation(
            user_id="user-1",
            order_id=order.order_id,
            idempotency_key="cancel-op-1",
        )
        == cancellation
    )


def test_order_cancellation_rechecks_state_version_and_user_scope(customer_operations):
    owner = service(customer_operations)
    paid = seed_order(owner, status=OrderStatus.PAID)
    shipped = seed_order(owner, status=OrderStatus.SHIPPED)

    with pytest.raises(StaleOrderVersionError):
        owner.cancel_order(
            idempotency_key="cancel-stale",
            user_id="user-1",
            order_id=paid.order_id,
            expected_order_version=paid.version,
        )
    with pytest.raises(OrderNotCancellableError):
        owner.cancel_order(
            idempotency_key="cancel-shipped",
            user_id="user-1",
            order_id=shipped.order_id,
            expected_order_version=shipped.version,
        )
    with pytest.raises(BusinessObjectNotFoundError):
        owner.get_order_cancellation_for_operation(
            user_id="user-2",
            order_id=shipped.order_id,
            idempotency_key="cancel-shipped",
        )


def test_shipping_address_change_is_versioned_idempotent_and_operation_queryable(
    customer_operations,
):
    owner = service(customer_operations)
    order = seed_order(owner, status=OrderStatus.PAID)

    change, created = owner.change_shipping_address(
        idempotency_key="address-op-1",
        user_id="user-1",
        order_id=order.order_id,
        expected_order_version=order.version,
        new_address="Berlin, Example Street 9",
    )
    replay, replay_created = owner.change_shipping_address(
        idempotency_key="address-op-1",
        user_id="user-1",
        order_id=order.order_id,
        expected_order_version=order.version,
        new_address="Berlin, Example Street 9",
    )

    assert created is True and replay_created is False
    assert replay == change
    assert change.order_version == order.version + 1
    assert (
        owner.get_order_for_user(
            user_id="user-1",
            order_id=order.order_id,
        ).shipping_address
        == "Berlin, Example Street 9"
    )
    assert (
        owner.get_shipping_address_change_for_operation(
            user_id="user-1",
            order_id=order.order_id,
            idempotency_key="address-op-1",
        )
        == change
    )


def test_shipping_address_change_rechecks_state_version_and_user_scope(
    customer_operations,
):
    owner = service(customer_operations)
    paid = seed_order(owner, status=OrderStatus.PAID)
    shipped = seed_order(owner, status=OrderStatus.SHIPPED)

    with pytest.raises(StaleOrderVersionError):
        owner.change_shipping_address(
            idempotency_key="address-stale",
            user_id="user-1",
            order_id=paid.order_id,
            expected_order_version=paid.version,
            new_address="Berlin, New Street 1",
        )
    with pytest.raises(ShippingAddressNotChangeableError):
        owner.change_shipping_address(
            idempotency_key="address-shipped",
            user_id="user-1",
            order_id=shipped.order_id,
            expected_order_version=shipped.version,
            new_address="Berlin, New Street 1",
        )
    with pytest.raises(BusinessObjectNotFoundError):
        owner.get_shipping_address_change_for_operation(
            user_id="user-2",
            order_id=shipped.order_id,
            idempotency_key="address-shipped",
        )
