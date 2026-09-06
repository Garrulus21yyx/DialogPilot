"""订单、退款与账户安全事实的本地业务 Owner。

该模块是可替换外部 CRM/订单平台的 PostgreSQL 业务沙箱：工具层只能读取或请求这里
定义的业务操作，不能自行判断退款资格、制造订单状态或宣称副作用已提交。
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Mapping, TYPE_CHECKING

from psycopg.rows import dict_row

if TYPE_CHECKING:
    from infrastructure.postgres import PostgresPool


class CustomerOperationError(Exception):
    """客户业务领域失败的基类。"""


class BusinessObjectNotFoundError(CustomerOperationError):
    """对象不存在或不属于当前认证用户。"""


class RefundNotEligibleError(CustomerOperationError):
    """订单不满足退款申请合同。"""

    def __init__(self, reason_code: str):
        super().__init__(f"refund is not eligible: {reason_code}")
        self.reason_code = reason_code


class StaleOrderVersionError(CustomerOperationError):
    """资格检查后订单版本已经变化。"""


class StaleAccountVersionError(CustomerOperationError):
    """The account changed after the security-state check."""


class OrderNotCancellableError(CustomerOperationError):
    """The current authoritative order state cannot be cancelled."""


class ShippingAddressNotChangeableError(CustomerOperationError):
    """The current authoritative order state cannot accept an address change."""


class AccountNotFreezableError(CustomerOperationError):
    """The current authoritative account state cannot be frozen."""


class OperationIdempotencyConflictError(CustomerOperationError):
    """同一幂等键被复用于另一项业务请求。"""


class OrderStatus(str, Enum):
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class RefundStatus(str, Enum):
    REQUESTED = "requested"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUNDED = "refunded"


class SecuritySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    FROZEN = "frozen"


@dataclass(frozen=True)
class Order:
    order_id: str
    user_id: str
    item_name: str
    amount_minor: int
    currency: str
    status: OrderStatus
    refundable_until: Optional[str]
    version: int
    created_at: str
    updated_at: str
    shipping_address: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class RefundEligibility:
    order_id: str
    eligible: bool
    reason_code: str
    amount_minor: int
    currency: str
    order_version: int
    refundable_until: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RefundRequest:
    refund_id: str
    order_id: str
    user_id: str
    reason: str
    amount_minor: int
    currency: str
    status: RefundStatus
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class RefundLookup:
    """An authorized order's refund observation at one database snapshot."""

    order_id: str
    request: Optional[RefundRequest]
    order_version: Optional[int] = None

    def __post_init__(self):
        if not self.order_id or (self.request is not None and self.request.order_id != self.order_id):
            raise ValueError("refund lookup requires matching order identity")
        if self.request is None and (type(self.order_version) is not int or self.order_version < 1):
            raise ValueError("absence requires the observed order version")


