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


def test_target_context_reads_prior_committed_turns_without_redis_projection(memory_projection_scope):
    from dataclasses import replace
    from application.conversation_state import ConversationState
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from infrastructure.target_turn_context import TargetTurnContextLoader
    from application.target_conversation_manager import TargetContextProjectionStatus
    pool, original = memory_projection_scope
    invocation = replace(original, request_id="next-request")
    state = ConversationState.empty(tenant_id=invocation.tenant_id,
        user_id=invocation.user_id, conversation_id=invocation.conversation_id)
    observations = TurnObservations("continue")
    context = asyncio.run(TargetTurnContextLoader(
        PostgresMemoryProjectionReader(pool), None).load(
            invocation, observations, state, DeterministicResolver().resolve(observations, state)))
    assert [message.content for message in context.recent_messages] == ["canonical prior turn"]
    assert context.source_watermark == 1
    assert context.projection_status is TargetContextProjectionStatus.DEGRADED
    assert context.recent_messages[0].source_ref


def test_projection_lag_returns_raw_source_fallback_and_omitted_ranges(
    memory_projection_scope,
):
    pool, identity = memory_projection_scope
    result = asyncio.run(PostgresMemoryProjectionReader(
        pool,
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
        pool,
    ).get_projection_result(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
        query="prior", current_request_id=str(identity.request_id),
    ))
    assert current_excluded.context.recent_messages == []


def test_current_thread_backend_failure_is_degraded_without_cross_session_retrieval(
    memory_projection_scope, monkeypatch,
):
    pool, identity = memory_projection_scope
    reader = PostgresMemoryProjectionReader(pool)
    def unavailable(subject):
        raise ConnectionError("summary unavailable")
    monkeypatch.setattr(reader._summaries, "read", unavailable)
    result = asyncio.run(reader.get_projection_result(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
        query="prior", current_request_id="different-request",
    ))
    assert result.state is MemoryProjectionState.DEGRADED
    assert result.retrieval_outcome is MemoryRetrievalOutcome.NOT_NEEDED
    assert result.raw_fallback_used is True
    assert result.reason_codes == ("SUMMARY_READ_ConnectionError",)
    assert result.context.recent_messages[0].content == "canonical prior turn"


def test_production_projection_loads_fixed_thread_without_episode_pre_retrieval(
    memory_projection_scope,
):
    pool, identity = memory_projection_scope
    result = asyncio.run(PostgresMemoryProjectionReader(
        pool,
    ).get_projection_result(
        str(identity.tenant_id), str(identity.user_id),
        str(identity.conversation_id), query="E401 登录失败",
        current_request_id="different-request",
    ))
    assert result.retrieval_outcome is MemoryRetrievalOutcome.NOT_NEEDED
    assert result.context.retrieval_hits == []
    assert result.context.relevant_history == []


def test_ready_projection_does_not_lose_more_than_200_uncovered_turns(memory_projection_scope, monkeypatch):
    from dataclasses import replace
    pool, identity = memory_projection_scope
    admission = PostgresAdmissionUnitOfWork(pool)
    for index in range(205):
        current = IdentityFactory().create_invocation(tenant_id=identity.tenant_id, user_id=identity.user_id,
            conversation_id=identity.conversation_id, request_id=f"uncovered-{index}")
        admission.admit_new(NewInvocationInbound(current, f"restriction-{index}", {"bundle": "v1"}, CREATED))
    reader = PostgresMemoryProjectionReader(pool)
    source, _ = reader._watermarks(identity.tenant_id, identity.user_id, identity.conversation_id)
    monkeypatch.setattr(reader, "_watermarks", lambda *_: (source, {
        name: source for name in ("working_window", "thread_summary", "fact_extraction")}))
    result = asyncio.run(reader.get_projection_result(identity.tenant_id, identity.user_id,
        identity.conversation_id, current_request_id="uncovered-204"))
    assert len(result.context.recent_messages) == 205
    assert result.context.recent_messages[0].content == "canonical prior turn"
    assert result.context.recent_messages[-1].content == "restriction-203"


def test_summary_and_transcript_use_event_sequence_not_turn_sequence(memory_projection_scope):
    from dataclasses import replace
    from application.conversation_projection import ConversationSubject
    from infrastructure.postgres_thread_summary import PostgresThreadSummaryRepository
    pool, identity = memory_projection_scope
    # A non-dialogue event advances the event sequence, not the turn sequence.
    with pool.transaction() as connection:
        from psycopg.types.json import Jsonb
        connection.execute("""INSERT INTO dialogpilot_app.conversation_events
            (event_id,operation_key,event_type,tenant_id,user_id,conversation_id,seq,payload,content_sha256,created_at)
            VALUES ('context-audit-event','context-audit-operation','CONVERSATION_FINALIZED',%s,%s,%s,2,%s,
                    repeat('0',64),transaction_timestamp())""",
            (identity.tenant_id, identity.user_id, identity.conversation_id, Jsonb({})))
        connection.execute("""UPDATE dialogpilot_app.conversations SET next_event_seq=3
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s""",
            (identity.tenant_id, identity.user_id, identity.conversation_id))
    repo = PostgresThreadSummaryRepository(pool)
    subject = ConversationSubject(identity.tenant_id, identity.user_id, identity.conversation_id)
    job = repo.prepare(subject, summarizer_version="context-test")
    repo.commit(job, summary="Original restriction is retained.")
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        IdentityFactory().create_invocation(tenant_id=identity.tenant_id, user_id=identity.user_id,
            conversation_id=identity.conversation_id, request_id="later"),
        "new restriction", {"bundle":"v1"}, CREATED))
    reader = PostgresMemoryProjectionReader(pool)
    result = asyncio.run(reader.get_projection_result(*(
        identity.tenant_id, identity.user_id, identity.conversation_id), current_request_id="next"))
    assert result.context.summary_covered_until_seq == 2
    assert result.context.recent_messages[-1].seq == 3
    assert result.context.recent_messages[-1].content == "new restriction"
    assert "Original restriction" in result.context.summary


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
