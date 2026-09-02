"""持久化用户可见回答的选择、送达和已读事实。

HTTP 返回只能证明服务端选择了文本。真正的客户端送达事实由认证客户端通过
ACK 推进；会话内 ``seq`` 则为断线续取提供稳定游标。
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
import json
from dataclasses import asdict, dataclass, field
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


class ResponseDeliveryWritesFrozen(ResponseDeliveryError):
    """Legacy selection/ACK is fenced for PostgreSQL cutover."""


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
    identity_metadata: Dict[str, str] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("user_id", None)
        data.pop("identity_metadata", None)
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
        identity_metadata: Dict[str, str] | None = None,
    ) -> ResponseDelivery:
        """在发送前记录服务端选定文本，并分配连续的会话局部序号。"""
        user_id = self._required(user_id, "user_id")
        conv_id = self._required(conv_id, "conv_id")
        request_id = self._required(request_id, "request_id")
        response_text = self._required(response_text, "response_text")
        identity_metadata = {
            str(key): str(value)
            for key, value in dict(identity_metadata or {}).items()
        }
        response_id = uuid.uuid4().hex
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_writable(conn)
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
                    status, selected_at, delivered_at, read_at, identity_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    response_id, user_id, conv_id, request_id, seq, response_text,
                    DeliveryStatus.SELECTED.value, now,
                    json.dumps(identity_metadata, ensure_ascii=False, sort_keys=True),
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
            self._assert_writable(conn)
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

    def writer_state(self) -> tuple[str, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state, freeze_id FROM response_delivery_writer_control "
                "WHERE singleton = 1"
            ).fetchone()
        return str(row["state"]), row["freeze_id"]

    def freeze_writes(self, freeze_id: str) -> None:
        freeze_id = self._required(freeze_id, "freeze_id")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state, current_id = self._writer_state(conn)
            if state == "RETIRED":
                raise ResponseDeliveryWritesFrozen("legacy writer is retired")
            if state == "FROZEN" and current_id == freeze_id:
                return
            if state != "ACTIVE":
                raise ResponseDeliveryWritesFrozen(
                    f"legacy writer already frozen by {current_id}"
                )
            conn.execute(
                "UPDATE response_delivery_writer_control "
                "SET state='FROZEN', freeze_id=?, updated_at=? WHERE singleton=1",
                (freeze_id, self._now()),
            )

    def unfreeze_before_cutover(self, freeze_id: str) -> None:
        freeze_id = self._required(freeze_id, "freeze_id")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state, current_id = self._writer_state(conn)
            if state == "ACTIVE":
                return
            if state != "FROZEN" or current_id != freeze_id:
                raise ResponseDeliveryWritesFrozen(
                    "only the matching pre-cutover freeze can be released"
                )
            conn.execute(
                "UPDATE response_delivery_writer_control "
                "SET state='ACTIVE', freeze_id=NULL, updated_at=? WHERE singleton=1",
                (self._now(),),
            )

    def retire_writes(self, freeze_id: str) -> None:
        freeze_id = self._required(freeze_id, "freeze_id")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state, current_id = self._writer_state(conn)
            if state == "RETIRED" and current_id == freeze_id:
                return
            if state != "FROZEN" or current_id != freeze_id:
                raise ResponseDeliveryWritesFrozen(
                    "legacy writer must have the matching freeze before retirement"
                )
            conn.execute(
                "UPDATE response_delivery_writer_control "
                "SET state='RETIRED', updated_at=? WHERE singleton=1",
                (self._now(),),
            )

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
                    identity_metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(user_id, conv_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_response_delivery_replay
                    ON response_deliveries(user_id, conv_id, seq);
                CREATE TABLE IF NOT EXISTS response_delivery_writer_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    state TEXT NOT NULL CHECK(state IN ('ACTIVE', 'FROZEN', 'RETIRED')),
                    freeze_id TEXT,
                    updated_at TEXT NOT NULL,
                    CHECK (
                        (state = 'ACTIVE' AND freeze_id IS NULL)
                        OR (state IN ('FROZEN', 'RETIRED') AND freeze_id IS NOT NULL)
                    )
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO response_delivery_writer_control "
                "(singleton, state, freeze_id, updated_at) VALUES (1,'ACTIVE',NULL,?)",
                (self._now(),),
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(response_deliveries)").fetchall()
            }
            if "identity_metadata_json" not in columns:
                conn.execute(
                    "ALTER TABLE response_deliveries "
                    "ADD COLUMN identity_metadata_json TEXT NOT NULL DEFAULT '{}'"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _writer_state(conn: sqlite3.Connection) -> tuple[str, str | None]:
        row = conn.execute(
            "SELECT state, freeze_id FROM response_delivery_writer_control "
            "WHERE singleton = 1"
        ).fetchone()
        return str(row["state"]), row["freeze_id"]

    @classmethod
    def _assert_writable(cls, conn: sqlite3.Connection) -> None:
        state, freeze_id = cls._writer_state(conn)
        if state != "ACTIVE":
            raise ResponseDeliveryWritesFrozen(
                f"legacy response writer is {state.lower()} ({freeze_id})"
            )

    @staticmethod
    def _row_to_delivery(row: sqlite3.Row) -> ResponseDelivery:
        return ResponseDelivery(
            response_id=row["response_id"], user_id=row["user_id"],
            conv_id=row["conv_id"], request_id=row["request_id"], seq=int(row["seq"]),
            response_text=row["response_text"], status=DeliveryStatus(row["status"]),
            selected_at=row["selected_at"], delivered_at=row["delivered_at"],
            read_at=row["read_at"],
            identity_metadata=json.loads(row["identity_metadata_json"] or "{}"),
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
