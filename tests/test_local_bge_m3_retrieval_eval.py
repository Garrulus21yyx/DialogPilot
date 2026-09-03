from __future__ import annotations

import numpy as np

from types import SimpleNamespace

from evaluation.local_bge_m3_retrieval_eval import (
    bm25_matrix,
    colbert_scores,
    deterministic_query,
    fused_candidate_ids,
    sparse_matrix,
)


def test_bm25_prefers_document_with_matching_terms():
    scores = bm25_matrix(["blind earnings test"], [
        "password reset instructions", "blind earnings test special rule",
    ])
    assert scores.shape == (1, 2)
    assert scores[0, 1] > scores[0, 0]


def test_sparse_matrix_is_dot_product_over_learned_terms():
    scores = sparse_matrix(
        [{"1": 2.0, "2": 1.0}],
        [{"1": 3.0}, {"2": 4.0}, {"3": 99.0}],
    )
    np.testing.assert_allclose(scores, [[6.0, 4.0, 0.0]])


def test_user_history_excludes_prior_agent_utterances():
    case = SimpleNamespace(
        query="current user question",
        history=("first user", "first agent", "second user", "second agent"),
    )
    value = deterministic_query(case, "user_history")
    assert "first user" in value and "second user" in value
    assert "first agent" not in value and "second agent" not in value


def test_colbert_scores_use_mean_query_token_maxsim():
    query = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    documents = [
        np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        np.asarray([[-1.0, 0.0], [0.0, -1.0]], dtype=np.float32),
    ]
    scores = colbert_scores(query, documents, [0, 1], device="cpu")
    np.testing.assert_allclose(scores, [1.0, 0.0])


def test_fused_candidates_reward_consensus_without_losing_unique_ids():
    result = fused_candidate_ids(
        np.asarray([3.0, 2.0, 1.0]),
        np.asarray([1.0, 3.0, 2.0]),
        np.asarray([2.0, 3.0, 1.0]),
        ("a", "b", "c"),
        top_k=3,
    )
    assert result[0] == "b"
    assert set(result) == {"a", "b", "c"}
