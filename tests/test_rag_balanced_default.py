"""Default fusion retains complementary top hits under the runtime budget."""
import pytest
from core.rag_policy import rag_retrieval_policy_from_env, validate_rag_policy
from mcp.rank_fusion import fuse_rankings

@pytest.mark.parametrize('prefixes', [('a','z'),('z','a')])
def test_default_retains_both_routes_when_candidates_are_disjoint(prefixes):
    p=rag_retrieval_policy_from_env({})
    assert validate_rag_policy({})==p
    routes={name:[f'{prefix}-{i:02}' for i in range(20)] for name,prefix in zip(('dense','bm25'),prefixes)}
    ids=fuse_rankings(routes,weights={'dense':p['vector_weight'],'bm25':p['lexical_weight']},rrf_k=p['rrf_k'],top_k=p['candidate_k'])
    assert len(ids)==20 and routes['dense'][0] in ids and routes['bm25'][0] in ids

def test_explicit_weights_remain_authoritative():
    p=rag_retrieval_policy_from_env({'RAG_VECTOR_WEIGHT':'0.25','RAG_LEXICAL_WEIGHT':'0.75'})
    assert p['vector_weight']==.25 and p['lexical_weight']==.75
    assert validate_rag_policy(p)==p
