"""Authoritative bounded runtime policy for the frozen customer-support RAG chain."""
from __future__ import annotations

from typing import Any, Mapping


DEFAULT_RAG_RETRIEVAL_POLICY: Mapping[str, Any] = {
    "top_k": 5,
    "candidate_k": 20,
    "context_max_tokens": 2600,
    "rrf_k": 10,
    "vector_weight": 0.25,
    "lexical_weight": 0.75,
    "raw_query_weight": 0.25,
    "standalone_query_weight": 0.75,
}


def rag_retrieval_policy_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Build the bootstrap Bundle policy; the Bundle owns request-time values."""
    defaults = DEFAULT_RAG_RETRIEVAL_POLICY
    return {
        "top_k": int(env.get("RAG_TOP_K", defaults["top_k"])),
        "candidate_k": int(env.get("RAG_CANDIDATE_K", defaults["candidate_k"])),
        "context_max_tokens": int(env.get("RAG_CONTEXT_MAX_TOKENS", defaults["context_max_tokens"])),
        "rrf_k": int(env.get("RAG_RRF_K", defaults["rrf_k"])),
        "vector_weight": float(env.get("RAG_VECTOR_WEIGHT", defaults["vector_weight"])),
        "lexical_weight": float(env.get("RAG_LEXICAL_WEIGHT", defaults["lexical_weight"])),
        "raw_query_weight": float(env.get("RAG_RAW_QUERY_WEIGHT", defaults["raw_query_weight"])),
        "standalone_query_weight": float(
            env.get("RAG_STANDALONE_QUERY_WEIGHT", defaults["standalone_query_weight"])
        ),
    }
