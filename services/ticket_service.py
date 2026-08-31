"""持久化人工工单及其闭合状态迁移合同。

``TicketService`` 是工单身份、幂等语义、当前状态和审计事件的唯一 Owner。
API 层只请求操作，不能自行放宽状态机或解释数据库行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


class TicketStatus(str, Enum):
    """工单生命周期支持的全部状态。"""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    """人工队列使用的业务优先级。"""
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


LEGAL_TRANSITIONS = {
    TicketStatus.OPEN: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
    TicketStatus.IN_PROGRESS: {
        TicketStatus.WAITING_CUSTOMER,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    },
    TicketStatus.WAITING_CUSTOMER: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    },
    TicketStatus.RESOLVED: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
    TicketStatus.CLOSED: set(),
}
# 该表是状态机的权威合同：CLOSED 是终态，其他路径必须显式列出。


class TicketError(Exception):
    """工单领域有类型失败的基类。"""


class TicketNotFoundError(TicketError):
    """目标工单不存在。"""


class InvalidTransitionError(TicketError):
    """请求的状态迁移不属于 ``LEGAL_TRANSITIONS``。"""

    def __init__(self, current: TicketStatus, target: TicketStatus):
        """保留当前/目标状态，便于 API 映射稳定错误结构。"""
        super().__init__(f"illegal ticket transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


class IdempotencyConflictError(TicketError):
    """同一幂等键被用于不同的稳定请求内容。"""


@dataclass(frozen=True)
class Ticket:
    """从持久层读取的不可变工单快照。"""
    ticket_id: str
    idempotency_key: str
    user_id: str
    conv_id: str
    request_id: str
    question: str
    published_response: str
    reason: str
    priority: TicketPriority
    status: TicketStatus
    agent_type: str
    intent: str
    verification_status: str
    assignee: Optional[str]
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        """转换为对外 JSON 结构，并展开领域枚举。"""
        data = asdict(self)
        data["priority"] = self.priority.value
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class TicketOutboxMessage:
    """等待外部人工系统确认接收的不可变工单事件。"""
    event_id: str
    event_type: str
    ticket_id: str
    payload: Dict[str, Any]
    attempts: int
    created_at: str


class TicketWebhookDispatcher:
    """把 outbox 事件投递到外部 CRM webhook；接收端须按 event_id 幂等。"""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0):
        self._url = (url or "").strip()
        if not self._url:
            raise ValueError("ticket dispatch webhook URL must not be blank")
        self._timeout_seconds = max(0.1, float(timeout_seconds))

    def __call__(self, message: TicketOutboxMessage) -> None:
        body = json.dumps({
            "event_id": message.event_id,
            "event_type": message.event_type,
            "ticket_id": message.ticket_id,
            "payload": message.payload,
            "created_at": message.created_at,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": message.event_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= int(response.status) < 300:
                raise RuntimeError(f"ticket webhook returned HTTP {response.status}")


class TicketService:
    """工单持久化、幂等身份与生命周期的权威 Owner。"""

    def __init__(
        self,
        database_path: str,
        *,
        dispatcher: Optional[Callable[[TicketOutboxMessage], None]] = None,
        dispatch_poll_seconds: float = 5.0,
        dispatch_lease_seconds: float = 30.0,
        dispatch_retry_base_seconds: float = 5.0,
        dispatch_retry_max_seconds: float = 300.0,
    ):
        """解析数据库路径、准备父目录并初始化 SQLite schema。"""
        self._path = Path(database_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._dispatcher = dispatcher
        self._dispatch_poll_seconds = max(0.05, float(dispatch_poll_seconds))
        self._dispatch_lease_seconds = max(1.0, float(dispatch_lease_seconds))
        self._dispatch_retry_base_seconds = max(0.05, float(dispatch_retry_base_seconds))
        self._dispatch_retry_max_seconds = max(
            self._dispatch_retry_base_seconds, float(dispatch_retry_max_seconds)
        )
        self._worker_id = uuid.uuid4().hex
        self._worker_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._initialize()

    async def start(self) -> None:
        """配置外部投递器时启动可恢复轮询；未配置时 outbox 保持 pending。"""
        if self._dispatcher is None or self._worker_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._dispatch_loop(), name="ticket-outbox-dispatcher"
        )

    async def close(self) -> None:
        if self._worker_task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        await self._worker_task
        self._worker_task = None
        self._stop_event = None

    def create_ticket(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        conv_id: str,
        request_id: str,
        question: str,
        published_response: str,
        reason: str,
        priority: TicketPriority = TicketPriority.NORMAL,
        agent_type: str = "general",
        intent: str = "other",
        verification_status: str = "unknown",
    ) -> Tuple[Ticket, bool]:
        """幂等创建工单，返回 ``(ticket, created)``。

        同一幂等键和稳定输入返回首个工单；同键不同输入抛出冲突。模型生成的
        ``published_response`` 不参与身份计算，因为安全重试可能产生不同措辞。
        """
        values = {
            "idempotency_key": self._required(idempotency_key, "idempotency_key"),
            "user_id": self._required(user_id, "user_id"),
            "conv_id": self._required(conv_id, "conv_id"),
            "request_id": self._required(request_id, "request_id"),
            "question": self._required(question, "question"),
            "published_response": (published_response or "").strip(),
            "reason": self._required(reason, "reason"),
            "priority": TicketPriority(priority),
            "agent_type": (agent_type or "general").strip(),
            "intent": (intent or "other").strip(),
            "verification_status": (verification_status or "unknown").strip(),
        }
        # 幂等身份描述客户端操作，而不是非确定性的 LLM 输出。安全重试即使
        # 生成措辞不同，也必须解析到首次持久化的人工工单。
        fingerprint = self._fingerprint(
            {
                "idempotency_key": values["idempotency_key"],
                "user_id": values["user_id"],
                "conv_id": values["conv_id"],
                "request_id": values["request_id"],
                "question": values["question"],
            }
        )
        now = self._now()
        ticket_id = uuid.uuid4().hex
        outbox_event_id = uuid.uuid4().hex

        with self._lock, self._connect() as conn:
            # BEGIN IMMEDIATE 在读旧记录与插入新记录之间取得写锁，配合唯一索引
            # 让并发重复请求也只能创建一个工单。
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM tickets WHERE idempotency_key = ?",
                (values["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with different ticket content"
                    )
                return self._row_to_ticket(existing), False

            conn.execute(
                """
                INSERT INTO tickets (
                    ticket_id, idempotency_key, request_fingerprint, user_id,
                    conv_id, request_id, question, published_response, reason,
                    priority, status, agent_type, intent, verification_status,
                    assignee, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    ticket_id,
                    values["idempotency_key"],
                    fingerprint,
                    values["user_id"],
                    values["conv_id"],
                    values["request_id"],
                    values["question"],
                    values["published_response"],
                    values["reason"],
                    values["priority"].value,
                    TicketStatus.OPEN.value,
                    values["agent_type"],
                    values["intent"],
                    values["verification_status"],
                    now,
                    now,
                ),
            )
            self._insert_event(
                conn,
                ticket_id=ticket_id,
                from_status=None,
                to_status=TicketStatus.OPEN,
                actor="system",
                note="ticket created",
                created_at=now,
            )
            outbox_payload = {
                "ticket_id": ticket_id,
                "idempotency_key": values["idempotency_key"],
                "user_id": values["user_id"],
                "conv_id": values["conv_id"],
                "request_id": values["request_id"],
                "question": values["question"],
                "published_response": values["published_response"],
                "reason": values["reason"],
                "priority": values["priority"].value,
                "status": TicketStatus.OPEN.value,
                "agent_type": values["agent_type"],
                "intent": values["intent"],
                "verification_status": values["verification_status"],
                "created_at": now,
            }
            conn.execute(
                """
                INSERT INTO ticket_outbox (
                    event_id, event_type, ticket_id, payload_json, attempts,
                    available_at, claimed_by, lease_until, last_error,
                    delivered_at, created_at
                ) VALUES (?, 'ticket.created', ?, ?, 0, ?, NULL, NULL, NULL, NULL, ?)
                """,
                (
                    outbox_event_id, ticket_id,
                    json.dumps(outbox_payload, ensure_ascii=False, sort_keys=True),
                    now, now,
                ),
            )
            row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
            return self._row_to_ticket(row), True

    def dispatch_once(self) -> bool:
        """认领并投递一个到期事件；返回本次是否处理了事件。"""
        if self._dispatcher is None:
            return False
        message = self._claim_outbox_message()
        if message is None:
            return False
        try:
            self._dispatcher(message)
        except Exception as exc:
            self._mark_outbox_failed(message, exc)
            logger.warning(
                "工单 outbox 投递失败 event_id=%s attempts=%s error=%s",
                message.event_id, message.attempts, type(exc).__name__,
            )
        else:
            self._mark_outbox_delivered(message)
        return True

    def outbox_stats(self) -> Dict[str, Any]:
        """返回真实持久状态；未配置接收端时明确报告 disabled。"""
        now = self._now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered,
                    SUM(CASE WHEN delivered_at IS NULL AND available_at <= ? THEN 1 ELSE 0 END) AS due
                FROM ticket_outbox
                """,
                (now,),
            ).fetchone()
        return {
            "dispatcher_configured": self._dispatcher is not None,
            "worker_running": self._worker_task is not None and not self._worker_task.done(),
            "pending": int(row["pending"] or 0),
            "delivered": int(row["delivered"] or 0),
            "due": int(row["due"] or 0),
        }

    async def _dispatch_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                handled = await asyncio.to_thread(self.dispatch_once)
            except Exception:
                logger.exception("工单 outbox worker 周期失败")
                handled = False
            if handled:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._dispatch_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    def _claim_outbox_message(self) -> Optional[TicketOutboxMessage]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_until = (now_dt + timedelta(seconds=self._dispatch_lease_seconds)).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM ticket_outbox
                WHERE delivered_at IS NULL AND available_at <= ?
                  AND (claimed_by IS NULL OR lease_until <= ?)
                ORDER BY created_at ASC, event_id ASC LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"]) + 1
            conn.execute(
                """
                UPDATE ticket_outbox
                SET claimed_by = ?, lease_until = ?, attempts = ?
                WHERE event_id = ?
                """,
                (self._worker_id, lease_until, attempts, row["event_id"]),
            )
        return TicketOutboxMessage(
            event_id=row["event_id"], event_type=row["event_type"],
            ticket_id=row["ticket_id"], payload=json.loads(row["payload_json"]),
            attempts=attempts, created_at=row["created_at"],
        )

    def _mark_outbox_delivered(self, message: TicketOutboxMessage) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE ticket_outbox
                SET delivered_at = ?, claimed_by = NULL, lease_until = NULL, last_error = NULL
                WHERE event_id = ? AND claimed_by = ? AND delivered_at IS NULL
                """,
                (self._now(), message.event_id, self._worker_id),
            )

    def _mark_outbox_failed(self, message: TicketOutboxMessage, error: Exception) -> None:
        delay = min(
            self._dispatch_retry_max_seconds,
            self._dispatch_retry_base_seconds * (2 ** max(0, message.attempts - 1)),
        )
        available_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE ticket_outbox
                SET available_at = ?, claimed_by = NULL, lease_until = NULL, last_error = ?
                WHERE event_id = ? AND claimed_by = ? AND delivered_at IS NULL
                """,
                (
                    available_at, f"{type(error).__name__}: {str(error)[:500]}",
                    message.event_id, self._worker_id,
                ),
            )

    def get_ticket(self, ticket_id: str) -> Ticket:
        """按 ID 读取工单；不存在时使用领域异常而不是返回空值。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
        if row is None:
            raise TicketNotFoundError(f"ticket not found: {ticket_id}")
        return self._row_to_ticket(row)

    def get_ticket_view(self, ticket_id: str) -> Dict[str, Any]:
        """组合当前工单快照及按时间排序的审计事件投影。"""
        ticket = self.get_ticket(ticket_id)
        data = ticket.to_dict()
        data["events"] = self.get_events(ticket_id)
        return data

    def list_tickets(
        self,
        *,
        user_id: Optional[str] = None,
        status: Optional[TicketStatus] = None,
        limit: int = 50,
    ) -> List[Ticket]:
        """按可选用户和状态筛选工单，并对分页上限做硬限制。"""
        limit = max(1, min(int(limit), 200))
        clauses: List[str] = []
        params: List[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(TicketStatus(status).value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tickets{where} ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_ticket(row) for row in rows]

    def list_active_tickets(
        self,
        *,
        user_id: str,
        limit: int = 3,
    ) -> List[Ticket]:
        """读取用户尚未进入 CLOSED 终态的工单，作为客服事项的权威投影。"""
        user_id = self._required(user_id, "user_id")
        limit = max(1, min(int(limit), 20))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tickets
                WHERE user_id = ? AND status != ?
                ORDER BY updated_at DESC, created_at DESC, ticket_id ASC
                LIMIT ?
                """,
                (user_id, TicketStatus.CLOSED.value, limit),
            ).fetchall()
        return [self._row_to_ticket(row) for row in rows]

    def transition(
        self,
        ticket_id: str,
        target: TicketStatus,
        *,
        actor: str,
        note: str = "",
        assignee: Optional[str] = None,
    ) -> Ticket:
        """原子执行合法状态迁移，并在同一事务写入审计事件。"""
        target = TicketStatus(target)
        actor = self._required(actor, "actor")
        note = (note or "").strip()[:1000]
        assignee = (assignee or "").strip() or None

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise TicketNotFoundError(f"ticket not found: {ticket_id}")
            current = TicketStatus(row["status"])
            if target is current:
                # 重复提交同一目标状态是幂等成功，不制造重复审计事件。
                return self._row_to_ticket(row)
            if target not in LEGAL_TRANSITIONS[current]:
                raise InvalidTransitionError(current, target)

            now = self._now()
            next_assignee = assignee or row["assignee"]
            if target is TicketStatus.IN_PROGRESS and not next_assignee:
                next_assignee = actor
            conn.execute(
                "UPDATE tickets SET status = ?, assignee = ?, updated_at = ? WHERE ticket_id = ?",
                (target.value, next_assignee, now, ticket_id),
            )
            self._insert_event(
                conn,
                ticket_id=ticket_id,
                from_status=current,
                to_status=target,
                actor=actor,
                note=note,
                created_at=now,
            )
            updated = conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            return self._row_to_ticket(updated)

    def get_events(self, ticket_id: str) -> List[Dict[str, Any]]:
        """返回不可变审计历史，缺失父工单时确定性失败。"""
        # 先验证父对象，避免把“没有事件”和“工单不存在”混为一谈。
        self.get_ticket(ticket_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, from_status, to_status, actor, note, created_at
                FROM ticket_events WHERE ticket_id = ? ORDER BY event_id ASC
                """,
                (ticket_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _initialize(self) -> None:
        """幂等创建当前状态表、审计表及主要查询索引。"""
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    published_response TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    assignee TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tickets_user_created
                    ON tickets(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tickets_status_created
                    ON tickets(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS ticket_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id)
                );

                CREATE TABLE IF NOT EXISTS ticket_outbox (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    available_at TEXT NOT NULL,
                    claimed_by TEXT,
                    lease_until TEXT,
                    last_error TEXT,
                    delivered_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ticket_outbox_due
                    ON ticket_outbox(delivered_at, available_at, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """创建启用外键、WAL 和忙等待的短生命周期连接。"""
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        *,
        ticket_id: str,
        from_status: Optional[TicketStatus],
        to_status: TicketStatus,
        actor: str,
        note: str,
        created_at: str,
    ) -> None:
        """在调用方现有事务中追加一条状态迁移审计事件。"""
        conn.execute(
            """
            INSERT INTO ticket_events (
                ticket_id, from_status, to_status, actor, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                from_status.value if from_status else None,
                to_status.value,
                actor,
                note,
                created_at,
            ),
        )

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> Ticket:
        """在持久化边界把字符串恢复为领域枚举和不可变对象。"""
        return Ticket(
            ticket_id=row["ticket_id"],
            idempotency_key=row["idempotency_key"],
            user_id=row["user_id"],
            conv_id=row["conv_id"],
            request_id=row["request_id"],
            question=row["question"],
            published_response=row["published_response"],
            reason=row["reason"],
            priority=TicketPriority(row["priority"]),
            status=TicketStatus(row["status"]),
            agent_type=row["agent_type"],
            intent=row["intent"],
            verification_status=row["verification_status"],
            assignee=row["assignee"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _fingerprint(values: Dict[str, Any]) -> str:
        """对稳定、排序后的操作字段计算可复现 SHA-256 指纹。"""
        serializable = {
            key: value.value if isinstance(value, Enum) else value
            for key, value in values.items()
        }
        raw = json.dumps(serializable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _required(value: str, field: str) -> str:
        """统一执行必填字符串的去空白校验。"""
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError(f"{field} must not be blank")
        return cleaned

    @staticmethod
    def _now() -> str:
        """生成带时区的 UTC ISO-8601 时间戳。"""
        return datetime.now(timezone.utc).isoformat()
