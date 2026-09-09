import math
import pytest
from scripts.compare_pg_native_fts import metrics


def test_article_metrics_do_not_count_duplicate_chunks_as_multiple_answers():
    sources={'a1':'a','a2':'a','b1':'b','c1':'c'}
    result=metrics(['a1','a2','c1','b1'],['a','b'],sources)
    assert result['article_recall_at20_chunks']==1
    assert result['all_articles_at20_chunks']==1
    assert result['article_mrr']==1
    assert result['article_ndcg_at5']==pytest.approx((1+1/math.log2(4))/(1+1/math.log2(3)))


def test_missing_article_counts_as_incomplete_even_when_one_answer_present():
    result=metrics(['x','y'],['a','b'],{'x':'c','y':'a'})
    assert result['article_recall_at20_chunks']==.5
    assert result['all_articles_at20_chunks']==0
    assert result['article_mrr']==.5
    assert metrics([],['a'],{})['article_ndcg_at5']==0
