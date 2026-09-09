import json
import pytest
from scripts.run_ecommerce_pure_rag import build
from scripts.report_ecommerce_pure_rag import metrics


def test_subset_keeps_group_split_and_excludes_business(tmp_path):
    root=tmp_path/'data';build(root)
    inputs=json.loads((root/'inputs.json').read_text());labels=json.loads((root/'labels.json').read_text())
    assert len(inputs)==100
    assert all(set(c)=={'id','group_id','category','message','history'} for c in inputs)
    assert all('knowledge_sources' not in c and 'answer_requirements' not in c for c in inputs)
    assert all(c['category']!='mixed' and not c['business_required'] for c in labels)
    assert sum(c['original_split']=='dev' for c in labels)==34
    assert sum(c['original_split']=='heldout' for c in labels)==66
    groups={}
    for c in labels:groups.setdefault(c['group_id'],set()).add(c['original_split'])
    assert len(groups)==50 and all(len(s)==1 for s in groups.values())


def test_source_metrics_deduplicate_and_retain_misses():
    assert metrics([],['gold'])==dict(recall5=0,recall20=0,mrr5=0,ndcg5=0)
    assert metrics(['noise','noise','gold'],['gold'])['mrr5']==.5
    assert metrics(['a','b','c','d','e','gold'],['gold'])['recall5']==0
    assert metrics(['a','b','c','d','e','gold'],['gold'])['recall20']==1
    assert metrics(['g1'],['g1','g2'])['recall5']==.5


def test_rewrite_replay_requires_identical_history_and_query(tmp_path):
    import asyncio,gzip
    from evaluation.ecommerce_pure_rag import ReplayTransformer
    row={'arm':'standalone_raw','original':'不是','history':[['assistant','是质量问题吗？']],
         'query':'非质量原因退货','rewrite_error':None}
    p=tmp_path/'queries.gz';p.write_bytes(gzip.compress((json.dumps(row)+'\n').encode()))
    replay=ReplayTransformer(p)
    assert asyncio.run(replay.standalone('不是',['assistant: 是质量问题吗？']))==('非质量原因退货',None)
    with pytest.raises(KeyError):asyncio.run(replay.standalone('不是',['assistant: 是七天内吗？']))


def test_capture_and_retrieval_share_one_identical_transform():
    import asyncio
    from evaluation.ecommerce_pure_rag import MemoTransformer
    class Delegate:
        calls=0
        async def standalone(self, query, history):
            self.calls+=1
            return query+' resolved',None
    async def check():
        delegate=Delegate();memo=MemoTransformer(delegate)
        first=await memo.standalone('否',['A'])
        assert await memo.standalone('否',['A'])==first
        assert delegate.calls==1
        await memo.standalone('否',['B'])
        assert delegate.calls==2
    asyncio.run(check())


@pytest.mark.parametrize('filtered_only', [False, True])
def test_scope_pair_preserves_query_budget_and_snapshot(tmp_path, filtered_only):
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from evaluation.ecommerce_pure_rag import run
    calls=[]
    async def retrieve(query,**options):
        calls.append((query,options))
        return SimpleNamespace(to_dict=lambda **kwargs:{'status':'NO_EVIDENCE','evidence_pack':None})
    class Transformer:
        async def standalone(self,query,history):return query,None
    path=tmp_path/'inputs.json'
    path.write_text(json.dumps([{'id':'x','message':'我只问2026年6月1日起中国大陆官网现行规则，不问经销商或香港渠道','history':[]}]))
    snapshot=datetime(2026,9,10,tzinfo=timezone.utc)
    asyncio.run(run(inputs=path,output=tmp_path,retrieve=retrieve,client=SimpleNamespace(calls=[]),policy=None,
        source=SimpleNamespace(records=[]),reranker=SimpleNamespace(records=[],version='test'),transformer=Transformer(),scope_pair=True,
        only_scope_filtered=filtered_only,as_of=snapshot))
    assert all(options['as_of']==snapshot for _,options in calls)
    if filtered_only:
        assert len(calls)==1
        assert calls[0][1]['applicable_channel']=='web'
        assert calls[0][1]['applicable_region']=='CN'
        return
    a,b=calls
    assert a[0]==b[0]
    assert a[1]['history']==b[1]['history']
    assert a[1]['as_of']==b[1]['as_of']
    assert a[1]['policy_values']==b[1]['policy_values']
    assert 'applicable_channel' not in a[1]
    assert b[1]['applicable_channel']=='web' and b[1]['applicable_region']=='CN'
    assert 'applicable_product' not in b[1]


def test_scope_fixture_uses_user_history_but_not_assistant_assertions():
    from evaluation.ecommerce_pure_rag import scope_fixture_options
    text='我只问2026年6月1日起中国大陆官网现行规则，不问经销商或香港渠道'
    assert scope_fixture_options({'message':'按刚才的条件呢？','history':[['user',text]]})=={'applicable_region':'CN','applicable_channel':'web'}
    with pytest.raises(ValueError):
        scope_fixture_options({'message':'按刚才的条件呢？','history':[['assistant',text]]})
    from pathlib import Path
    for split in ('dev','heldout'):
        for case in json.loads(Path(f'data/eval/ecommerce-complex-v2/{split}.inputs.json').read_text()):
            assert scope_fixture_options(case)=={'applicable_region':'CN','applicable_channel':'web'}
