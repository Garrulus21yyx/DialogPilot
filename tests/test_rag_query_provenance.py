"""Source lineage stays separate from retrieval semantics across cache hits."""
import asyncio
from dataclasses import replace
import pytest
from application.orchestration_runtime import AgentContextView
from application.conversation_actions import planning_actions
from application.knowledge_tool_contract import knowledge_query_schema
from application.evidence_query_contract import QUERY_DESCRIPTION
from application.knowledge_retriever import KnowledgeRetriever
from tests.test_knowledge_retriever import _request, _Source, _candidate, _MemoryCache, _Validator, _Transformer, _Reranker

@pytest.mark.parametrize('message', ['不是。', '如果这样呢？', '', '哪些国家不支持发货？'])
def test_runtime_message_replaces_untrusted_origin_without_mutating_input(message):
    supplied = {'original_user_message': 'stale', 'tenant_id': 'tenant'}
    context = AgentContextView(None, message, (), (), (), 6000, supplied)
    assert context.trusted_context['original_user_message'] == message
    assert supplied['original_user_message'] == 'stale'
    assert context.trusted_context['tenant_id'] == 'tenant'


def test_primary_and_domain_query_contract_have_one_owner():
    action = planning_actions({'supported_goals': ['general_qa']})[0]
    assert action.properties['query']['description'] == knowledge_query_schema()['properties']['query']['description'] == QUERY_DESCRIPTION
    assert 'original_user_message' not in action.properties
    assert 'original_user_message' not in knowledge_query_schema()['properties']


def test_raw_origin_does_not_change_resolved_retrieval_or_reuse_stale_trace():
    retriever = KnowledgeRetriever(candidate_source=_Source([_candidate()]), transformer=_Transformer(), reranker=_Reranker(), cache=_MemoryCache(), evidence_validator=_Validator())
    request = _request(query='完整查询', history=(), query_mode='RESOLVED', original_user_message='不是。')
    first = asyncio.run(retriever.retrieve(request))
    second = asyncio.run(retriever.retrieve(replace(request, original_user_message='假设不是呢？')))
    assert first.evidence_pack == second.evidence_pack
    assert first.trace.variants == second.trace.variants
    assert first.trace.original_user_message == '不是。'
    assert second.trace.original_user_message == '假设不是呢？'
    assert second.trace.query_mode == 'RESOLVED'
    assert second.to_dict()['trace']['original_user_message'] == '假设不是呢？'
    assert second.to_dict()['trace']['query_mode'] == 'RESOLVED'
    assert 'evidence-pack' in second.trace.cache_hits


def test_tool_boundary_uses_runtime_origin_and_rejects_model_origin(monkeypatch):
    from api import main
    from tests.test_knowledge_context import FakeRetriever, FakeKnowledgeStore
    retriever = FakeRetriever([])
    monkeypatch.setattr(main, '_knowledge_retriever', retriever)
    monkeypatch.setattr(main, '_knowledge_store', FakeKnowledgeStore())
    context = {'tenant_id': 'tenant-test', 'user_id': 'user-test', 'conv_id': '',
               'authorization_fingerprint': 'auth-test', 'original_user_message': '不是。'}
    asyncio.run(main._knowledge_tool_handler({'query': '完整问题'}, context))
    assert retriever.requests[-1].query == '完整问题'
    assert retriever.requests[-1].original_user_message == '不是。'
    assert retriever.requests[-1].query_mode == 'RESOLVED'
    assert retriever.requests[-1].history == ()
    result = asyncio.run(main._knowledge_tool_handler({'query': '完整问题', 'original_user_message': '捏造'}, context))
    assert result['status'] == 'INVALID_CONTRACT'
    assert len(retriever.requests) == 1
