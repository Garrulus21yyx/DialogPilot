"""M4-T01 typed Memory read projection and raw fallback proofs."""
import asyncio

import pytest

from application.inbound_admission import NewInvocationInbound
from application.memory_projection import (
    MemoryProjectionResult,
    MemoryProjectionState,
    MemoryRetrievalOutcome,
    ProjectionRange,
)
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_memory_projection import PostgresMemoryProjectionReader
from memory.conversation_memory import MemoryContext


CREATED = "2026-09-02T19:00:00+00:00"


@pytest.fixture()
def memory_projection_scope(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.projection_watermarks,
                dialogpilot_app.conversation_projection_outbox,
                dialogpilot_app.workflow_start_outbox,
                dialogpilot_app.workflow_invocations,
                dialogpilot_app.conversation_events,
                dialogpilot_app.conversation_turns,
                dialogpilot_app.conversations
            CASCADE
        """)
    identity = IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-memory", user_id="user-memory",
        conversation_id="conversation-memory", request_id="request-memory",
    )
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity, "canonical prior turn", {"bundle": "v1"}, CREATED,
    ))
    try:
        yield pool, identity
    finally:
        pool.close()


class Memory:
    def __init__(self, *, failures=()):
        self.failures = list(failures)

    async def get_current_context(self, *_args, diagnostics=None, **_kwargs):
        if diagnostics is not None:
            diagnostics["failures"] = list(self.failures)
        return MemoryContext([], [], {}, "", [])


class CurrentThreadOnlyMemory:
    def __init__(self):
        self.current_calls = []

    async def get_current_context(self, user_id, conv_id, *, diagnostics=None):
        self.current_calls.append((user_id, conv_id))
        return MemoryContext([], [], {}, "", [])

    async def get_context(self, *_args, **_kwargs):
        raise AssertionError("ChatApplication projection must not pre-retrieve episodes")


def test_projection_lag_returns_raw_source_fallback_and_omitted_ranges(
    memory_projection_scope,
):
    pool, identity = memory_projection_scope
    result = asyncio.run(PostgresMemoryProjectionReader(
        pool, Memory(),
    ).get_projection_result(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
        query="prior", current_request_id="different-request",
    ))
    assert result.state is MemoryProjectionState.LAGGING
    assert result.source_watermark == 1
    assert set(result.projection_watermarks.values()) == {0}
    assert result.raw_fallback_used is True
    assert [item.content for item in result.context.recent_messages] == [
        "canonical prior turn"
    ]
    assert {item.projection for item in result.omitted_ranges} == {
        "working_window", "thread_summary", "fact_extraction",
    }
    assert result.retrieval_outcome is MemoryRetrievalOutcome.NOT_NEEDED
    current_excluded = asyncio.run(PostgresMemoryProjectionReader(
        pool, Memory(),
    ).get_projection_result(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
        query="prior", current_request_id=str(identity.request_id),
    ))
    assert current_excluded.context.recent_messages == []


def test_current_thread_backend_failure_is_degraded_without_cross_session_retrieval(
    memory_projection_scope,
):
    pool, identity = memory_projection_scope
    result = asyncio.run(PostgresMemoryProjectionReader(pool, Memory(failures=(
        "CURRENT_THREAD_REDIS_UNAVAILABLE",
    ))).get_projection_result(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
        query="prior", current_request_id="different-request",
    ))
    assert result.state is MemoryProjectionState.DEGRADED
    assert result.retrieval_outcome is MemoryRetrievalOutcome.NOT_NEEDED
    assert result.raw_fallback_used is True
    assert result.reason_codes == ("CURRENT_THREAD_REDIS_UNAVAILABLE",)


def test_production_projection_loads_fixed_thread_without_episode_pre_retrieval(
    memory_projection_scope,
):
    pool, identity = memory_projection_scope
    memory = CurrentThreadOnlyMemory()
    result = asyncio.run(PostgresMemoryProjectionReader(
        pool, memory,
    ).get_projection_result(
        str(identity.tenant_id), str(identity.user_id),
        str(identity.conversation_id), query="E401 登录失败",
        current_request_id="different-request",
    ))
    assert memory.current_calls == [
        (str(identity.user_id), str(identity.conversation_id)),
    ]
    assert result.retrieval_outcome is MemoryRetrievalOutcome.NOT_NEEDED
    assert result.context.retrieval_hits == []
    assert result.context.relevant_history == []


def test_ready_contract_rejects_hidden_omission_or_lag():
    with pytest.raises(ValueError, match="cannot omit"):
        MemoryProjectionResult(
            MemoryProjectionState.READY,
            MemoryContext([], [], {}, "", []),
            2,
            {"working_window": 2},
            MemoryRetrievalOutcome.NO_MATCH,
            omitted_ranges=(ProjectionRange("fact_extraction", 1, 2, "lag"),),
        )
    with pytest.raises(ValueError, match="must cover"):
        MemoryProjectionResult(
            MemoryProjectionState.READY,
            MemoryContext([], [], {}, "", []),
            2,
            {"working_window": 1},
            MemoryRetrievalOutcome.NO_MATCH,
        )


def test_memory_projection_state_and_retrieval_algebras_are_closed():
    assert set(MemoryProjectionState) == {
        MemoryProjectionState.READY,
        MemoryProjectionState.LAGGING,
        MemoryProjectionState.DEGRADED,
        MemoryProjectionState.UNAVAILABLE,
    }
    assert set(MemoryRetrievalOutcome) == {
        MemoryRetrievalOutcome.NOT_NEEDED,
        MemoryRetrievalOutcome.NO_MATCH,
        MemoryRetrievalOutcome.HITS,
        MemoryRetrievalOutcome.UNAVAILABLE,
        MemoryRetrievalOutcome.CONFLICT,
    }
