"""订单、退款与账户安全事实的本地业务 Owner。

该模块是可替换外部 CRM/订单平台的 SQLite 沙箱：工具层只能读取或请求这里
定义的业务操作，不能自行判断退款资格、制造订单状态或宣称副作用已提交。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


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


class CustomerOperationsService:
    """订单、退款资格、退款申请和安全事件的唯一事实 Owner。"""

    def __init__(
        self,
        database_path: str,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._path = Path(database_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._initialize()

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
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT version, created_at FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            version = int(existing["version"]) + 1 if existing else 1
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, item_name, amount_minor, currency, status,
                    refundable_until, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    user_id=excluded.user_id, item_name=excluded.item_name,
                    amount_minor=excluded.amount_minor, currency=excluded.currency,
                    status=excluded.status, refundable_until=excluded.refundable_until,
                    version=excluded.version, updated_at=excluded.updated_at
                """,
                (
                    order_id, user_id, item_name, amount_minor, currency, status.value,
                    refundable_until, version, created_at, now,
                ),
            )
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._row_to_order(row)

    def get_order_for_user(self, *, user_id: str, order_id: str) -> Order:
        """按可信用户读取订单；不存在与越权读取使用同一种失败。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
                (self._required(order_id, "order_id"), self._required(user_id, "user_id")),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("order not found")
        return self._row_to_order(row)

    def check_refund_eligibility(self, *, user_id: str, order_id: str) -> RefundEligibility:
        """由订单状态、窗口与已有退款共同计算确定性的资格快照。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
                (self._required(order_id, "order_id"), self._required(user_id, "user_id")),
            ).fetchone()
            if row is None:
                raise BusinessObjectNotFoundError("order not found")
            existing = conn.execute(
                "SELECT refund_id FROM refund_requests WHERE order_id = ?", (order_id,)
            ).fetchone()
        order = self._row_to_order(row)
        return self._eligibility(order, has_refund=existing is not None)

    def get_refund_status(self, *, user_id: str, order_id: str) -> RefundRequest:
        """Read the current refund request for an authenticated user's order."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM refund_requests WHERE order_id=? AND user_id=?",
                (self._required(order_id, "order_id"),
                 self._required(user_id, "user_id")),
            ).fetchone()
        if row is None:
            raise BusinessObjectNotFoundError("refund request not found")
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
        fingerprint = self._fingerprint({
            "idempotency_key": idempotency_key,
            "user_id": user_id,
            "order_id": order_id,
            "expected_order_version": expected_order_version,
            "reason": reason,
        })
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM refund_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise OperationIdempotencyConflictError(
                        "idempotency key was reused with different refund content"
                    )
                return self._row_to_refund(existing), False

            order_row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
                (order_id, user_id),
            ).fetchone()
            if order_row is None:
                raise BusinessObjectNotFoundError("order not found")
            order = self._row_to_order(order_row)
            if order.version != expected_order_version:
                raise StaleOrderVersionError("order changed after eligibility check")
            prior = conn.execute(
                "SELECT refund_id FROM refund_requests WHERE order_id = ?", (order_id,)
            ).fetchone()
            eligibility = self._eligibility(order, has_refund=prior is not None)
            if not eligibility.eligible:
                raise RefundNotEligibleError(eligibility.reason_code)

            refund_id = uuid.uuid4().hex
            now = self._now()
            conn.execute(
                """
                INSERT INTO refund_requests (
                    refund_id, idempotency_key, request_fingerprint, order_id,
                    user_id, reason, amount_minor, currency, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refund_id, idempotency_key, fingerprint, order_id, user_id,
                    reason, order.amount_minor, order.currency,
                    RefundStatus.REQUESTED.value, now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM refund_requests WHERE refund_id = ?", (refund_id,)
            ).fetchone()
        return self._row_to_refund(row), True

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
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO security_events (
                    event_id, user_id, event_type, severity, summary, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, self._required(user_id, "user_id"),
                    self._required(event_type, "event_type"),
                    SecuritySeverity(severity).value,
                    self._required(summary, "summary")[:1000], occurred_at,
                ),
            )
        return event_id

    def list_security_events(
        self,
        *,
        user_id: str,
        severity: Optional[SecuritySeverity] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """只返回指定可信用户的最近安全事件。"""
        limit = min(max(int(limit), 1), 20)
        clauses = ["user_id = ?"]
        params: List[Any] = [self._required(user_id, "user_id")]
        if severity is not None:
            clauses.append("severity = ?")
            params.append(SecuritySeverity(severity).value)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT event_id, event_type, severity, summary, occurred_at "
                f"FROM security_events WHERE {' AND '.join(clauses)} "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
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
        elif self._clock().astimezone(timezone.utc) > self._parse_time(order.refundable_until):
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

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    refundable_until TEXT,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_orders_user_updated
                    ON orders(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS refund_requests (
                    refund_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    order_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_refunds_user_created
                    ON refund_requests(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS security_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_security_user_occurred
                    ON security_events(user_id, occurred_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        return Order(
            order_id=row["order_id"], user_id=row["user_id"], item_name=row["item_name"],
            amount_minor=int(row["amount_minor"]), currency=row["currency"],
            status=OrderStatus(row["status"]), refundable_until=row["refundable_until"],
            version=int(row["version"]), created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_refund(row: sqlite3.Row) -> RefundRequest:
        return RefundRequest(
            refund_id=row["refund_id"], order_id=row["order_id"], user_id=row["user_id"],
            reason=row["reason"], amount_minor=int(row["amount_minor"]), currency=row["currency"],
            status=RefundStatus(row["status"]), created_at=row["created_at"],
            updated_at=row["updated_at"],
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
        raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
