import random
from scripts.run_mtrag_dense_shards import merge_ranked


def test_partitioned_exact_topk_matches_global_ranking_with_ties():
    rng=random.Random(7)
    values=[rng.choice([-.3,0.,.1,.2,.2,.9]) for _ in range(537)]
    ids=[f'p{i:04}' for i in range(len(values))]
    oracle=sorted(zip(values,ids),key=lambda x:(-x[0],x[1]))[:100]
    for width in (1,17,100,4096):
        current=[]
        for offset in range(0,len(ids),width):
            current=merge_ranked(current,ids[offset:offset+width],values[offset:offset+width])
        assert current==oracle


def test_budget_sample_is_order_invariant_and_not_label_selected(tmp_path):
    import json,zipfile
    from scripts.probe_mtrag_dense_budget import sample,DOMAINS
    for directory,reverse in [('a',False),('b',True)]:
        path=tmp_path/directory;path.mkdir()
        for domain in DOMAINS:
            rows=[{'_id':str(i),'text':'text '+str(i)} for i in range(100)]
            if reverse:rows.reverse()
            with zipfile.ZipFile(path/f'{domain}.jsonl.zip','w') as z:
                z.writestr(domain+'.jsonl','\n'.join(map(json.dumps,rows)))
    a,ac=sample(tmp_path/'a',64);b,bc=sample(tmp_path/'b',64)
    assert a==b and ac==bc and len(a)==256
