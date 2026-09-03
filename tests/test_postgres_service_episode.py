"""ServiceEpisode lifecycle, CAS, deletion and projection proofs."""
from dataclasses import replace

import psycopg
import pytest

from application.data_location_registry import (
    DataLocationRegistry,
    DataSubjectRef,
    default_registry_path,
)
from application.inbound_admission import NewInvocationInbound
from application.evidence_receipt import ServiceEpisodeLocator
from application.hybrid_retrieval import EmbeddingProfile, EmbeddingProviderKind
from application.service_episode import (
    CaseOutcomeVerification,
    EpisodeEvidence,
    EpisodeProjectionTarget,
    OutcomeVerificationStatus,
    ServiceEpisodeCandidate,
    ServiceEpisodeConflict,
)
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_retrieval_projection import (
    PostgresCanonicalRetrievalProjector,
)
from infrastructure.postgres_service_episode import (
    PostgresServiceEpisodeRepository,
    PostgresServiceEpisodeEvidenceResolver,
    PostgresServiceEpisodeResolver,
)
from infrastructure.service_episode_embedding import (
    ServiceEpisodeDocumentEmbedder,
    ServiceEpisodeEmbeddingContractError,
    ServiceEpisodeEmbeddingFailure,
)


CREATED = "2026-09-02T20:00:00+00:00"
SHA = "a" * 64


class EpisodeEmbeddingProvider:
    profile = EmbeddingProfile(
        provider="fixture-service-episode-model-provider-v1",
        provider_kind=EmbeddingProviderKind.MODEL,
        model="fixture-service-episode-model",
        model_version="revision-1",
        dimension=3,
        model_digest="b" * 64,
        document_preprocessing="raw-service-episode-canonical-text-v1",
        query_preprocessing="raw-service-episode-query-v1",
    )

    def __init__(self):
        self.document_inputs = []

    def embed_documents(self, texts):
        self.document_inputs.append(tuple(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    def embed_queries(self, texts):
        return [[0.3, 0.2, 0.1] for _ in texts]


@pytest.fixture()
def episode_scope(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=3,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.service_episode_heads,
                dialogpilot_app.service_episode_revisions,
                retrieval.canonical_projection_receipts,
                retrieval.canonical_projection_outbox,
                retrieval.service_episode_search,
                retrieval.retrieval_generation_registry,
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
        tenant_id="tenant-episode", user_id="user-episode",
        conversation_id="conversation-episode", request_id="request-episode",
    )
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity, "customer symptom", {"bundle": "v1"}, CREATED,
    ))
    try:
        yield pool, identity
    finally:
        pool.close()


def _candidate(identity, **changes):
    candidate = ServiceEpisodeCandidate(
        subject=DataSubjectRef(
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ),
        source_deletion_epoch=0,
        case_id="case-verified-1",
        case_status="resolved",
        revision=1,
        expected_version=0,
        problem="customer symptom after upgrade",
        product_version="desktop-2.0",
        symptoms=("login failed",),
        materials=("log-ref:abc",),
        actions=("reset token",),
        authoritative_outcomes=("account owner confirmed login succeeds",),
        resolution="token reset completed",
        root_cause="stale token",
        outcome_verification=CaseOutcomeVerification(
            "receipt:case-owner:1", OutcomeVerificationStatus.ACCEPTED,
            "TicketService", "2026-09-02T20:10:00+00:00",
        ),
        evidence=(
            EpisodeEvidence("event:user:1", "user", "customer symptom"),
            EpisodeEvidence("event:assistant:1", "assistant", "assistant suggestion"),
        ),
        extractor_version="episode-projector-v1",
        entity_ids=("account-42", "device-7"),
    )
    return replace(candidate, **changes)


