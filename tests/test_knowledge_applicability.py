"""Cross-owner temporal/source applicability acceptance against real PostgreSQL."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.test_postgres_knowledge_store import store
from tests.test_postgres_knowledge_retriever import _request
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.retrieval_postgres import RetrievalPoolConfig, RetrievalPostgresPool
from mcp.source_document import SourceDocument
from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_source import KnowledgeSourceContractError


def instant(year):
    return datetime(year, 1, 1, tzinfo=timezone.utc)


def test_source_update_time_facets_withdrawal_and_cache_reread(store, postgres_database_url):
    knowledge, pool, _ = store
    old = SourceDocument.create(source_id='policy',title='退款政策',content='旧版退款期限七天。', effective_from=instant(2023))
    first = knowledge.import_documents((old,))
    newer = replace(old, content='新版退款期限三天。', checksum=SourceDocument.content_checksum('新版退款期限三天。'), effective_from=instant(2025), effective_to=instant(2027))
    second = knowledge.import_documents((newer,))
    assert knowledge.import_documents((newer,)).revisions == second.revisions
    eu = SourceDocument.create(source_id='eu-policy',title='退款政策',content='欧洲渠道退款规则。', region='EU', channel='web', product='headphones', effective_from=instant(2022))
    knowledge.import_documents((eu,))
    generation = knowledge.active_generation()
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(postgres_database_url,min_size=1,max_size=3))
    retrieval.open()
    source = PostgresKnowledgeCandidateSource(backend=PostgresHybridBackend(retrieval),generations=knowledge._generations,pool=retrieval,embed_query=knowledge.embed_query)
    request = replace(_request(), generation_id=generation.generation_id, manifest_fingerprint=generation.manifest_hash,
        policy=replace(_request().policy, backend_fingerprint=generation.backend_fingerprint, lexical_provider=generation.lexical_ranker, embedding_version=generation.embedding_profile.fingerprint),
        as_of=instant(2024), applicable_region='CN')
    def search(req):
        return asyncio.run(source.search_variants_async(req,[('raw','退款规则',1.0)],top_k=20))
    try:
        historical = search(request)
        assert historical.status is RetrievalStatus.OK
        assert {row['source_revision'] for row in historical.candidates} == {first.revisions[0].revision_id}
        current_request = replace(request,as_of=instant(2026))
        assert search(replace(request, as_of=instant(2022))).status is RetrievalStatus.NO_EVIDENCE
        assert search(replace(request, as_of=instant(2027))).status is RetrievalStatus.NO_EVIDENCE
        assert {row['source_revision'] for row in search(replace(request, as_of=instant(2025))).candidates} == {second.revisions[0].revision_id}
        current = search(current_request)
        assert {row['source_revision'] for row in current.candidates} == {second.revisions[0].revision_id}
        assert current.candidates[0]['applicability']['effective_from'] == instant(2025).isoformat()
        assert source.validate_candidates(current.candidates,current_request)
        for region, channel, product, expected in [('EU','web','headphones',2),('EU','store','headphones',1),('EU','web','other',1),('CN','web','headphones',1)]:
            result = search(replace(current_request,applicable_region=region,applicable_channel=channel,applicable_product=product))
            assert len(result.candidates) == expected
        from mcp.evidence_pack import EvidencePack, EvidenceItem, SourceReference
        row = current.candidates[0]
        packed = EvidencePack('退款规则', generation.manifest_hash, (), (EvidenceItem(
            row['chunk_id'], row['title'], SourceReference(row['source_id'],row['source_revision'],
            row['source_start_char'],row['source_end_char'],row['source_type'],row['source_checksum']),
            1.0,1,(), 'allowed_public',row['content']),))
        wire = {'status':'OK','evidence_pack':packed.to_dict(include_text=True)}
        assert knowledge.validate_publication_evidence([wire])
        knowledge.withdraw_revision('policy',second.revisions[0].revision_id,reason='incorrect policy')
        assert not knowledge.validate_publication_evidence([wire])
        knowledge.withdraw_revision('policy',second.revisions[0].revision_id,reason='retry')
        assert not source.validate_candidates(current.candidates,current_request)
        assert search(current_request).status is RetrievalStatus.NO_EVIDENCE
        assert search(request).status is RetrievalStatus.OK
        with pytest.raises(KnowledgeSourceContractError):
            knowledge.import_documents((newer,))
        with pool.transaction() as connection:
            import psycopg
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                connection.execute("UPDATE retrieval.knowledge_source_revisions SET withdrawn_at=NULL,withdrawal_reason=NULL WHERE withdrawn_at IS NOT NULL")
    finally:
        retrieval.close()


def test_manifest_versions_are_closed_and_identity_bearing(store):
    from application.knowledge_source import KnowledgeSourceManifest
    knowledge, _, _ = store
    document = SourceDocument.create(source_id='s',title='t',content='text')
    revision = knowledge.import_documents((document,)).revisions[0]
    kwargs = dict(tenant_id='tenant-a',backend_id='b',generation_id='g',scope='public',locale='zh-CN',product='',sources=(revision,),reviewer_manifest_ref='test')
    old = KnowledgeSourceManifest.build(**kwargs)
    new = KnowledgeSourceManifest.build(**kwargs,schema_version='knowledge-source-temporal-v2')
    assert old.manifest_hash != new.manifest_hash
    with pytest.raises(KnowledgeSourceContractError):
        KnowledgeSourceManifest.build(**kwargs,schema_version='UNKNOWN')
