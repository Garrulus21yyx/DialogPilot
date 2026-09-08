"""Authoritative bounded runtime policy for the frozen customer-support RAG chain."""
from __future__ import annotations

from typing import Any, Mapping
import math


DEFAULT_RAG_RETRIEVAL_POLICY: Mapping[str, Any] = {
    "top_k": 5,
    "candidate_k": 20,
    "context_max_tokens": 2600,
    "rrf_k": 10,
    "vector_weight": 0.50,
    "lexical_weight": 0.50,
    "raw_query_weight": 0.20,
    "standalone_query_weight": 0.60,
    "expansion_query_weight": 0.0,
    "query_expansion_count": 0,
    "metadata_hint_weight": 0.50,
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
        "expansion_query_weight": float(
            env.get("RAG_EXPANSION_QUERY_WEIGHT", defaults["expansion_query_weight"])
        ),
        "query_expansion_count": int(
            env.get("RAG_QUERY_EXPANSION_COUNT", defaults["query_expansion_count"])
        ),
        "metadata_hint_weight": float(
            env.get("RAG_METADATA_HINT_WEIGHT", defaults["metadata_hint_weight"])
        ),
    }


def validate_rag_policy(values: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve supported partial policies against one authoritative default set."""
    unknown = set(values) - set(DEFAULT_RAG_RETRIEVAL_POLICY)
    if unknown:
        raise ValueError(f"unsupported retrieval policy keys: {sorted(unknown)}")
    policy = {**DEFAULT_RAG_RETRIEVAL_POLICY, **dict(values)}
    for key, low, high in (
        ("top_k", 1, 20), ("candidate_k", 1, 100),
        ("context_max_tokens", 1, 12000), ("rrf_k", 1, 1000),
        ("query_expansion_count", 0, 2),
    ):
        value = policy[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    if policy["candidate_k"] < policy["top_k"]:
        raise ValueError("retrieval candidate_k must be at least top_k")
    for key in ("vector_weight", "lexical_weight", "raw_query_weight",
                "standalone_query_weight", "expansion_query_weight", "metadata_hint_weight"):
        value = policy[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and non-negative")
    if not 0 < policy["metadata_hint_weight"] < 1:
        raise ValueError("metadata hint weight must be strictly between zero and one")
    if policy["vector_weight"] + policy["lexical_weight"] <= 0:
        raise ValueError("retrieval weights must not both be zero")
    if policy["raw_query_weight"] + policy["standalone_query_weight"] <= 0:
        raise ValueError("query requires a base route")
    if not policy["query_expansion_count"] and policy["expansion_query_weight"]:
        raise ValueError("query expansion weight requires enabled expansions")
    return policy
