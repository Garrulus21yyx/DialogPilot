"""Cross-boundary properties for query contracts, temporal granularity and caches."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from itertools import permutations
import asyncio
import jsonschema
import pytest

from application.conversation_agent import planning_output_schema, _KNOWLEDGE_GOALS
from application.knowledge_tool_contract import knowledge_query_options, knowledge_query_options_schema, knowledge_query_schema, knowledge_time_window
from application.knowledge_retriever import RetrievalCacheKeyBuilder
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.document_chunker import DocumentChunker, ChunkStrategy
from tests.test_knowledge_retriever import _request


@pytest.mark.parametrize('kind', sorted(_KNOWLEDGE_GOALS))
@pytest.mark.parametrize('query', [None, '', '  ', '不是质量问题的拆封耳机退货规则'])
def test_every_knowledge_goal_schema_requires_query(kind, query):
    goal = {'kind': kind}
    if query is not None:
        goal['resolved_query'] = query
    value = {'status':'resolved', 'goals':[goal]}
    if query and query.strip():
        jsonschema.validate(value, planning_output_schema())
    else:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, planning_output_schema())


@pytest.mark.parametrize('options', [
    {'policy_date':'2026-03-01'}, {'as_of':'2026-03-01T10:30:00+08:00'}, {},
    {'applicable_region':'CN', 'policy_date':'2026-03-01'},
])
def test_options_share_planner_tool_and_parser_contract(options):
    jsonschema.validate(options, knowledge_query_options_schema())
    jsonschema.validate({'query':'政策条件', **options}, knowledge_query_schema())
    assert knowledge_query_options(knowledge_query_options(options)) == knowledge_query_options(options)


@pytest.mark.parametrize('options', [{'as_of':'2026-03-01'},
    {'as_of':'2026-03-01T12:00:00'}, {'policy_date':'2026-03-01','as_of':'2026-03-01T00:00:00Z'}])
def test_ambiguous_temporal_wire_values_rejected_at_both_boundaries(options):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(options, knowledge_query_options_schema())
    with pytest.raises(ValueError):
        knowledge_query_options(options)


@pytest.mark.parametrize('day,hours', [('2026-03-29',23),('2026-10-25',25),('2026-03-01',24)])
def test_dates_preserve_calendar_day_in_host_owned_zone(day, hours):
    start, end = knowledge_time_window({'policy_date':day}, current=None, business_timezone='Europe/Berlin')
    assert end - start == timedelta(hours=hours)
    assert start.utcoffset() == timedelta(0)
    with pytest.raises(ValueError):
        knowledge_time_window({'policy_date':day}, current=None, business_timezone=None)


def test_listwise_cache_tracks_actual_order_and_text():
    candidates = [ContextCandidate(str(i),'doc',str(i),0,1,source_revision='v1') for i in range(3)]
    request = _request()
    keys = {RetrievalCacheKeyBuilder.rerank(request, rows) for rows in permutations(candidates)}
    assert len(keys) == 6
    assert RetrievalCacheKeyBuilder.rerank(request,candidates) != RetrievalCacheKeyBuilder.rerank(request,[replace(candidates[0],text='other'),*candidates[1:]])
    assert RetrievalCacheKeyBuilder.candidates(request,()) != RetrievalCacheKeyBuilder.candidates(replace(request,as_of_end=request.as_of+timedelta(days=1)),())


def test_packer_redundancy_preserves_revision_identity():
    one = ContextCandidate('1','doc','old',0,3,source_revision='old')
    two = replace(one,chunk_id='2',text='new',source_revision='new')
    packer = ContextPacker()
    assert packer.pack([one,two],max_tokens=100,max_chunks=5).chunk_ids == ('1','2')
    assert packer.pack([one,replace(one,chunk_id='duplicate')],max_tokens=100,max_chunks=5).chunk_ids == ('1',)


@pytest.mark.parametrize('strategy',list(ChunkStrategy))
def test_generated_chunk_boundaries_preserve_every_original_character(strategy):
    import random
    rng = random.Random(7103)
    chunker = DocumentChunker()
    for _ in range(60):
        text = ''.join(rng.choice(['退货不能保证到账。','型号 A-17 不是 A17。','\n\n','  ','😀']) for _ in range(80))
        chunks = chunker.split(text,max_tokens=64,overlap_tokens=8,strategy=strategy,source_type='text')
        covered = set()
        for chunk in chunks:
            assert chunk.content == text[chunk.start_char:chunk.end_char]
            covered.update(range(chunk.start_char,chunk.end_char))
        assert covered == set(range(len(text)))


def test_cache_identity_does_not_assume_model_case_or_whitespace_invariance():
    request = _request(query='型号 Aa-17')
    changed = replace(request,query='型号 aa-17')
    assert RetrievalCacheKeyBuilder.transform(request) != RetrievalCacheKeyBuilder.transform(changed)
    assert RetrievalCacheKeyBuilder.candidates(request,[('raw',request.query,1)]) != RetrievalCacheKeyBuilder.candidates(changed,[('raw',changed.query,1)])


def test_unrewritten_query_keeps_base_weight_and_discards_blank_expansion():
    from tests.test_knowledge_retriever import _Source, _Reranker, _policy
    from application.knowledge_retriever import KnowledgeRetriever
    class Transformer:
        async def standalone(self,q,h): return q,None
        async def expand(self,q,*,n): return (' ', '其他表达'),None
    source = _Source()
    request = _request(policy=_policy(raw_query_weight=0,standalone_query_weight=.8,expansion_query_weight=.2,query_expansion_count=2))
    result = asyncio.run(KnowledgeRetriever(candidate_source=source,transformer=Transformer(),reranker=_Reranker()).retrieve(request))
    assert result.trace.variants == (('raw',request.query,.8),('expansion-1','其他表达',.2))


def test_calendar_upper_boundary_is_a_typed_contract_failure():
    with pytest.raises(ValueError,match='supported datetime'):
        knowledge_time_window({'policy_date':'9999-12-31'},current=None,business_timezone='UTC')


def test_bundle_and_retriever_defaults_share_one_owner():
    from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY
    from tests.test_knowledge_retriever import _policy
    assert {k:_policy().legacy_mapping()[k] for k in DEFAULT_RAG_RETRIEVAL_POLICY} == DEFAULT_RAG_RETRIEVAL_POLICY


def test_generated_full_overlap_dedup_never_loses_source_coverage_without_budget_pressure():
    import random
    rng = random.Random(713)
    text = ''.join(chr(65+i%26) for i in range(80))
    for _ in range(100):
        candidates=[]
        for i in range(20):
            start=rng.randrange(len(text)); end=rng.randrange(start+1,len(text)+1)
            candidates.append(ContextCandidate(str(i),'same-source',text[start:end],start,end,source_revision='v1'))
        selected=ContextPacker().pack(candidates,max_tokens=5000,max_chunks=20).selected
        def covered(items):return {j for item in items for j in range(item.start_char,item.end_char)}
        assert covered(candidates)==covered(selected)
