"""Generated scoped BM25 equals a scalar oracle across immutable revisions."""
from collections import Counter
from dataclasses import replace
import math
import random

import pytest

from tests.test_hybrid_retrieval_backends import backend_foundation, _generation, _knowledge_request, SHA, _vector
from application.chinese_lexical import postgres_lexical_document
from application.hybrid_retrieval import GenerationState, KnowledgeSearchScope, RetrievalStatus
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend


def test_generated_scoped_bm25_scores_match_scalar_formula(backend_foundation):
    platform, retrieval = backend_foundation
    registry = PostgresRetrievalGenerationRegistry(platform)
    rng = random.Random(113)
    base = []
    for i in range(35):
        text = ' '.join(rng.choices(['refund','policy','pending','approved','退款','sku-17'], k=i%13))
        base.append((i, 'tenant-b' if i%5==0 else 'tenant-a', 'local' if i%2 else 'global',
                     postgres_lexical_document(text).split()))
    # Revision changes are new immutable generation projections, not UPDATEs.
    # Old generations stay present: their tokens must not affect new statistics.
    for phase in range(3):
        generation = registry.register(replace(_generation(f'bm25-property-{phase}'),
            backend_id='POSTGRES_PG_BM25_ZH_V1', lexical_ranker='PG_BM25_ZH_V1'))
        registry.transition(generation.generation_id, GenerationState.BUILDING)
        rows = []
        with platform.transaction() as c:
            for i, tenant, region, original in base:
                if phase == 2 and i%4 == 0:
                    continue  # Source omitted from the next generation.
                terms = (postgres_lexical_document('退款 refund refund newterm').split()
                         if phase and i%3 == 0 else original)
                key = f'generated-{phase}-{i:02}'
                rows.append((key, tenant, region, terms))
                c.execute('''INSERT INTO retrieval.knowledge_chunk_search
                    (candidate_id,tenant_id,backend_id,generation_id,source_id,source_revision,source_checksum,source_span,provenance_sha256,scope,locale,product,deletion_epoch,source_type,region,embedding,lexical_document,projected_at)
                    VALUES (%s,%s,%s,%s,%s,'r1',%s,'{"start":0,"end":2}'::jsonb,%s,'public','zh-CN','payments',0,'text',%s,%s::vector,%s,now())''',
                    (key,tenant,generation.backend_id,generation.generation_id,key,SHA,SHA,region,_vector(0),' '.join(terms)))
        registry.transition(generation.generation_id, GenerationState.READY)
        for regions in [(), ('local',), ('global',)]:
            selected = [(key,terms) for key,tenant,region,terms in rows
                        if tenant=='tenant-a' and (not regions or region in regions)]
            avg = max(sum(len(x) for _,x in selected)/len(selected), 1.)
            for query in ['refund','refund refund policy','退款 pending','sku-17 approved','missing-token','newterm']:
                expected = []
                for key, terms in selected:
                    counts = Counter(terms)
                    score = 0.
                    for term in set(postgres_lexical_document(query).split()):
                        tf = counts[term]
                        if tf:
                            df = sum(term in t for _,t in selected)
                            score += math.log(1+(len(selected)-df+.5)/(df+.5))*tf*2.2/(tf+1.2*(.25+.75*len(terms)/avg))
                    if score:
                        expected.append((key,score))
                request = replace(_knowledge_request(generation),query_text=query,query_embedding=None,
                    dense_limit=0,lexical_limit=100,
                    scope=KnowledgeSearchScope('public','zh-CN','payments',('text',),regions))
                backend = PostgresHybridBackend(retrieval)
                result = backend.retrieve(request)
                assert result.status is (RetrievalStatus.OK if expected else RetrievalStatus.NO_EVIDENCE)
                actual = result.lexical_candidates
                assert {r.candidate_id:r.score for r in actual} == pytest.approx(dict(expected),abs=1e-9)
                assert [r.score for r in actual] == sorted([r.score for r in actual],reverse=True)
                reverse_query = ' '.join(reversed(query.split()))
                reordered = backend.retrieve(replace(request,query_text=reverse_query)).lexical_candidates
                assert [(r.candidate_id,r.score) for r in actual] == [(r.candidate_id,r.score) for r in reordered]
                limited = backend.retrieve(replace(request,lexical_limit=3)).lexical_candidates
                assert [(r.candidate_id,r.score) for r in limited] == [(r.candidate_id,r.score) for r in actual[:3]]
