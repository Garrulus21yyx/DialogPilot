"""Deterministic safety and diversity checks for generated query variants."""
from __future__ import annotations

import re
import statistics
from typing import Sequence

from evaluation.rag_pipeline.contracts import QueryVariant
from memory.hybrid_retrieval import HybridMemoryRetriever


_ENTITY = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]+[-_]?[A-Za-z0-9]*\d+[A-Za-z0-9._-]*|\b\d+(?:\.\d+)?\b")
_CJK_NEGATIONS = ("不", "不是", "不要", "不能", "没有", "未", "无需", "别")
_EN_NEGATIONS = ("not", "no", "never", "without", "don't", "do not", "cannot", "can't")


def extract_exact_entities(text: str) -> set[str]:
    return {match.group(0).lower() for match in _ENTITY.finditer(str(text))}


def extract_negations(text: str) -> set[str]:
    normalized = str(text).lower()
    found = {term for term in _CJK_NEGATIONS if term in normalized}
    found.update(
        term for term in _EN_NEGATIONS
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", normalized)
    )
    return found


def evaluate_query_variants(
    raw_query: str,
    variants: Sequence[QueryVariant],
) -> dict[str, float]:
    """Measure preservation and expansion risk independently of retrieval quality."""
    generated = [variant for variant in variants if variant.kind != "raw"]
    if not generated:
        generated = list(variants)
    raw_entities = extract_exact_entities(raw_query)
    raw_negations = extract_negations(raw_query)
    raw_tokens = set(HybridMemoryRetriever.tokenize(raw_query))
    preservation = []
    negation_preservation = []
    hallucination_rates = []
    token_sets = []
    for variant in generated:
        entities = extract_exact_entities(variant.text)
        negations = extract_negations(variant.text)
        preservation.append(
            len(raw_entities & entities) / len(raw_entities) if raw_entities else 1.0
        )
        negation_preservation.append(
            len(raw_negations & negations) / len(raw_negations) if raw_negations else 1.0
        )
        invented = entities - raw_entities
        hallucination_rates.append(len(invented) / max(1, len(entities)))
        token_sets.append(set(HybridMemoryRetriever.tokenize(variant.text)))
    pairwise_distances = []
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1:]:
            union = left | right
            pairwise_distances.append(1.0 - len(left & right) / len(union) if union else 0.0)
    coverage_tokens = set().union(*token_sets) if token_sets else set()
    return {
        "raw_query_retained": float(any(
            variant.kind == "raw" and variant.text.strip() == raw_query.strip()
            for variant in variants
        )),
        "entity_preservation": statistics.fmean(preservation) if preservation else 1.0,
        "negation_preservation": (
            statistics.fmean(negation_preservation) if negation_preservation else 1.0
        ),
        "hallucinated_entity_rate": (
            statistics.fmean(hallucination_rates) if hallucination_rates else 0.0
        ),
        "variant_token_diversity": (
            statistics.fmean(pairwise_distances) if pairwise_distances else 0.0
        ),
        "vocabulary_expansion_ratio": (
            len(coverage_tokens - raw_tokens) / max(1, len(raw_tokens))
        ),
    }
