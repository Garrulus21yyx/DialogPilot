from dataclasses import dataclass
from itertools import permutations
from types import SimpleNamespace

from scripts.run_rag_known_miss_replay import positions


def test_rank_diagnostic_requires_all_spans_and_correct_document():
    case=SimpleNamespace(evidence=[SimpleNamespace(document_id='d',start_char=0,end_char=5),SimpleNamespace(document_id='d',start_char=10,end_char=15)])
    rows=[dict(chunk_id='a',source_id='d',source_start_char=0,source_end_char=5),
          dict(chunk_id='b',source_id='d',source_start_char=10,source_end_char=15),
          dict(chunk_id='c',source_id='other',source_start_char=0,source_end_char=20)]
    for order in permutations(['a','b','c']):
        r=positions(case,rows,order)
        assert r['all_evidence_rank']==max(order.index('a'),order.index('b'))+1
        assert r['first_gold_document_rank']==min(order.index('a'),order.index('b'))+1
    assert positions(case,rows,['a','c'])['all_evidence_rank'] is None
    assert positions(case,rows,['c'])['first_gold_document_rank'] is None


def test_partial_span_overlap_is_not_full_evidence():
    case=SimpleNamespace(evidence=[SimpleNamespace(document_id='d',start_char=0,end_char=10)])
    row=dict(chunk_id='a',source_id='d',source_start_char=1,source_end_char=10)
    result=positions(case,[row],['a'])
    assert result['all_evidence_rank'] is None
    assert result['first_gold_document_rank']==1


def test_current_single_query_weights_exclude_dense_only_candidates():
    """Witness the configuration algebra, not a desired production invariant."""
    import random
    from mcp.rank_fusion import fuse_rankings
    rng=random.Random(17)
    lexical=tuple(f'l{i}' for i in range(20))
    for _ in range(50):
        dense=tuple(rng.sample(list(lexical)+[f'd{i}' for i in range(20)],20))
        fused=fuse_rankings({'dense':dense,'bm25':lexical},weights={'dense':.25,'bm25':.75},rrf_k=10,top_k=20)
        assert set(fused)==set(lexical)
    assert .25/(10+1) < .75/(10+20)
