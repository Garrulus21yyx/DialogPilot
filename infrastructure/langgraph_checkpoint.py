"""Lifecycle owner for LangGraph's PostgreSQL execution checkpoints."""
from __future__ import annotations

from contextlib import AbstractContextManager

from psycopg import Connection
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


_TARGET_CHECKPOINT_TYPES = (
    ("application.work_item", "ControlMode"),
    ("application.work_item", "ArgumentValue"),
    ("application.work_item", "WorkItem"),
    ("application.work_item", "WorkPlan"),
    ("application.capability_registry", "CapabilityEffect"),
    ("application.capability_registry", "CapabilityRisk"),
    ("application.agent_result", "AgentResultStatus"),
    ("application.agent_result", "FactSourceKind"),
    ("application.agent_result", "FactRecord"),
    ("application.agent_result", "EvidenceRequest"),
    ("application.agent_result", "MissingInputSpec"),
    ("application.agent_result", "ReceiptRef"),
    ("application.agent_result", "StateMutationProposal"),
    ("application.agent_result", "AgentResult"),
    ("application.result_board", "ResultBoardSnapshot"),
)


def target_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_TARGET_CHECKPOINT_TYPES)


class PostgresCheckpointOwner(AbstractContextManager):
    """Open/setup/close one PostgresSaver at the application composition root."""

    def __init__(self, database_url: str, *, setup: bool = False) -> None:
        if not str(database_url or "").strip():
            raise ValueError("database_url is required")
        self._database_url = database_url
        self._setup = setup
        self._connection = None
        self.checkpointer = None

    def __enter__(self):
        self._connection = Connection.connect(
            self._database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        self.checkpointer = PostgresSaver(
            self._connection,
            serde=target_checkpoint_serializer(),
        )
        if self._setup:
            self.checkpointer.setup()
        return self.checkpointer

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self._connection is not None:
                self._connection.close()
            return None
        finally:
            self.checkpointer = None
            self._connection = None


class AsyncPostgresCheckpointOwner:
    """Async checkpoint lifecycle for graphs invoked through ``ainvoke``."""

    def __init__(self, database_url: str, *, setup: bool = False) -> None:
        if not str(database_url or "").strip():
            raise ValueError("database_url is required")
        self._database_url = database_url
        self._setup = setup
        self._context = None
        self.checkpointer = None

    async def __aenter__(self):
        self._context = AsyncPostgresSaver.from_conn_string(
            self._database_url,
            serde=target_checkpoint_serializer(),
        )
        self.checkpointer = await self._context.__aenter__()
        if self._setup:
            await self.checkpointer.setup()
        return self.checkpointer

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            if self._context is not None:
                return await self._context.__aexit__(exc_type, exc_value, traceback)
            return None
        finally:
            self.checkpointer = None
            self._context = None


def checkpoint_thread_id(invocation_key: object) -> str:
    """A checkpoint thread is one replayable invocation, not business state."""
    value = str(invocation_key or "").strip()
    if not value:
        raise ValueError("invocation_key is required")
    if len(value) > 255:
        raise ValueError("checkpoint thread_id exceeds PostgreSQL limit")
    return value
