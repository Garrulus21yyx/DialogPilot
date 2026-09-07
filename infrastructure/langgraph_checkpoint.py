"""Lifecycle owner for LangGraph's PostgreSQL execution checkpoints."""
from __future__ import annotations

from contextlib import AbstractContextManager, AsyncExitStack
from langgraph.store.postgres.aio import AsyncPostgresStore

from psycopg import Connection
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import ormsgpack
import json


class TargetCheckpointContractError(ValueError):
    """A persisted target binding cannot be reconstructed under its contract."""


class TargetCheckpointSerializer(JsonPlusSerializer):
    def loads_typed(self, data):
        if data[0] == "json":
            def inspect_json(value):
                if isinstance(value, dict):
                    identity = value.get('id')
                    if (value.get('lc') == 2 and isinstance(identity, list)
                            and len(identity) >= 2 and all(isinstance(part, str) for part in identity)
                            and ('.'.join(identity[:-1]), identity[-1]) in _TARGET_CHECKPOINT_TYPES):
                        raise TargetCheckpointContractError("legacy target constructor JSON requires explicit migration")
                    for child in value.values():
                        inspect_json(child)
                elif isinstance(value, list):
                    for child in value:
                        inspect_json(child)
            inspect_json(json.loads(data[1]))
        if data[0] == "msgpack":
            # JsonPlus may swallow constructor errors and return None, including
            # inside optional parent fields. Inspect nested extension records
            # before ordinary decoding so binding migration failures stay typed.
            def inspect_extension(code, encoded):
                decoded = ormsgpack.unpackb(encoded, ext_hook=inspect_extension,
                                            option=ormsgpack.OPT_NON_STR_KEYS)
                if (isinstance(decoded, (list, tuple)) and len(decoded) >= 2
                        and decoded[:2] in (("application.entity_binding", "EntityBinding"),
                                            ["application.entity_binding", "EntityBinding"])):
                    from application.entity_binding import EntityBinding, BindingSource
                    try:
                        if len(decoded) != 3 or not isinstance(decoded[2], dict):
                            raise ValueError('unsupported binding encoding')
                        fields = dict(decoded[2])
                        source = fields['source']
                        if isinstance(source, (list, tuple)) and len(source) == 3 and tuple(source[:2]) == (
                                'application.entity_binding', 'BindingSource'):
                            source = source[2]
                        fields['source'] = BindingSource(source)
                        EntityBinding(**fields)
                    except (ValueError, TypeError, KeyError) as exc:
                        raise TargetCheckpointContractError("checkpoint entity binding requires fresh type selection") from exc
                # Only return structural data to the preflight scanner. Normal
                # decoding below constructs the rest of the checkpoint once.
                return decoded
            try:
                ormsgpack.unpackb(data[1], ext_hook=inspect_extension,
                                 option=ormsgpack.OPT_NON_STR_KEYS)
            except TargetCheckpointContractError:
                raise
            except Exception as exc:
                raise TargetCheckpointContractError("checkpoint binding inspection failed") from exc
        return super().loads_typed(data)


