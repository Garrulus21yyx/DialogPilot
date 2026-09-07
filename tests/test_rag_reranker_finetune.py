"""Metric and evidence-boundary invariants for the bounded adaptation pilot."""
import math

from scripts.run_rag_reranker_finetune import audit_splits, coverage, metrics, overlap, passage_groups


def test_partial_evidence_is_relevant_but_not_complete():
    row = dict(relevance=[1, 0, 1], coverage=[[0], [], [1]], total_relevant=2, span_count=2)
    report = metrics([row], [[0, 1]])
    assert report['mrr5'] == 1
    assert report['complete5'] == 0
    assert report['candidate_complete'] == 1
    assert report['ndcg5'] == 1 / (1 + 1 / math.log2(3))


def test_missing_gold_is_not_removed_from_ndcg_denominator():
    row = dict(relevance=[1, 0], coverage=[[0], []], total_relevant=2, span_count=2)
    result = metrics([row], [[0, 1]])
    assert 0 < result['ndcg5'] < 1
    assert result['candidate_complete'] == 0


def test_chunk_overlap_exclusion_and_containment_use_half_open_offsets():
    chunk = dict(start=10, end=20)
    assert not overlap(chunk, (0, 10))
    assert not overlap(chunk, (20, 30))
    assert overlap(chunk, (19, 21))
    assert coverage(chunk, [(10, 20), (9, 20), (10, 21)]) == [0]


def test_ranking_cannot_change_candidate_coverage():
    from itertools import permutations
    row = dict(relevance=[1, 0, 1], coverage=[[0], [], [1]], total_relevant=2, span_count=2)
    for order in permutations(range(3)):
        result = metrics([row], [order])
        assert result['candidate_complete'] == result['complete5'] == 1
        assert 0 < result['mrr5'] <= 1
        assert 0 <= result['ndcg5'] <= 1


def test_duplicate_passages_join_documents_transitively_ignoring_titles():
    chunks = [dict(doc='a', text='Title A\nSame   body'),
              dict(doc='b', text='Title B\nsame body'),
              dict(doc='b', text='Title B\nAnother body'),
              dict(doc='c', text='Title C\nanother body'),
              dict(doc='d', text='Title D\nDifferent passage')]
    groups = passage_groups(chunks)
    assert groups['a'] == groups['b'] == groups['c']
    assert groups['d'] != groups['a']
    from itertools import permutations
    for order in permutations(chunks):
        assert passage_groups(order) == groups


def test_split_audit_rejects_shared_body_even_with_different_group_labels():
    import pytest
    chunks = [dict(text='A\nshared body'), dict(text='B\nshared body')]
    rows = dict(train=[dict(group='a', positives=[0], negatives=[])],
                dev=[], test=[dict(group='b', positives=[1])])
    with pytest.raises(ValueError, match='duplicates'):
        audit_splits(chunks, rows)


def test_split_audit_accepts_disjoint_sources_and_bodies():
    chunks = [dict(text='A\nfirst body'), dict(text='B\nsecond body')]
    rows = dict(train=[dict(group='a', positives=[0], negatives=[])],
                dev=[], test=[dict(group='b', positives=[1])])
    assert audit_splits(chunks, rows)['exact_evaluation_positive_training_duplicates'] == 0
