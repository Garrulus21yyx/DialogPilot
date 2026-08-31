"""持久化用户可见回答的选择、送达和已读事实。

HTTP 返回只能证明服务端选择了文本。真正的客户端送达事实由认证客户端通过
ACK 推进；会话内 ``seq`` 则为断线续取提供稳定游标。
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List


class DeliveryStatus(str, Enum):
    SELECTED = "selected"
    DELIVERED = "delivered"
    READ = "read"


class ResponseDeliveryError(Exception):
    """回答送达领域失败的基类。"""


class ResponseNotFoundError(ResponseDeliveryError):
    """回答不存在，或不属于当前认证用户。"""


@dataclass(frozen=True)
class ResponseDelivery:
    response_id: str
    user_id: str
    conv_id: str
    request_id: str
    seq: int
    response_text: str
    status: DeliveryStatus
    selected_at: str
    delivered_at: str | None
    read_at: str | None

    def to_public_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("user_id", None)
        data["status"] = self.status.value
        return data


class ResponseDeliveryService:
    """回答身份、会话序号和单调 ACK 状态的唯一 Owner。"""

    def __init__(self, database_path: str):
        self._path = Path(database_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def select_response(
        self,
        *,
        user_id: str,
        conv_id: str,
        request_id: str,
        response_text: str,
    ) -> ResponseDelivery:
        """在发送前记录服务端选定文本，并分配连续的会话局部序号。"""
        user_id = self._required(user_id, "user_id")
        conv_id = self._required(conv_id, "conv_id")
        request_id = self._required(request_id, "request_id")
        response_text = self._required(response_text, "response_text")
        response_id = uuid.uuid4().hex
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                FROM response_deliveries WHERE user_id = ? AND conv_id = ?
                """,
                (user_id, conv_id),
            ).fetchone()
            seq = int(row["next_seq"])
            conn.execute(
                """
                INSERT INTO response_deliveries (
                    response_id, user_id, conv_id, request_id, seq, response_text,
                    status, selected_at, delivered_at, read_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    response_id, user_id, conv_id, request_id, seq, response_text,
                    DeliveryStatus.SELECTED.value, now,
                ),
            )
            stored = conn.execute(
                "SELECT * FROM response_deliveries WHERE response_id = ?", (response_id,)
            ).fetchone()
        return self._row_to_delivery(stored)

    def acknowledge(
        self,
        response_id: str,
        *,
        user_id: str,
        status: DeliveryStatus,
    ) -> ResponseDelivery:
        """幂等推进 ``selected -> delivered -> read``；READ 隐含已送达。"""
        response_id = self._required(response_id, "response_id")
        user_id = self._required(user_id, "user_id")
        target = DeliveryStatus(status)
        if target is DeliveryStatus.SELECTED:
            raise ValueError("client ACK target must be delivered or read")

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM response_deliveries WHERE response_id = ? AND user_id = ?",
                (response_id, user_id),
            ).fetchone()
            if row is None:
                raise ResponseNotFoundError(f"response not found: {response_id}")
            current = DeliveryStatus(row["status"])
            now = self._now()
            if target is DeliveryStatus.READ and current is not DeliveryStatus.READ:
                conn.execute(
                    """
                    UPDATE response_deliveries
                    SET status = ?, delivered_at = COALESCE(delivered_at, ?), read_at = ?
                    WHERE response_id = ?
                    """,
                    (DeliveryStatus.READ.value, now, now, response_id),
                )
            elif target is DeliveryStatus.DELIVERED and current is DeliveryStatus.SELECTED:
                conn.execute(
                    """
                    UPDATE response_deliveries SET status = ?, delivered_at = ?
                    WHERE response_id = ?
                    """,
                    (DeliveryStatus.DELIVERED.value, now, response_id),
                )
            # 重复 ACK 或 READ 后迟到的 DELIVERED ACK 都是单调幂等成功。
            updated = conn.execute(
                "SELECT * FROM response_deliveries WHERE response_id = ?", (response_id,)
            ).fetchone()
        return self._row_to_delivery(updated)

    def list_after(
        self,
        *,
        user_id: str,
        conv_id: str,
        after_seq: int = 0,
        limit: int = 100,
    ) -> List[ResponseDelivery]:
        """按会话序号返回断线期间遗漏的服务端已选回答。"""
        user_id = self._required(user_id, "user_id")
        conv_id = self._required(conv_id, "conv_id")
        after_seq = max(0, int(after_seq))
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM response_deliveries
                WHERE user_id = ? AND conv_id = ? AND seq > ?
                ORDER BY seq ASC LIMIT ?
                """,
                (user_id, conv_id, after_seq, limit),
            ).fetchall()
        return [self._row_to_delivery(row) for row in rows]

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM response_deliveries GROUP BY status"
            ).fetchall()
        counts = {status.value: 0 for status in DeliveryStatus}
        counts.update({str(row["status"]): int(row["count"]) for row in rows})
        return counts

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS response_deliveries (
                    response_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    seq INTEGER NOT NULL CHECK(seq > 0),
                    response_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('selected', 'delivered', 'read')),
                    selected_at TEXT NOT NULL,
                    delivered_at TEXT,
                    read_at TEXT,
                    UNIQUE(user_id, conv_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_response_delivery_replay
                    ON response_deliveries(user_id, conv_id, seq);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _row_to_delivery(row: sqlite3.Row) -> ResponseDelivery:
        return ResponseDelivery(
            response_id=row["response_id"], user_id=row["user_id"],
            conv_id=row["conv_id"], request_id=row["request_id"], seq=int(row["seq"]),
            response_text=row["response_text"], status=DeliveryStatus(row["status"]),
            selected_at=row["selected_at"], delivered_at=row["delivered_at"],
            read_at=row["read_at"],
        )

    @staticmethod
    def _required(value: str, field: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError(f"{field} must not be blank")
        return cleaned

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