_TARGET_CHECKPOINT_TYPES = (
    ("core.identity", "TenantId"),
    ("core.identity", "UserId"),
    ("core.identity", "ConversationId"),
    ("core.identity", "ContinuationId"),
    ("core.identity", "RequestId"),
    ("core.identity", "TurnId"),
    ("core.identity", "WorkflowRunId"),
    ("core.identity", "TurnKey"),
    ("core.identity", "InvocationKey"),
    ("core.identity", "InvocationIdentity"),
    ("application.deterministic_resolution", "TurnObservations"),
    ("application.deterministic_resolution", "ResolvedField"),
    ("application.deterministic_resolution", "DeterministicResolution"),
    ("application.deterministic_resolution", "ResolutionKind"),
    ("application.entity_binding", "BindingSource"),
    ("application.entity_binding", "BindingStatus"),
    ("application.entity_binding", "EntityBinding"),
    ("application.entity_binding", "EntityBindingSet"),
    ("application.work_item", "ControlMode"),
    ("application.work_item", "ArgumentValue"),
    ("application.work_item", "WorkControlBinding"),
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
    ("application.conversation_state", "WorkstreamStatus"),
    ("application.conversation_state", "WorkControlStatus"),
    ("application.conversation_state", "ConversationOwner"),
    ("application.conversation_state", "WorkstreamState"),
    ("application.conversation_state", "WorkControlState"),
    ("application.conversation_state", "PendingInteractionState"),
    ("application.conversation_state", "PendingApprovalState"),
    ("application.conversation_state", "AcceptedApprovalState"),
    ("application.conversation_state", "ResumeBinding"),
    ("application.conversation_state", "ConversationState"),
    ("application.turn_planning", "RouteMode"),
    ("application.turn_planning", "MutationApplyStage"),
    ("application.turn_planning", "WorkControlMutation"),
    ("application.turn_planning", "FlowMutation"),
    ("application.turn_planning", "FlowTransitionPlan"),
    ("application.turn_planning", "RouteDecision"),
    ("application.turn_planning", "TurnPlan"),
    ("application.target_conversation_manager", "TargetContextProjectionStatus"),
    ("application.target_conversation_manager", "TargetContextMessage"),
    ("application.target_conversation_manager", "TargetContextSummary"),
    ("application.target_conversation_manager", "TargetTurnContext"),
    ("application.target_conversation_manager", "PreparedTurn"),
    ("application.target_conversation_manager", "ManagedTurnResult"),
    ("application.response_assembly", "ResponseAssemblyMode"),
    ("application.response_assembly", "AssembledResponse"),
)


def target_checkpoint_serializer() -> JsonPlusSerializer:
    return TargetCheckpointSerializer(allowed_msgpack_modules=_TARGET_CHECKPOINT_TYPES)


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

    def __init__(self, database_url: str, *, setup: bool = False,
                 result_ttl_minutes: float = 43200) -> None:
        if not str(database_url or "").strip():
            raise ValueError("database_url is required")
        self._database_url = database_url
        self._setup = setup
        if result_ttl_minutes <= 0:
            raise ValueError("result retention must be positive")
        self._result_ttl = result_ttl_minutes
        self._context = None
        self.checkpointer = None
        self.store = None

    async def __aenter__(self):
        self._context = AsyncExitStack()
        await self._context.__aenter__()
        try:
            self.checkpointer = await self._context.enter_async_context(
                AsyncPostgresSaver.from_conn_string(self._database_url, serde=target_checkpoint_serializer()))
            self.store = await self._context.enter_async_context(
                AsyncPostgresStore.from_conn_string(self._database_url, ttl={
                    "default_ttl": self._result_ttl, "refresh_on_read": True, "omit_expired": True,
                    "sweep_interval_minutes": 5}))
            if self._setup:
                await self.checkpointer.setup()
                await self.store.setup()
            # Forward migration for pre-TTL originals in the pinned SDK schema.
            # Preserve their original age; a restart must not extend retention.
            # SDK remains the owner of subsequent refresh, reads and sweeping.
            await self.store.conn.execute("""
                UPDATE store
                SET ttl_minutes = %s,
                    expires_at = updated_at + %s * INTERVAL '1 minute'
                WHERE prefix LIKE 'target-originals.%%' AND ttl_minutes IS NULL
            """, (self._result_ttl, self._result_ttl))
            await self.store.start_ttl_sweeper()
            self._context.push_async_callback(self.store.stop_ttl_sweeper)
            return self.checkpointer
        except BaseException:
            await self._context.aclose()
            raise

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            if self._context is not None:
                return await self._context.__aexit__(exc_type, exc_value, traceback)
            return None
        finally:
            self.checkpointer = None
            self.store = None
            self._context = None


def checkpoint_thread_id(invocation_key: object) -> str:
    """A checkpoint thread is one replayable invocation, not business state."""
    value = str(invocation_key or "").strip()
    if not value:
        raise ValueError("invocation_key is required")
    if len(value) > 255:
        raise ValueError("checkpoint thread_id exceeds PostgreSQL limit")
    return value