def _generation(pool, profile=EpisodeEmbeddingProvider.profile):
    with pool.transaction() as connection:
        connection.execute("""
            INSERT INTO retrieval.retrieval_generation_registry (
                corpus,backend_id,generation_id,backend_fingerprint,
                immutable_fingerprint,schema_version,source_watermark,
                embedding_provider,embedding_provider_kind,embedding_model,
                embedding_model_version,embedding_dimension,embedding_model_digest,
                embedding_document_preprocessing,embedding_query_preprocessing,
                distance_metric,vector_extension_version,index_method,index_params,
                chinese_tokenizer,lexical_ranker,manifest_hash,state
            ) VALUES (
                'SERVICE_EPISODE','pg-hybrid-v1','episode-generation-1','backend-v1',
                %s,'service-episode-search-v1','0',%s,%s,%s,%s,%s,%s,%s,%s,
                'COSINE','0.8.0','EXACT','{}','simple','ts_rank_cd',%s,'BUILDING'
            )
        """, (
            SHA, profile.provider, profile.provider_kind.value, profile.model,
            profile.model_version, profile.dimension, profile.model_digest,
            profile.document_preprocessing, profile.query_preprocessing,
            "c" * 64,
        ))


def test_location_is_write_approved_before_service_episode_schema():
    location = DataLocationRegistry.load(default_registry_path()).get(
        "location:service-episode:v1"
    )
    assert location.readiness.value == "WRITE_APPROVED"
    assert location.delete_adapter_id == "adapter:pg-service-episode-delete:v1"
    assert location.proof_contract_id == "proof:m4-t04-service-episode:v1"


@pytest.mark.parametrize("status", ["open", "in_progress", "waiting_customer"])
def test_unresolved_case_cannot_form_episode(status):
    identity = type("Identity", (), {
        "tenant_id": "t", "user_id": "u", "conversation_id": "c",
    })()
    with pytest.raises(ValueError, match="resolved/closed"):
        _candidate(identity, case_status=status)


def test_unaccepted_outcome_cannot_form_episode():
    identity = type("Identity", (), {
        "tenant_id": "t", "user_id": "u", "conversation_id": "c",
    })()
    verification = CaseOutcomeVerification(
        "receipt:unknown", OutcomeVerificationStatus.UNKNOWN,
        "TicketService", "2026-09-02T20:10:00+00:00",
    )
    with pytest.raises(ValueError, match="must accept"):
        _candidate(identity, outcome_verification=verification)


@pytest.mark.parametrize(
    ("entity_ids", "message"),
    [
        (("account-42", ""), "must not be blank"),
        (("account-42", "account-42"), "must be unique"),
    ],
)
def test_canonical_episode_rejects_invalid_entity_scope(entity_ids, message):
    identity = type("Identity", (), {
        "tenant_id": "t", "user_id": "u", "conversation_id": "c",
    })()
    with pytest.raises(ValueError, match=message):
        _candidate(identity, entity_ids=entity_ids)


def test_commit_is_canonical_idempotent_and_keeps_roles_separate(episode_scope):
    pool, identity = episode_scope
    candidate = _candidate(identity)
    repository = PostgresServiceEpisodeRepository(pool)
    first = repository.commit(candidate)
    replay = repository.commit(candidate)

    assert first.already_applied is False
    assert replay.already_applied is True
    assert first.episode_id == candidate.case_id
    with pool.transaction() as connection:
        row = connection.execute("""
            SELECT user_evidence,assistant_evidence,source_event_refs,
                   outcome_verification_ref,provenance_sha256,entity_ids
            FROM dialogpilot_app.service_episode_revisions
        """).fetchone()
        assert row[0][0]["content"] == "customer symptom"
        assert row[1][0]["content"] == "assistant suggestion"
        assert row[2] == ["event:user:1", "event:assistant:1"]
        assert row[3] == "receipt:case-owner:1"
        assert row[4] == candidate.provenance_sha256
        assert row[5] == ["account-42", "device-7"]


def test_revision_cas_conflict_and_canonical_rows_are_immutable(episode_scope):
    pool, identity = episode_scope
    repository = PostgresServiceEpisodeRepository(pool)
    candidate = _candidate(identity)
    repository.commit(candidate)
    with pytest.raises(ServiceEpisodeConflict, match="CAS"):
        repository.commit(replace(
            candidate, revision=2, expected_version=0,
            resolution="different verified resolution",
        ))
    with pool.transaction() as connection, pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
    ):
        connection.execute("""
            UPDATE dialogpilot_app.service_episode_revisions
            SET resolution='mutated' WHERE episode_id=%s AND revision=1
        """, (candidate.episode_id,))