@dataclass(frozen=True)
class OrderCancellation:
    cancellation_id: str
    order_id: str
    user_id: str
    status: str
    order_version: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShippingAddressChange:
    change_id: str
    order_id: str
    user_id: str
    new_address: str
    status: str
    order_version: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountSecurityState:
    user_id: str
    status: AccountStatus
    version: int
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class AccountFreeze:
    freeze_id: str
    user_id: str
    status: str
    account_version: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CustomerOperationsService:
    """订单、退款资格、退款申请和安全事件的唯一事实 Owner。"""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        tenant_id: str,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.pool = pool
        self.tenant_id = self._required(tenant_id, "tenant_id")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def upsert_order(
        self,
        *,
        order_id: str,
        user_id: str,
        item_name: str,
        amount_minor: int,
        currency: str,
        status: OrderStatus,
        refundable_until: Optional[str] = None,
        shipping_address: Optional[str] = None,
    ) -> Order:
        """由可信业务适配器写入订单快照；更新时单调递增版本。"""
        order_id = self._required(order_id, "order_id")
        user_id = self._required(user_id, "user_id")
        item_name = self._required(item_name, "item_name")
        amount_minor = int(amount_minor)
        if amount_minor <= 0:
            raise ValueError("amount_minor must be positive")
        currency = self._required(currency, "currency").upper()
        if len(currency) != 3:
            raise ValueError("currency must be a 3-letter code")
        status = OrderStatus(status)
        if refundable_until:
            self._parse_time(refundable_until)
        now = self._now()
        with self._connect(locks=(f"order:{order_id}",)) as conn:
            existing = conn.execute(
                """
                SELECT version, created_at, shipping_address FROM dialogpilot_app.customer_orders WHERE
                order_id = %s AND tenant_id=%s
                """,
                (order_id, self.tenant_id),
            ).fetchone()
            version = int(existing["version"]) + 1 if existing else 1
            created_at = existing["created_at"] if existing else now
            resolved_address = (
                str(shipping_address).strip()
                if shipping_address is not None
                else str(existing["shipping_address"] or "")
                if existing
                else ""
            )
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_orders ( order_id, user_id, item_name,
                amount_minor, currency, status, refundable_until, version, created_at, updated_at,
                shipping_address, tenant_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON
                CONFLICT(tenant_id, order_id) DO UPDATE SET user_id=excluded.user_id,
                item_name=excluded.item_name, amount_minor=excluded.amount_minor,
                currency=excluded.currency, status=excluded.status,
                refundable_until=excluded.refundable_until, version=excluded.version,
                updated_at=excluded.updated_at, shipping_address=excluded.shipping_address
                """,
                (
                    order_id,
                    user_id,
                    item_name,
                    amount_minor,
                    currency,
                    status.value,
                    refundable_until,
                    version,
                    created_at,
                    now,
                    resolved_address,
                    self.tenant_id,
                ),
            )
            row = conn.execute(
                """
                               SELECT * FROM dialogpilot_app.customer_orders WHERE order_id = %s AND tenant_id=%s
                               """,
                (order_id, self.tenant_id),
            ).fetchone()
        return self._row_to_order(row)

    def get_order_for_user(self, *, user_id: str, order_id: str) -> Order:
        """按可信用户读取订单；不存在与越权读取使用同一种失败。"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_orders WHERE order_id = %s AND user_id = %s AND
                tenant_id=%s
                """,
                (
                    self._required(order_id, "order_id"),
                    self._required(user_id, "user_id"),
                    self.tenant_id,
                ),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("order not found")
        return self._row_to_order(row)

    def check_refund_eligibility(
        self, *, user_id: str, order_id: str
    ) -> RefundEligibility:
        """由订单状态、窗口与已有退款共同计算确定性的资格快照。"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_orders WHERE order_id = %s AND user_id = %s AND
                tenant_id=%s
                """,
                (
                    self._required(order_id, "order_id"),
                    self._required(user_id, "user_id"),
                    self.tenant_id,
                ),
            ).fetchone()
            if row is None:
                raise BusinessObjectNotFoundError("order not found")
            existing = conn.execute(
                """
                SELECT refund_id FROM dialogpilot_app.customer_refund_requests WHERE order_id = %s AND
                tenant_id=%s
                """,
                (order_id, self.tenant_id),
            ).fetchone()
        order = self._row_to_order(row)
        return self._eligibility(order, has_refund=existing is not None)

    def get_refund_status(self, *, user_id: str, order_id: str) -> RefundRequest:
        """Require an existing request; ordinary search uses lookup_refund_status."""
        observation = self.lookup_refund_status(user_id=user_id, order_id=order_id)
        if observation.request is None:
            raise BusinessObjectNotFoundError("refund request not found")
        return observation.request

    def lookup_refund_status(self, *, user_id: str, order_id: str) -> RefundLookup:
        """Distinguish no application from an inaccessible order in one snapshot."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT orders.order_id, orders.version AS order_version, to_jsonb(refund) AS refund
                FROM dialogpilot_app.customer_orders AS orders
                LEFT JOIN dialogpilot_app.customer_refund_requests AS refund
                  ON refund.order_id=orders.order_id AND refund.tenant_id=orders.tenant_id
                  AND refund.user_id=orders.user_id
                WHERE orders.order_id=%s AND orders.user_id=%s AND orders.tenant_id=%s
                """,
                (
                    self._required(order_id, "order_id"),
                    self._required(user_id, "user_id"),
                    self.tenant_id,
                ),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("order not found")
        return RefundLookup(row['order_id'], self._row_to_refund(row['refund']) if row['refund'] else None,
                            row['order_version'])

    def get_refund_status_for_operation(
        self,
        *,
        user_id: str,
        order_id: str,
        idempotency_key: str,
    ) -> RefundRequest:
        """Read only the refund created by one exact governed operation."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_refund_requests WHERE order_id=%s AND user_id=%s
                AND idempotency_key=%s AND tenant_id=%s
                """,
                (
                    self._required(order_id, "order_id"),
                    self._required(user_id, "user_id"),
                    self._required(idempotency_key, "idempotency_key"),
                    self.tenant_id,
                ),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("refund operation not found")
        return self._row_to_refund(row)

    def create_refund_request(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        order_id: str,
        expected_order_version: int,
        reason: str,
    ) -> Tuple[RefundRequest, bool]:
        """在一个事务中重验资格并幂等创建退款申请。"""
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        user_id = self._required(user_id, "user_id")
        order_id = self._required(order_id, "order_id")
        reason = self._required(reason, "reason")[:1000]
        expected_order_version = int(expected_order_version)
        fingerprint = self._fingerprint(
            {
                "idempotency_key": idempotency_key,
                "user_id": user_id,
                "order_id": order_id,
                "expected_order_version": expected_order_version,
                "reason": reason,
            }
        )
        with self._connect(
            locks=(f"order:{order_id}", f"refund-operation:{idempotency_key}")
        ) as conn:
            existing = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_refund_requests WHERE idempotency_key = %s AND
                tenant_id=%s
                """,
                (idempotency_key, self.tenant_id),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise OperationIdempotencyConflictError(
                        "idempotency key was reused with different refund content"
                    )
                return self._row_to_refund(existing), False

            order_row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_orders WHERE order_id = %s AND user_id = %s AND
                tenant_id=%s
                """,
                (order_id, user_id, self.tenant_id),
            ).fetchone()
            if order_row is None:
                raise BusinessObjectNotFoundError("order not found")
            order = self._row_to_order(order_row)
            if order.version != expected_order_version:
                raise StaleOrderVersionError("order changed after eligibility check")
            prior = conn.execute(
                """
                SELECT refund_id FROM dialogpilot_app.customer_refund_requests WHERE order_id = %s AND
                tenant_id=%s
                """,
                (order_id, self.tenant_id),
            ).fetchone()
            eligibility = self._eligibility(order, has_refund=prior is not None)
            if not eligibility.eligible:
                raise RefundNotEligibleError(eligibility.reason_code)

            refund_id = uuid.uuid4().hex
            now = self._now()
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_refund_requests ( refund_id, idempotency_key,
                request_fingerprint, order_id, user_id, reason, amount_minor, currency, status,
                created_at, updated_at, tenant_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s)
                """,
                (
                    refund_id,
                    idempotency_key,
                    fingerprint,
                    order_id,
                    user_id,
                    reason,
                    order.amount_minor,
                    order.currency,
                    RefundStatus.REQUESTED.value,
                    now,
                    now,
                    self.tenant_id,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_refund_requests WHERE refund_id = %s AND
                tenant_id=%s
                """,
                (refund_id, self.tenant_id),
            ).fetchone()
        return self._row_to_refund(row), True

    def cancel_order(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        order_id: str,
        expected_order_version: int,
    ) -> Tuple[OrderCancellation, bool]:
        """Recheck a paid order and atomically record one idempotent cancellation."""
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        user_id = self._required(user_id, "user_id")
        order_id = self._required(order_id, "order_id")
        expected_order_version = int(expected_order_version)
        fingerprint = self._fingerprint(
            {
                "idempotency_key": idempotency_key,
                "user_id": user_id,
                "order_id": order_id,
                "expected_order_version": expected_order_version,
            }
        )
        with self._connect(
            locks=(f"order:{order_id}", f"cancel-operation:{idempotency_key}")
        ) as conn:
            existing = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_order_cancellations WHERE idempotency_key=%s AND
                tenant_id=%s
                """,
                (idempotency_key, self.tenant_id),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise OperationIdempotencyConflictError(
                        "idempotency key was reused with different cancellation content"
                    )
                return self._row_to_cancellation(existing), False
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_orders WHERE order_id=%s AND user_id=%s AND
                tenant_id=%s
                """,
                (order_id, user_id, self.tenant_id),
            ).fetchone()
            if row is None:
                raise BusinessObjectNotFoundError("order not found")
            order = self._row_to_order(row)
            if order.version != expected_order_version:
                raise StaleOrderVersionError("order changed after cancellation check")
            if order.status is not OrderStatus.PAID:
                raise OrderNotCancellableError(
                    f"order status {order.status.value} cannot be cancelled"
                )
            cancellation_id = uuid.uuid4().hex
            now = self._now()
            next_version = order.version + 1
            conn.execute(
                """
                UPDATE dialogpilot_app.customer_orders SET status=%s,version=%s,updated_at=%s WHERE
                order_id=%s AND tenant_id=%s
                """,
                (
                    OrderStatus.CANCELLED.value,
                    next_version,
                    now,
                    order_id,
                    self.tenant_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_order_cancellations (
                cancellation_id,idempotency_key,request_fingerprint,order_id,
                user_id,status,order_version,created_at, tenant_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, %s)
                """,
                (
                    cancellation_id,
                    idempotency_key,
                    fingerprint,
                    order_id,
                    user_id,
                    OrderStatus.CANCELLED.value,
                    next_version,
                    now,
                    self.tenant_id,
                ),
            )
            created = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_order_cancellations WHERE cancellation_id=%s AND
                tenant_id=%s
                """,
                (cancellation_id, self.tenant_id),
            ).fetchone()
        return self._row_to_cancellation(created), True

    def get_order_cancellation_for_operation(
        self,
        *,
        user_id: str,
        order_id: str,
        idempotency_key: str,
    ) -> OrderCancellation:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_order_cancellations WHERE user_id=%s AND
                order_id=%s AND idempotency_key=%s AND tenant_id=%s
                """,
                (
                    self._required(user_id, "user_id"),
                    self._required(order_id, "order_id"),
                    self._required(idempotency_key, "idempotency_key"),
                    self.tenant_id,
                ),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("order cancellation operation not found")
        return self._row_to_cancellation(row)

    def change_shipping_address(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        order_id: str,
        expected_order_version: int,
        new_address: str,
    ) -> Tuple[ShippingAddressChange, bool]:
        """Atomically recheck a paid order and apply one idempotent address change."""
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        user_id = self._required(user_id, "user_id")
        order_id = self._required(order_id, "order_id")
        new_address = self._required(new_address, "new_address")[:500]
        expected_order_version = int(expected_order_version)
        fingerprint = self._fingerprint(
            {
                "idempotency_key": idempotency_key,
                "user_id": user_id,
                "order_id": order_id,
                "expected_order_version": expected_order_version,
                "new_address": new_address,
            }
        )
        with self._connect(
            locks=(f"order:{order_id}", f"address-operation:{idempotency_key}")
        ) as conn:
            existing = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_shipping_address_changes WHERE idempotency_key=%s
                AND tenant_id=%s
                """,
                (idempotency_key, self.tenant_id),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise OperationIdempotencyConflictError(
                        "idempotency key was reused with different address content"
                    )
                return self._row_to_address_change(existing), False
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_orders WHERE order_id=%s AND user_id=%s AND
                tenant_id=%s
                """,
                (order_id, user_id, self.tenant_id),
            ).fetchone()
            if row is None:
                raise BusinessObjectNotFoundError("order not found")
            order = self._row_to_order(row)
            if order.version != expected_order_version:
                raise StaleOrderVersionError("order changed after address check")
            if order.status is not OrderStatus.PAID:
                raise ShippingAddressNotChangeableError(
                    f"order status {order.status.value} cannot change address"
                )
            change_id = uuid.uuid4().hex
            now = self._now()
            next_version = order.version + 1
            conn.execute(
                """
                UPDATE dialogpilot_app.customer_orders SET shipping_address=%s,version=%s,updated_at=%s
                WHERE order_id=%s AND tenant_id=%s
                """,
                (new_address, next_version, now, order_id, self.tenant_id),
            )
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_shipping_address_changes (
                change_id,idempotency_key,request_fingerprint,order_id,user_id,
                new_address,status,order_version,created_at, tenant_id) VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s, %s)
                """,
                (
                    change_id,
                    idempotency_key,
                    fingerprint,
                    order_id,
                    user_id,
                    new_address,
                    "updated",
                    next_version,
                    now,
                    self.tenant_id,
                ),
            )
            created = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_shipping_address_changes WHERE change_id=%s AND
                tenant_id=%s
                """,
                (change_id, self.tenant_id),
            ).fetchone()
        return self._row_to_address_change(created), True

    def get_shipping_address_change_for_operation(
        self,
        *,
        user_id: str,
        order_id: str,
        idempotency_key: str,
    ) -> ShippingAddressChange:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_shipping_address_changes WHERE user_id=%s AND
                order_id=%s AND idempotency_key=%s AND tenant_id=%s
                """,
                (
                    self._required(user_id, "user_id"),
                    self._required(order_id, "order_id"),
                    self._required(idempotency_key, "idempotency_key"),
                    self.tenant_id,
                ),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("shipping address operation not found")
        return self._row_to_address_change(row)

    def record_security_event(
        self,
        *,
        user_id: str,
        event_type: str,
        severity: SecuritySeverity,
        summary: str,
        occurred_at: Optional[str] = None,
    ) -> str:
        """由可信账户系统追加不可变安全事件。"""
        event_id = uuid.uuid4().hex
        occurred_at = occurred_at or self._now()
        self._parse_time(occurred_at)
        with self._connect(locks=(f"account:{user_id}",)) as conn:
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_accounts (user_id,status,version,updated_at,
                tenant_id) VALUES (%s,%s,%s,%s, %s) ON CONFLICT (tenant_id, user_id) DO NOTHING
                """,
                (
                    self._required(user_id, "user_id"),
                    AccountStatus.ACTIVE.value,
                    1,
                    occurred_at,
                    self.tenant_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_security_events ( event_id, user_id, event_type,
                severity, summary, occurred_at, tenant_id) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    self._required(user_id, "user_id"),
                    self._required(event_type, "event_type"),
                    SecuritySeverity(severity).value,
                    self._required(summary, "summary")[:1000],
                    occurred_at,
                    self.tenant_id,
                ),
            )
        return event_id

    def get_account_security_state(self, *, user_id: str) -> AccountSecurityState:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_accounts WHERE user_id=%s AND tenant_id=%s
                """,
                (self._required(user_id, "user_id"), self.tenant_id),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("account not found")
        return self._row_to_account(row)

    def freeze_account(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        expected_account_version: int,
    ) -> Tuple[AccountFreeze, bool]:
        """Atomically recheck and freeze the authenticated account once."""
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        user_id = self._required(user_id, "user_id")
        expected_account_version = int(expected_account_version)
        fingerprint = self._fingerprint(
            {
                "idempotency_key": idempotency_key,
                "user_id": user_id,
                "expected_account_version": expected_account_version,
            }
        )
        with self._connect(
            locks=(f"account:{user_id}", f"freeze-operation:{idempotency_key}")
        ) as conn:
            existing = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_account_freezes WHERE idempotency_key=%s AND
                tenant_id=%s
                """,
                (idempotency_key, self.tenant_id),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise OperationIdempotencyConflictError(
                        "idempotency key was reused with different freeze content"
                    )
                return self._row_to_account_freeze(existing), False
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_accounts WHERE user_id=%s AND tenant_id=%s
                """,
                (user_id, self.tenant_id),
            ).fetchone()
            if row is None:
                raise BusinessObjectNotFoundError("account not found")
            account = self._row_to_account(row)
            if account.version != expected_account_version:
                raise StaleAccountVersionError("account changed after security check")
            if account.status is not AccountStatus.ACTIVE:
                raise AccountNotFreezableError(
                    f"account status {account.status.value} cannot be frozen"
                )
            freeze_id = uuid.uuid4().hex
            now = self._now()
            next_version = account.version + 1
            conn.execute(
                """
                UPDATE dialogpilot_app.customer_accounts SET status=%s,version=%s,updated_at=%s WHERE
                user_id=%s AND tenant_id=%s
                """,
                (
                    AccountStatus.FROZEN.value,
                    next_version,
                    now,
                    user_id,
                    self.tenant_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO dialogpilot_app.customer_account_freezes (
                freeze_id,idempotency_key,request_fingerprint,user_id,
                status,account_version,created_at, tenant_id) VALUES (%s,%s,%s,%s,%s,%s,%s, %s)
                """,
                (
                    freeze_id,
                    idempotency_key,
                    fingerprint,
                    user_id,
                    AccountStatus.FROZEN.value,
                    next_version,
                    now,
                    self.tenant_id,
                ),
            )
            created = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_account_freezes WHERE freeze_id=%s AND
                tenant_id=%s
                """,
                (freeze_id, self.tenant_id),
            ).fetchone()
        return self._row_to_account_freeze(created), True

    def get_account_freeze_for_operation(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> AccountFreeze:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dialogpilot_app.customer_account_freezes WHERE user_id=%s AND
                idempotency_key=%s AND tenant_id=%s
                """,
                (
                    self._required(user_id, "user_id"),
                    self._required(idempotency_key, "idempotency_key"),
                    self.tenant_id,
                ),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("account freeze operation not found")
        return self._row_to_account_freeze(row)

    def list_security_events(
        self,
        *,
        user_id: str,
        severity: Optional[SecuritySeverity] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """只返回指定可信用户的最近安全事件。"""
        limit = min(max(int(limit), 1), 20)
        clauses = ["tenant_id = %s", "user_id = %s"]
        params: List[Any] = [self.tenant_id, self._required(user_id, "user_id")]
        if severity is not None:
            clauses.append("severity = %s")
            params.append(SecuritySeverity(severity).value)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_id, event_type, severity, summary, occurred_at "
                f"FROM dialogpilot_app.customer_security_events WHERE {' AND '.join(clauses)} "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT %s",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def _eligibility(self, order: Order, *, has_refund: bool) -> RefundEligibility:
        reason_code = "eligible"
        eligible = True
        if has_refund:
            eligible, reason_code = False, "refund_already_requested"
        elif order.status is not OrderStatus.DELIVERED:
            eligible, reason_code = False, f"order_status_{order.status.value}"
        elif not order.refundable_until:
            eligible, reason_code = False, "refund_window_missing"
        elif self._clock().astimezone(timezone.utc) > self._parse_time(
            order.refundable_until
        ):
            eligible, reason_code = False, "refund_window_expired"
        return RefundEligibility(
            order_id=order.order_id,
            eligible=eligible,
            reason_code=reason_code,
            amount_minor=order.amount_minor,
            currency=order.currency,
            order_version=order.version,
            refundable_until=order.refundable_until,
        )

    @contextmanager
    def _connect(self, *, locks: tuple[str, ...] = ()):
        with (
            self.pool.transaction() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            for key in sorted(set(locks)):
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (json.dumps((self.tenant_id, key)),),
                )
            yield cursor

    @staticmethod
    def _row_to_order(row: Mapping[str, Any]) -> Order:
        return Order(
            order_id=row["order_id"],
            user_id=row["user_id"],
            item_name=row["item_name"],
            amount_minor=int(row["amount_minor"]),
            currency=row["currency"],
            status=OrderStatus(row["status"]),
            refundable_until=row["refundable_until"],
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            shipping_address=str(row["shipping_address"] or ""),
        )

    @staticmethod
    def _row_to_refund(row: Mapping[str, Any]) -> RefundRequest:
        return RefundRequest(
            refund_id=row["refund_id"],
            order_id=row["order_id"],
            user_id=row["user_id"],
            reason=row["reason"],
            amount_minor=int(row["amount_minor"]),
            currency=row["currency"],
            status=RefundStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_cancellation(row: Mapping[str, Any]) -> OrderCancellation:
        return OrderCancellation(
            cancellation_id=row["cancellation_id"],
            order_id=row["order_id"],
            user_id=row["user_id"],
            status=row["status"],
            order_version=int(row["order_version"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_address_change(row: Mapping[str, Any]) -> ShippingAddressChange:
        return ShippingAddressChange(
            change_id=row["change_id"],
            order_id=row["order_id"],
            user_id=row["user_id"],
            new_address=row["new_address"],
            status=row["status"],
            order_version=int(row["order_version"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_account(row: Mapping[str, Any]) -> AccountSecurityState:
        return AccountSecurityState(
            user_id=row["user_id"],
            status=AccountStatus(row["status"]),
            version=int(row["version"]),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_account_freeze(row: Mapping[str, Any]) -> AccountFreeze:
        return AccountFreeze(
            freeze_id=row["freeze_id"],
            user_id=row["user_id"],
            status=row["status"],
            account_version=int(row["account_version"]),
            created_at=row["created_at"],
        )

    def _now(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _required(value: str, field: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError(f"{field} must not be blank")
        return cleaned

    @staticmethod
    def _fingerprint(values: Dict[str, Any]) -> str:
        raw = json.dumps(
            values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
