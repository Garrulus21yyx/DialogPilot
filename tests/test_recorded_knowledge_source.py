from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_retriever import KnowledgeCandidateResult
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource, _CollectedSources
from evaluation.recorded_knowledge_source import RecordedKnowledgeSource
from mcp.rank_fusion import fuse_rankings


def request(query):
    return SimpleNamespace(query=query, as_of_end=None, manifest_fingerprint='manifest',
        policy=SimpleNamespace(dense_weight=.25, lexical_weight=.75, rrf_k=10))


def source():
    return RecordedKnowledgeSource(backend=None, generations=None, pool=None, embed_query=lambda q: None)


@pytest.mark.parametrize('queries', [['a'], ['a','b','c','d']])
def test_complete_route_pool_replays_same_fusion_without_second_search(queries):
    seen=[]
    def collect(self, req, variants, **kwargs):
        seen.append(req.query)
        ids=[req.query+str(i) for i in range(4)]
        return _CollectedSources(SimpleNamespace(), {i:('source','revision','checksum') for i in ids},
            {'raw:vector':{ids[0]:1,ids[1]:2}, 'raw:bm25':{ids[2]:1,ids[3]:2}},
            {'raw:vector':.25,'raw:bm25':.75})
    def load(self,req,generation,ids):
        return {i:{'chunk_id':i,'text':i+' evidence'} for i in ids}
    recorder=source()
    with patch.object(PostgresKnowledgeCandidateSource,'_collect_sources',collect), patch.object(PostgresKnowledgeCandidateSource,'_load_rows',load):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda q:recorder._search(request(q),[('raw',q,1.0)],2),queries))
    assert sorted(seen)==sorted(queries)
    assert all(r.status is RetrievalStatus.OK for r in results)
    for record in recorder.records:
        assert record['projection_complete'] and record['selected_projection_matches']
        assert len(record['source_union'])==4 and len(record['fused_candidates'])==2
        assert all(i.startswith(record['query']) for i in record['source_union'])
        rankings={route:tuple(sorted(ranks,key=ranks.get)) for route,ranks in record['route_ranks'].items()}
        replay=fuse_rankings(rankings,weights=record['route_weights'],rrf_k=record['rrf_k'],top_k=record['top_k'])
        assert list(replay)==[c['chunk_id'] for c in record['fused_candidates']]


def test_typed_source_failure_is_recorded_without_fabricating_a_pool():
    recorder=source()
    with patch.object(PostgresKnowledgeCandidateSource,'_collect_sources',return_value=KnowledgeCandidateResult(RetrievalStatus.UNAVAILABLE,detail_code='GENERATION_UNAVAILABLE')):
        result=recorder._search(request('a'),[('raw','a',1.0)],2)
    assert result.status is RetrievalStatus.UNAVAILABLE
    assert recorder.records[0]['detail_code']=='GENERATION_UNAVAILABLE'
    assert 'source_union' not in recorder.records[0]
