import copy
import pytest
from scripts.replay_mtrag_hybrid_weights import paired_rows,report


def inputs():
    base={'case_id':'q','group_id':'g','domain':'cloud','query':'fixed query','gold':['mtrag:cloud:d0']}
    d={**base,'ranking':[{'id':f'mtrag:cloud:d{i}'} for i in range(100)]}
    b={**base,'mode':'rewrite','ranking':[{'id':f'mtrag:cloud:b{i}'} for i in range(20)]}
    return [d],[b]


def test_budget_pure_routes_and_weight_exclusion_are_visible():
    ds,bs=inputs();r=paired_rows(ds,bs)[0]
    assert r['variants']['0.0']['ranking']==[x['id'] for x in bs[0]['ranking']]
    assert r['variants']['1.0']['ranking']==[x['id'] for x in ds[0]['ranking'][:20]]
    assert r['variants']['0.25']['metrics']['recall@20']==0
    assert r['union_diagnostic']['recall']==1 and r['union_diagnostic']['budget']==40
    assert all(len(v['ranking'])==20 for v in r['variants'].values())
    assert report([r])['summary']['all']['1.0']['recall@20']==1


@pytest.mark.parametrize('field',['query','group_id','domain','gold'])
def test_changed_input_cannot_be_reported_as_paired(field):
    ds,bs=inputs();bs=copy.deepcopy(bs);bs[0][field]=['different'] if field=='gold' else 'different'
    with pytest.raises(ValueError,match='mismatch'):paired_rows(ds,bs)
