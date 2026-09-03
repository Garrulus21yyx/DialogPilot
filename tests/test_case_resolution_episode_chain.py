"""Accepted case resolution through canonical episode generation and retrieval."""

from application.case_resolution import AcceptCaseResolution
from application.hybrid_retrieval import (
    EmbeddingProfile,
    EmbeddingProviderKind,
    RetrievalStatus,
)
from application.inbound_admission import NewInvocationInbound
from application.memory_retrieval_policy import DEFAULT_MEMORY_RETRIEVAL_POLICY
from application.service_episode_memory_search import ServiceEpisodeMemorySearch
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetriever,
)
from core.auth import Principal
from core.identity import IdentityFactory
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_case_resolution_episode import (
    PostgresCaseResolutionEpisodeProjector,
)
from infrastructure.postgres_retrieval_projection import (
    PostgresCanonicalRetrievalProjector,
)
from infrastructure.postgres_service_episode import (
    PostgresServiceEpisodeRepository,
    PostgresServiceEpisodeResolver,
)
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)
from infrastructure.service_episode_embedding import (
    ServiceEpisodeDocumentEmbedder,
    ServiceEpisodeQueryEmbedder,
)
from infrastructure.service_episode_generation import (
    PostgresServiceEpisodeGenerationManager,
)
from services.ticket_service import TicketPriority, TicketStatus


CREATED = "2026-09-03T11:00:00+00:00"


class EpisodeEmbeddingProvider:
    profile = EmbeddingProfile(
        provider="case-resolution-fixture-provider-v1",
        provider_kind=EmbeddingProviderKind.MODEL,
        model="case-resolution-fixture-model",
        model_version="1",
        dimension=3,
        model_digest="d" * 64,
        document_preprocessing="raw-service-episode-canonical-text-v1",
        query_preprocessing="raw-service-episode-query-v1",
    )

    def embed_documents(self, texts):
        return [[0.2, 0.4, 0.8] for _ in texts]

    def embed_queries(self, texts):
        return [[0.2, 0.4, 0.8] for _ in texts]


def test_accepted_resolution_is_retrievable_for_the_same_subject(ticket_service):
    service = ticket_service
    with service.pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.service_episode_heads,
                dialogpilot_app.service_episode_revisions,
                retrieval.canonical_projection_receipts,
                retrieval.canonical_projection_outbox,
                retrieval.service_episode_search,
                retrieval.retrieval_generation_registry,
                dialogpilot_app.conversations
            CASCADE
        """)
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-episode-chain",
        user_id="user-episode-chain",
        conversation_id="conversation-episode-chain",
        request_id="request-episode-chain",
    )
    PostgresAdmissionUnitOfWork(service.pool).admit_new(
        NewInvocationInbound(
            identity,
            "之前升级后出现 E401，无法登录",
            {"bundle": "case-resolution-chain-v1"},
            CREATED,
        )
    )
    ticket, _ = service.create_ticket(
        idempotency_key="episode-chain-ticket-v1",
        user_id=str(identity.user_id),
        conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id),
        question="升级后 E401 无法登录",
        published_response="已转交支持人员处理",
        reason="需要人工修复并验收",
        priority=TicketPriority.HIGH,
        agent_type="technical",
        intent="technical_login",
        verification_status="unknown",
        identity_metadata=identity.metadata(),
    )
    active = service.transition(
        ticket.ticket_id,
        TicketStatus.IN_PROGRESS,
        actor="support-agent-chain",
    )
    accepted, _ = service.accept_resolution(
        ticket.ticket_id,
        AcceptCaseResolution(
            idempotency_key="accept-episode-chain-v1",
            expected_ticket_version=active.version,
            resolution="清除旧令牌并重新绑定设备后恢复登录。",
            authoritative_outcomes=("用户确认 E401 已消失且登录成功",),
        ),
        principal=Principal("support-agent-chain", frozenset({"admin"})),
    )

    repository = PostgresServiceEpisodeRepository(service.pool)
    episode_projector = PostgresCaseResolutionEpisodeProjector(
        service.pool,
        repository,
    )
    commits = episode_projector.project_pending()
    assert len(commits) == 1
    assert commits[0].episode_id == ticket.ticket_id
    assert commits[0].already_applied is False

    provider = EpisodeEmbeddingProvider()
    retrieval_projector = PostgresCanonicalRetrievalProjector(
        service.pool,
        resolvers={
            "SERVICE_EPISODE": PostgresServiceEpisodeResolver(
                ServiceEpisodeDocumentEmbedder(provider),
            ),
        },
    )
    build = PostgresServiceEpisodeGenerationManager(
        service.pool,
        projector=retrieval_projector,
        embedding_profile=provider.profile,
    ).rebuild_and_activate("case-resolution-generation-v1")
    assert build.episode_count == 1

    retrieval_pool = RetrievalPostgresPool(
        RetrievalPoolConfig(
            service.pool.config.database_url,
            statement_timeout_ms=2000,
        )
    )
    retrieval_pool.open()
    try:
        policy = ServiceEpisodeRetrievalPolicy(
            "case-resolution-retrieval-v1",
            DEFAULT_MEMORY_RETRIEVAL_POLICY,
            0.0,
            365 * 86400,
        )
        search = ServiceEpisodeMemorySearch(
            generations=PostgresRetrievalGenerationRegistry(service.pool),
            retriever=ServiceEpisodeRetriever(
                PostgresHybridBackend(retrieval_pool),
                policy,
            ),
            embed_query=ServiceEpisodeQueryEmbedder(provider),
        )
        result = search.search(
            tenant_id=accepted.subject.tenant_id,
            user_id=accepted.subject.user_id,
            query="上次 E401 登录失败是怎么解决的？",
            top_k=1,
        )
    finally:
        retrieval_pool.close()

    assert result.status is RetrievalStatus.OK
    assert result.hits[0].episode_id == ticket.ticket_id