def test_projection_uses_canonical_current_revision_and_weighted_roles(episode_scope):
    pool, identity = episode_scope
    _generation(pool)
    provider = EpisodeEmbeddingProvider()
    candidate = _candidate(identity)
    committed = PostgresServiceEpisodeRepository(pool).commit(
        candidate,
        projection_target=EpisodeProjectionTarget(
            "pg-hybrid-v1", "episode-generation-1",
        ),
    )
    result = PostgresCanonicalRetrievalProjector(
        pool, resolvers={
            "SERVICE_EPISODE": PostgresServiceEpisodeResolver(
                ServiceEpisodeDocumentEmbedder(provider),
            ),
        },
    ).project(committed.projection_event_id)
    assert result.code.value == "APPLIED"
    assert result.candidate_count == 1
    with pool.transaction() as connection:
        row = connection.execute("""
            SELECT episode_id,episode_revision,outcome_receipt_ref,
                   provenance_sha256,entity_ids,embedding::text,
                   ts_rank_cd(search_tsv,plainto_tsquery('simple','customer symptom')),
                   ts_rank_cd(search_tsv,plainto_tsquery('simple','assistant suggestion'))
            FROM retrieval.service_episode_search
        """).fetchone()
    assert row[:4] == (
        candidate.episode_id, "1", "receipt:case-owner:1",
        candidate.provenance_sha256,
    )
    assert row[4] == ["account-42", "device-7"]
    assert row[5] == "[0.1,0.2,0.3]"
    assert row[6] > row[7]
    assert len(provider.document_inputs) == 1
    embedded_text = provider.document_inputs[0][0]
    assert "problem: customer symptom after upgrade" in embedded_text
    assert "materials: log-ref:abc" in embedded_text
    assert "entity_ids: account-42 | device-7" in embedded_text
    payload = PostgresServiceEpisodeEvidenceResolver(pool).resolve(
        ServiceEpisodeLocator(
            "tenant-episode", "user-episode", "pg-hybrid-v1",
            "episode-generation-1", candidate.episode_id, "1",
            candidate.provenance_sha256,
        )
    )
    assert payload["outcome_receipt_ref"] == "receipt:case-owner:1"
    assert payload["user_evidence_refs"] == ["event:user:1"]
    assert payload["assistant_evidence_refs"] == ["event:assistant:1"]


def test_projection_rejects_provider_profile_drift_before_search_write(episode_scope):
    pool, identity = episode_scope
    _generation(pool)
    candidate = _candidate(identity)
    committed = PostgresServiceEpisodeRepository(pool).commit(
        candidate,
        projection_target=EpisodeProjectionTarget(
            "pg-hybrid-v1", "episode-generation-1",
        ),
    )
    provider = EpisodeEmbeddingProvider()
    provider.profile = replace(provider.profile, model_version="revision-2")
    projector = PostgresCanonicalRetrievalProjector(
        pool,
        resolvers={
            "SERVICE_EPISODE": PostgresServiceEpisodeResolver(
                ServiceEpisodeDocumentEmbedder(provider),
            ),
        },
    )

    with pytest.raises(ServiceEpisodeEmbeddingContractError) as raised:
        projector.project(committed.projection_event_id)

    assert raised.value.code is ServiceEpisodeEmbeddingFailure.PROFILE_MISMATCH
    with pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM retrieval.service_episode_search"
        ).fetchone()[0] == 0


def test_conversation_tombstone_purges_episode_canonical_state(episode_scope):
    pool, identity = episode_scope
    PostgresServiceEpisodeRepository(pool).commit(_candidate(identity))
    with pool.transaction() as connection:
        connection.execute("""
            UPDATE dialogpilot_app.conversations
            SET deleted_at=transaction_timestamp(), deletion_epoch=deletion_epoch+1
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ))
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.service_episode_revisions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.service_episode_heads"
        ).fetchone()[0] == 0
