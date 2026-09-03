"""Deterministic Flow retrieval for shadow candidate-recall experiments."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from application.route_policy_v2 import FlowActionRegistry
from application.turn_state import FlowDefinitionRef


@dataclass(frozen=True)
class FlowCandidate:
    flow: FlowDefinitionRef
    score: float


class LexicalFlowRetriever:
    version = "lexical-flow-retriever-v1"

    def retrieve(
        self,
        query: str,
        registry: FlowActionRegistry,
        *,
        limit: int = 5,
    ) -> tuple[FlowCandidate, ...]:
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        query_terms = _terms(query)
        ranked = []
        for action in registry.actions:
            if action.flow is None:
                continue
            text = " ".join((
                action.flow.flow_id.replace("_", " ").replace(".", " "),
                action.objective,
                *(item.description for item in action.argument_definitions),
                *(item.name.replace("_", " ") for item in action.argument_definitions),
            ))
            terms = _terms(text)
            overlap = sum(query_terms.get(term, 0) * terms.get(term, 0) for term in query_terms)
            norm = math.sqrt(sum(value * value for value in terms.values())) or 1.0
            ranked.append(FlowCandidate(action.flow, overlap / norm))
        unique = {item.flow.key: item for item in ranked}
        return tuple(sorted(
            unique.values(),
            key=lambda item: (-item.score, item.flow.flow_id, item.flow.version),
        )[:limit])


def _terms(text: str) -> dict[str, int]:
    normalized = text.lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)
    grams = [
        normalized[index:index + 2]
        for index in range(max(0, len(normalized) - 1))
        if not normalized[index:index + 2].isspace()
    ]
    counts: dict[str, int] = {}
    for term in (*tokens, *grams):
        counts[term] = counts.get(term, 0) + 1
    return counts
