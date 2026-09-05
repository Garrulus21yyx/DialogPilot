"""Legacy ReAct checkpoint storage pending consumer removal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, Optional, Tuple

from core.schema_version_registry import SchemaCompatibilityError, SchemaVersionRegistry


class RunStatus(str, Enum):
    """持久 Run 支持的闭合状态代数。"""

    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    TOOL_ERROR = "tool_error"
    MAX_STEPS = "max_steps"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_RUN_STATUSES = frozenset({
    RunStatus.COMPLETED,
    RunStatus.BLOCKED,
    RunStatus.TOOL_ERROR,
    RunStatus.MAX_STEPS,
    RunStatus.CANCELLED,
    RunStatus.EXPIRED,
})


class RunStoreError(Exception):
    """RunStore 领域错误基类。"""


class RunNotFoundError(RunStoreError):
    pass


class RunAccessDeniedError(RunStoreError):
    pass


class RunTransitionError(RunStoreError):
    pass


class RunVersionConflictError(RunStoreError):
    pass


@dataclass(frozen=True)
class RunCheckpoint:
    run_id: str
    request_id: str
    user_id: str
    conv_id: str
    agent_type: str
    task_id: str
    bundle_version: str
    status: RunStatus
    system: str
    messages: Tuple[Dict[str, Any], ...]
    execution_context: Dict[str, Any]
    runtime: Dict[str, Any]
    step: int
    max_steps: int
    tool_call_ids: Tuple[str, ...]
    pending: Dict[str, Any]
    result: Dict[str, Any]
    version: int
    expires_at: str
    created_at: str
    updated_at: str


class RunStore:
    """Legacy Run checkpoint and approval CAS storage."""

    def __init__(
        self,
        database_path: str,
        *,
        approval_ttl_s: float = 900.0,
        recovery_grace_s: float = 30.0,
    ):
        self._path = Path(database_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._approval_ttl_s = max(1.0, float(approval_ttl_s))
        self._recovery_grace_s = max(0.0, float(recovery_grace_s))
        self._lock = threading.RLock()
        self._initialize()
        self._restrict_storage_permissions()

    def create(
        self,
        *,
        run_id: str,
        request_id: str,
        user_id: str,
        conv_id: str,
        agent_type: str,
        task_id: str,
        bundle_version: str,
        system: str,
        messages: Tuple[Dict[str, Any], ...],
        execution_context: Dict[str, Any],
        max_steps: int,
    ) -> RunCheckpoint:
        """为一次新 ReAct 运行创建唯一起点；重复 run_id 不覆盖旧状态。"""
        values = {
            "run_id": self._required(run_id, "run_id", 160),
            "request_id": self._required(request_id, "request_id", 200),
            "user_id": self._required(user_id, "user_id", 200),
            "conv_id": self._required(conv_id, "conv_id", 200),
            "agent_type": self._required(agent_type, "agent_type", 80),
            "task_id": self._required(task_id, "task_id", 160),
            "bundle_version": self._required(bundle_version or "unversioned", "bundle_version", 160),
        }
        now = self._now()
        expires = self._future(self._approval_ttl_s)
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO react_runs (
                        run_id, request_id, user_id, conv_id, agent_type, task_id,
                        bundle_version, status, system_text, messages_json,
                        execution_context_json, runtime_json, step, max_steps,
                        tool_call_ids_json, pending_json, result_json, version,
                        expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 0, ?, '[]', '{}', '{}', 0, ?, ?, ?)
                    """,
                    (
                        values["run_id"], values["request_id"], values["user_id"],
                        values["conv_id"], values["agent_type"], values["task_id"],
                        values["bundle_version"], RunStatus.RUNNING.value,
                        self._sanitize_checkpoint_value(str(system)),
                        self._json(self._sanitize_checkpoint_value(list(messages))),
                        self._json(self._sanitize_checkpoint_value({
                            **dict(execution_context),
                            "_checkpoint_schema_version": (
                                SchemaVersionRegistry.agent_checkpoint.current_version
                            ),
                            "_agent_code_version": (
                                SchemaVersionRegistry.agent_code.current_version
                            ),
                        })),
                        int(max_steps), expires, now, now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RunTransitionError("run_id already exists") from exc
            row = conn.execute("SELECT * FROM react_runs WHERE run_id=?", (values["run_id"],)).fetchone()
            return self._row(row)

    def checkpoint(
        self,
        *,
        run_id: str,
        expected_version: int,
        status: RunStatus,
        messages: Tuple[Dict[str, Any], ...],
        runtime: Dict[str, Any],
        step: int,
        tool_call_ids: Tuple[str, ...],
        pending: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> RunCheckpoint:
        """以版本 CAS 持久一次状态迁移，防止并发 resume 互相覆盖。"""
        status = RunStatus(status)
        now = self._now()
        expires = self._future(self._approval_ttl_s)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()
            if current is None:
                raise RunNotFoundError(run_id)
            current_status = RunStatus(current["status"])
            if current_status in TERMINAL_RUN_STATUSES:
                raise RunTransitionError(f"terminal run cannot transition: {current_status.value}")
            if int(current["version"]) != int(expected_version):
                raise RunVersionConflictError("run checkpoint version changed")
            updated = conn.execute(
                """
                UPDATE react_runs SET status=?, messages_json=?, runtime_json=?, step=?,
                    tool_call_ids_json=?, pending_json=?, result_json=?, version=version+1,
                    expires_at=?, updated_at=?
                WHERE run_id=? AND version=?
                """,
                (
                    status.value,
                    self._json(self._sanitize_checkpoint_value(list(messages))),
                    self._json(self._sanitize_checkpoint_value(runtime)), int(step),
                    self._json(list(tool_call_ids)),
                    self._json(self._sanitize_checkpoint_value(pending or {})),
                    self._json(self._sanitize_checkpoint_value(result or {})),
                    expires, now, run_id, int(expected_version),
                ),
            )
            if updated.rowcount != 1:
                raise RunVersionConflictError("run checkpoint version changed")
            row = conn.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()
            return self._row(row)

    def get(self, run_id: str) -> RunCheckpoint:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            checkpoint = self._row(row)
        return self._expire_if_due(checkpoint)

    def get_for_user(self, run_id: str, user_id: str) -> RunCheckpoint:
        checkpoint = self.get(run_id)
        if checkpoint.user_id != str(user_id):
            raise RunAccessDeniedError("run belongs to another authenticated user")
        return checkpoint

    def acquire_waiting(self, run_id: str, user_id: str) -> RunCheckpoint:
        """原子获取等待审批的 Run，同一时刻只允许一个 resume。"""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            checkpoint = self._row(row)
            if checkpoint.user_id != str(user_id):
                raise RunAccessDeniedError("run belongs to another authenticated user")
            checkpoint = self._expire_if_due(checkpoint, conn=conn)
            if checkpoint.status in TERMINAL_RUN_STATUSES:
                return checkpoint
            stale_approved_recovery = (
                checkpoint.status is RunStatus.RUNNING
                and checkpoint.pending.get("phase") == "approved_executing"
            )
            if stale_approved_recovery:
                updated_at = datetime.fromisoformat(checkpoint.updated_at)
                age_s = (datetime.now(timezone.utc) - updated_at).total_seconds()
                if age_s < self._recovery_grace_s:
                    raise RunTransitionError("approved run is still within its execution lease")
            elif checkpoint.status is not RunStatus.WAITING_APPROVAL:
                raise RunTransitionError(f"run is not awaiting approval: {checkpoint.status.value}")
            now = self._now()
            updated = conn.execute(
                """
                UPDATE react_runs SET status=?, version=version+1, updated_at=?
                WHERE run_id=? AND version=? AND status=?
                """,
                (
                    RunStatus.RUNNING.value, now, run_id, checkpoint.version,
                    checkpoint.status.value,
                ),
            )
            if updated.rowcount != 1:
                raise RunVersionConflictError("run was acquired by another resume")
            return self._row(conn.execute(
                "SELECT * FROM react_runs WHERE run_id=?", (run_id,)
            ).fetchone())


    def _expire_if_due(
        self,
        checkpoint: RunCheckpoint,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> RunCheckpoint:
        if checkpoint.status is not RunStatus.WAITING_APPROVAL:
            return checkpoint
        if datetime.fromisoformat(checkpoint.expires_at) > datetime.now(timezone.utc):
            return checkpoint
        owns_connection = conn is None
        connection = conn or self._connect()
        try:
            connection.execute(
                """
                UPDATE react_runs SET status=?, version=version+1, updated_at=?
                WHERE run_id=? AND version=? AND status=?
                """,
                (
                    RunStatus.EXPIRED.value, self._now(), checkpoint.run_id,
                    checkpoint.version, RunStatus.WAITING_APPROVAL.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM react_runs WHERE run_id=?", (checkpoint.run_id,)
            ).fetchone()
            if owns_connection:
                connection.commit()
            return self._row(row)
        finally:
            if owns_connection:
                connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS react_runs (
                    run_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    bundle_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    system_text TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    execution_context_json TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    max_steps INTEGER NOT NULL,
                    tool_call_ids_json TEXT NOT NULL,
                    pending_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_react_runs_user_updated
                    ON react_runs(user_id, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        self._restrict_storage_permissions()
        return conn

    def _restrict_storage_permissions(self) -> None:
        """Keep the checkpoint DB and SQLite WAL/SHM readable only by its owner."""
        try:
            for path in (
                self._path,
                Path(f"{self._path}-wal"),
                Path(f"{self._path}-shm"),
            ):
                if path.exists():
                    os.chmod(path, 0o600)
        except OSError as exc:
            raise RunStoreError("checkpoint file permissions cannot be restricted") from exc

    @classmethod
    def _row(cls, row: sqlite3.Row) -> RunCheckpoint:
        execution_context = cls._loads(row["execution_context_json"], {})
        checkpoint_version = str(execution_context.get(
            "_checkpoint_schema_version", "legacy-react-checkpoint-v0",
        ))
        code_version = str(execution_context.get(
            "_agent_code_version", "legacy-react-engine-v0",
        ))
        try:
            SchemaVersionRegistry.validate_agent_resume(
                checkpoint_version=checkpoint_version, code_version=code_version,
            )
        except SchemaCompatibilityError as exc:
            raise RunStoreError("checkpoint schema/code version is incompatible") from exc
        return RunCheckpoint(
            run_id=row["run_id"], request_id=row["request_id"], user_id=row["user_id"],
            conv_id=row["conv_id"], agent_type=row["agent_type"], task_id=row["task_id"],
            bundle_version=row["bundle_version"], status=RunStatus(row["status"]),
            system=row["system_text"], messages=tuple(cls._loads(row["messages_json"], [])),
            execution_context=execution_context,
            runtime=cls._loads(row["runtime_json"], {}), step=int(row["step"]),
            max_steps=int(row["max_steps"]),
            tool_call_ids=tuple(cls._loads(row["tool_call_ids_json"], [])),
            pending=cls._loads(row["pending_json"], {}),
            result=cls._loads(row["result_json"], {}), version=int(row["version"]),
            expires_at=row["expires_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _loads(value: str, default: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _sanitize_checkpoint_value(cls, value: Any, *, key: str = "") -> Any:
        """Preserve resumable structure while removing raw credentials."""
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized_key and not normalized_key.endswith(("fingerprint", "hash")) and any(
            token in normalized_key for token in (
                "authorization", "password", "passwd", "secret", "apikey",
                "accesstoken", "refreshtoken", "cookie",
            )
        ):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): cls._sanitize_checkpoint_value(
                    item_value, key=str(item_key),
                )
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_checkpoint_value(item) for item in value]
        if isinstance(value, str):
            text = value
            for pattern in (
                r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
                r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
                r"(?i)\b(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*[^\s,;]+",
            ):
                text = re.sub(pattern, "[REDACTED]", text)
            return text
        return value

    @staticmethod
    def _required(value: Any, name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{name} must not be empty")
        if len(text) > limit:
            raise ValueError(f"{name} exceeds {limit} characters")
        return text

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _future(seconds: float) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
