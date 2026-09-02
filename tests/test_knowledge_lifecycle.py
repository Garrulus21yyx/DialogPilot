"""M5-T01 SourceRevision lifecycle and Knowledge Publication proofs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from application.hybrid_retrieval import GenerationState
from application.knowledge_lifecycle import (
    LEGAL_TRANSITIONS,
    GateApproval,
    KnowledgeCandidate,
    KnowledgeCandidateKind,
    KnowledgeLifecycleError,
    PublicationPointerKind,
    PublicationScope,
    ReviewDecision,
    SourceRevisionStatus,
    validate_transition,
)
from application.knowledge_source import KnowledgeSourceManifest, SourceRevision
from infrastructure.postgres_knowledge_publication import (
    PostgresKnowledgePublicationRepository,
)
from infrastructure.postgres_knowledge_source import PostgresKnowledgeSourceRepository
from infrastructure.postgres_retrieval_projection import (
    PostgresCanonicalRetrievalProjector,
)
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry
from tests.test_knowledge_source_postgres import (
    _chunk,
    _generation,
    _locator,
    source_pool as _source_pool_fixture,
)


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def lifecycle_pool(postgres_database_url):
    fixture = _source_pool_fixture.__wrapped__(postgres_database_url)
    pool = next(fixture)
    try:
        yield pool
    finally:
        with pytest.raises(StopIteration):
            next(fixture)


def _draft(content: str, *, supersedes: str | None = None) -> SourceRevision:
    return SourceRevision.draft(
        tenant_id="tenant-knowledge", source_id="refund-policy", title="退款政策",
        source_type="text", content=content, effective_from=NOW,
        owner_id="knowledge-ops", scope="public", locale="zh-CN",
        product="", region="CN", supersedes_revision_id=supersedes,
        operations_audit_ref="ops/change-42",
    )


def _gate(kind: PublicationPointerKind) -> GateApproval:
    return GateApproval(kind.required_gate_id, "READY", "a" * 64, "evaluation")


def _scope(kind=PublicationPointerKind.SCOPED_CANARY) -> PublicationScope:
    if kind is PublicationPointerKind.GLOBAL:
        return PublicationScope(
            "tenant-knowledge", "pg-knowledge-v1", kind, "*", "*", "", "*",
        )
    return PublicationScope(
        "tenant-knowledge", "pg-knowledge-v1", kind,
        "public", "zh-CN", "", "CN",
    )


def _stage(pool, source: SourceRevision, generation_id: str):
    publication = PostgresKnowledgePublicationRepository(pool)
    publication.register_draft(source, actor_id="author", created_at=NOW)
    review = ReviewDecision("reviewer", "APPROVED", "review/42", NOW)
    publication.transition(
        tenant_id=source.tenant_id, source_id=source.source_id,
        revision_id=source.revision_id, expected_version=1,
        target=SourceRevisionStatus.REVIEWED, actor_id="reviewer",
        reason_code="HUMAN_REVIEW", evidence_ref="review/42",
        occurred_at=NOW, review=review,
    )
    publication.transition(
        tenant_id=source.tenant_id, source_id=source.source_id,
        revision_id=source.revision_id, expected_version=2,
        target=SourceRevisionStatus.STAGED, actor_id="index-builder",
        reason_code="HELDOUT_PASSED", evidence_ref="gate/heldout-42",
        occurred_at=NOW,
    )
    manifest = KnowledgeSourceManifest.build(
        tenant_id=source.tenant_id, backend_id="pg-knowledge-v1",
        generation_id=generation_id, scope="public", locale="zh-CN",
        product="", sources=(source,), reviewer_manifest_ref="review/42",
    )
    generations = PostgresRetrievalGenerationRegistry(pool)
    generations.register(_generation(manifest))
    generations.transition(generation_id, GenerationState.BUILDING)
    source_repository = PostgresKnowledgeSourceRepository(pool)
    source_repository.write_generation(
        manifest, (source,), (_chunk(source, generation_id),),
    )
    assert PostgresCanonicalRetrievalProjector(pool).project_next() is not None
    generations.transition(generation_id, GenerationState.READY)
    return publication, source_repository, manifest


def test_canonical_state_machine_accepts_exactly_the_declared_edges():
    states = tuple(SourceRevisionStatus)
    for current in states:
        for target in states:
            if (current, target) in LEGAL_TRANSITIONS:
                assert validate_transition(current, target) == (current, target)
            else:
                with pytest.raises(KnowledgeLifecycleError) as error:
                    validate_transition(current, target)
                assert error.value.code == "INVALID_TRANSITION"
    with pytest.raises(KnowledgeLifecycleError) as error:
        validate_transition("FUTURE", "ACTIVE")
    assert error.value.code == "INVALID_TRANSITION"


def test_frozen_contract_matches_runtime_state_and_gate_algebra():
    contract = json.loads((
        ROOT / "governance/knowledge/m5-t01-knowledge-lifecycle-v1.json"
    ).read_text("utf-8"))
    assert contract["source_revision"]["states"] == [
        state.value for state in SourceRevisionStatus
    ]
    assert set(contract["source_revision"]["legal_transitions"]) == {
        f"{current.value}->{target.value}" for current, target in LEGAL_TRANSITIONS
    }
    assert contract["publication"]["scoped_canary_gate"] == (
        f"{PublicationPointerKind.SCOPED_CANARY.required_gate_id}:READY"
    )
    assert contract["publication"]["global_gate"] == (
        f"{PublicationPointerKind.GLOBAL.required_gate_id}:READY"
    )
    assert contract["candidate_policy"]["automatic_publication"] is False


def test_operational_source_revision_requires_explicit_applicability():
    source = _draft("退款将在三日内原路退回。")
    assert source.schema_version == "knowledge-source-v1"
    assert (source.owner_id, source.locale, source.region) == (
        "knowledge-ops", "zh-CN", "CN",
    )
    with pytest.raises(Exception, match="provenance"):
        SourceRevision.draft(
            tenant_id="tenant-knowledge", source_id="bad", title="bad",
            source_type="text", content="bad", effective_from=NOW,
            owner_id="", scope="public", locale="zh-CN", product="",
            region="CN", operations_audit_ref="ops/bad",
        )


def test_scoped_publication_pins_atomically_and_rolls_back(lifecycle_pool):
    source_pool = lifecycle_pool
    old = _draft("退款将在三日内原路退回。")
    publication, _sources, old_manifest = _stage(
        source_pool, old, "knowledge-lifecycle-old",
    )
    pointer = publication.publish(
        publication_scope=_scope(), generation_id=old_manifest.generation_id,
        manifest_hash=old_manifest.manifest_hash, expected_version=0,
        gate=_gate(PublicationPointerKind.SCOPED_CANARY), actor_id="publisher",
        occurred_at=NOW,
    )
    assert pointer.pointer_version == 1
    pinned_before = publication.pin_request(
        request_id="request-before", tenant_id=old.tenant_id,
        backend_id="pg-knowledge-v1", scope="public", locale="zh-CN",
        product="", region="CN",
    )

    new = _draft("退款将在一个工作日内原路退回。", supersedes=old.revision_id)
    _same_publication, _same_sources, new_manifest = _stage(
        source_pool, new, "knowledge-lifecycle-new",
    )
    publication.publish(
        publication_scope=_scope(), generation_id=new_manifest.generation_id,
        manifest_hash=new_manifest.manifest_hash, expected_version=1,
        gate=_gate(PublicationPointerKind.SCOPED_CANARY), actor_id="publisher",
        occurred_at=NOW + timedelta(minutes=1),
    )
    pinned_after = publication.pin_request(
        request_id="request-after", tenant_id=old.tenant_id,
        backend_id="pg-knowledge-v1", scope="public", locale="zh-CN",
        product="", region="CN",
    )
    assert pinned_before.generation_id == old_manifest.generation_id
    assert publication.pin_request(
        request_id="request-before", tenant_id=old.tenant_id,
        backend_id="pg-knowledge-v1", scope="public", locale="zh-CN",
        product="", region="CN",
    ) == pinned_before
    with pytest.raises(KnowledgeLifecycleError) as pin_error:
        publication.pin_request(
            request_id="request-before", tenant_id="another-tenant",
            backend_id="pg-knowledge-v1", scope="public", locale="zh-CN",
            product="", region="CN",
        )
    assert pin_error.value.code == "MANIFEST_PIN_CONFLICT"
    assert pinned_after.generation_id == new_manifest.generation_id

    rolled_back = publication.rollback(
        publication_scope=_scope(), expected_version=2,
        gate=_gate(PublicationPointerKind.SCOPED_CANARY), actor_id="publisher",
        occurred_at=NOW + timedelta(minutes=2),
    )
    assert (rolled_back.generation_id, rolled_back.pointer_version) == (
        old_manifest.generation_id, 3,
    )
    assert publication.pin_request(
        request_id="request-after-rollback", tenant_id=old.tenant_id,
        backend_id="pg-knowledge-v1", scope="public", locale="zh-CN",
        product="", region="CN",
    ).generation_id == old_manifest.generation_id


def test_global_pointer_requires_lifecycle_ga_and_candidate_never_publishes(
    lifecycle_pool,
):
    source_pool = lifecycle_pool
    source = _draft("政策内容。")
    publication, _sources, manifest = _stage(
        source_pool, source, "knowledge-lifecycle-global",
    )
    with pytest.raises(KnowledgeLifecycleError) as error:
        publication.publish(
            publication_scope=_scope(PublicationPointerKind.GLOBAL),
            generation_id=manifest.generation_id,
            manifest_hash=manifest.manifest_hash, expected_version=0,
            gate=_gate(PublicationPointerKind.SCOPED_CANARY), actor_id="publisher",
            occurred_at=NOW,
        )
    assert error.value.code == "GATE_NOT_READY"

    candidate = KnowledgeCandidate(
        "candidate-zero-hit", source.tenant_id, KnowledgeCandidateKind.ZERO_HIT,
        {"query": "如何退款"}, "trace/zero-hit", "privacy/review-42", NOW,
    )
    publication.submit_candidate(candidate)
    publication.submit_candidate(candidate)
    with source_pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM retrieval.knowledge_candidates"
        ).fetchone()[0] == 1
        assert connection.execute("""
            SELECT count(*) FROM retrieval.knowledge_publication_pointers
        """).fetchone()[0] == 0

    publication.publish(
        publication_scope=_scope(PublicationPointerKind.GLOBAL),
        generation_id=manifest.generation_id, manifest_hash=manifest.manifest_hash,
        expected_version=0, gate=_gate(PublicationPointerKind.GLOBAL),
        actor_id="publisher", occurred_at=NOW,
    )


def test_retracted_revision_cannot_be_dereferenced_for_evidence(
    lifecycle_pool,
):
    source_pool = lifecycle_pool
    source = _draft("即日起生效。")
    publication, source_repository, manifest = _stage(
        source_pool, source, "knowledge-lifecycle-retract",
    )
    publication.publish(
        publication_scope=_scope(), generation_id=manifest.generation_id,
        manifest_hash=manifest.manifest_hash, expected_version=0,
        gate=_gate(PublicationPointerKind.SCOPED_CANARY), actor_id="publisher",
        occurred_at=NOW,
    )
    assert source_repository.resolve(_locator(source, manifest.generation_id))
    publication.transition(
        tenant_id=source.tenant_id, source_id=source.source_id,
        revision_id=source.revision_id, expected_version=4,
        target=SourceRevisionStatus.RETRACTED, actor_id="reviewer",
        reason_code="POLICY_WITHDRAWN", evidence_ref="ticket/withdrawal",
        occurred_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(Exception, match="cannot be resolved"):
        source_repository.resolve(_locator(source, manifest.generation_id))


def test_manifest_rejects_two_revisions_for_same_logical_source():
    old = _draft("旧政策。")
    new = _draft("新政策。", supersedes=old.revision_id)
    with pytest.raises(Exception, match="conflicting revisions"):
        KnowledgeSourceManifest.build(
            tenant_id=old.tenant_id, backend_id="pg-knowledge-v1",
            generation_id="conflict-generation", scope="public", locale="zh-CN",
            product="", sources=(old, new), reviewer_manifest_ref="review/conflict",
        )
