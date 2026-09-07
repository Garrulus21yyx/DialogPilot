import asyncio
from dataclasses import replace
from types import SimpleNamespace
import pytest
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from infrastructure.knowledge_retriever_adapters import ToolManagerRerankerAdapter,configured_knowledge_reranker
from application.knowledge_retriever import KnowledgeRetriever,RetrievalCacheKeyBuilder
from tests.test_knowledge_retriever import _request
from core.model_policy import ModelProfile


@pytest.fixture
def local(tmp_path):
    for name in ['config.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json','sentencepiece.bpe.model']:
        (tmp_path/name).write_text('{}')
    return LocalKnowledgeReranker(tmp_path)


def test_scores_preserve_all_ids_full_text_and_tie_order(local,monkeypatch):
    seen=[]
    def score(query,texts):
        seen.append((query,texts));return [0,2,2]
    monkeypatch.setattr(local,'_score',score)
    rows=[{'chunk_id':str(i),'title':'标题','content':'正文'*2000+'末尾例外'} for i in range(3)]
    ids,fallback=asyncio.run(local.rerank('完整查询',rows))
    assert ids==('1','2','0') and not fallback
    assert all(text.endswith('末尾例外') for text in seen[0][1])
    assert seen[0][0]=='完整查询'


@pytest.mark.parametrize('scores', [[float('nan'),1],[1],[float('inf'),1]])
def test_invalid_model_scores_preserve_fallback_contract(local,monkeypatch,scores):
    monkeypatch.setattr(local,'_score',lambda *args:scores)
    assert asyncio.run(local.rerank('q',[{'chunk_id':'a','content':'a'},{'chunk_id':'b','content':'b'}])) == (('a','b'),True)


def test_artifact_identity_changes_with_weights_and_tokenizer(local):
    old=local.version
    (local.path/'tokenizer.json').write_text('{"changed":true}')
    assert LocalKnowledgeReranker(local.path).version != old
    assert LocalKnowledgeReranker(local.path,max_tokens=4096).version != LocalKnowledgeReranker(local.path).version


def test_model_identity_is_checked_before_cache_or_candidate_access(local):
    class Source:
        async def search_variants_async(self,*args,**kwargs):pytest.fail('identity mismatch reached retrieval')
    retriever=KnowledgeRetriever(candidate_source=Source(),transformer=None,reranker=local)
    result=asyncio.run(retriever.retrieve(_request()))
    assert result.detail_code=='RERANKER_IDENTITY_MISMATCH'
    a=_request(policy=replace(_request().policy,reranker_version=local.version))
    b=replace(a,policy=replace(a.policy,reranker_version='other'))
    assert RetrievalCacheKeyBuilder.rerank(a,[]) != RetrievalCacheKeyBuilder.rerank(b,[])


def test_listwise_identity_includes_actual_model():
    from mcp.result_reranker import ResultReranker
    def adapter(model):return ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(None,ModelProfile(model),structured_agent=object())))
    assert adapter('one').version != adapter('two').version
    with pytest.raises(ValueError):configured_knowledge_reranker(None,{'RAG_RERANKER':'invalid'})


def test_local_cancellation_propagates(local,monkeypatch):
    def cancel(*args):raise asyncio.CancelledError()
    monkeypatch.setattr(local,'_score',cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(local.rerank('q',[{'chunk_id':'a','content':'a'}]))


def test_local_order_is_a_permutation_for_all_score_orders(local,monkeypatch):
    from itertools import permutations
    rows=[{'chunk_id':str(i),'content':'text'} for i in range(3)]
    for scores in permutations(range(3)):
        monkeypatch.setattr(local,'_score',lambda *args:scores)
        ids,fallback=asyncio.run(local.rerank('q',rows))
        assert not fallback and set(ids)=={'0','1','2'} and len(ids)==3
        assert ids==tuple(str(i) for i in sorted(range(3),key=lambda i:-scores[i]))


def test_over_budget_model_input_returns_explicit_fallback_without_scoring(local):
    import torch
    local._model=lambda **kw:pytest.fail('over-budget input reached model')
    local._tokenizer=lambda *args,**kwargs:{'input_ids':torch.zeros((1,8193),dtype=torch.long)}
    assert asyncio.run(local.rerank('q',[{'chunk_id':'a','content':'text'}])) == (('a',),True)
