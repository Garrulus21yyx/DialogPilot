"""Typed auxiliary evidence and deterministic Intent Fusion V2 policy.

V2 deliberately does not add heterogeneous source scores. The LLM owns the
primary classification; semantic and Pattern sources may provide typed fallback
or a legal generic-parent to specific-child refinement.
"""
from __future__ import annotations

import asyncio
import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence

from core.intent_recognizer import (
    EvidencePolarity,
    IntentCategory,
    PatternEvidence,
    _GENERIC_INTENTS,
    _GENERIC_PATTERNS,
    _INTENT_GROUPS,
    _SEMANTIC_PROTOTYPES,
    _SPECIFIC_INTENTS,
    _SPECIFIC_PATTERNS,
    extract_pattern_evidence,
    pattern_supports,
)


class FusionStatus(Enum):
    CLASSIFIED = "classified"
    OUT_OF_SCOPE = "out_of_scope"
    AMBIGUOUS = "ambiguous"
    PROVIDER_FAILURE = "provider_failure"


@dataclass(frozen=True)
class SemanticCandidate:
    intent: IntentCategory
    score: float


@dataclass(frozen=True)
class SemanticEvidence:
    provider: str
    top1: SemanticCandidate
    top2: SemanticCandidate
    margin: float
    threshold: float
    min_margin: float
    accepted: bool
    failed: bool = False
    error_type: str = ""

    @classmethod
    def failure(cls, provider: str, error_type: str) -> "SemanticEvidence":
        empty = SemanticCandidate(IntentCategory.OTHER, 0.0)
        return cls(
            provider=provider,
            top1=empty,
            top2=empty,
            margin=0.0,
            threshold=1.0,
            min_margin=1.0,
            accepted=False,
            failed=True,
            error_type=error_type,
        )


@dataclass(frozen=True)
class FusionDecision:
    status: FusionStatus
    intent: IntentCategory
    support_score: float
    confidence_kind: str
    reason: str
    source_intents: Mapping[str, str] = field(default_factory=dict)


class TextEncoder(Protocol):
    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one L2-normalized dense vector per input text."""


class SentenceTransformerBGEEncoder:
    """Explicit optional BGE runtime; construction fails if the extra is absent."""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on deployment extra
            raise RuntimeError(
                "semantic intent mode requires the optional sentence-transformers runtime"
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False,
        )


def _normalize_prototype(text: str) -> str:
    return "".join(str(text).lower().split()).strip("，。！？,.!?")


def validate_semantic_prototypes(
    prototypes: Mapping[IntentCategory, Sequence[str]] = _SEMANTIC_PROTOTYPES,
) -> None:
    """Reject duplicated truth and unsupported OTHER prototypes."""
    if IntentCategory.OTHER in prototypes:
        raise ValueError("OTHER must be represented by semantic rejection, not a prototype")
    missing = set(IntentCategory) - {IntentCategory.OTHER} - set(prototypes)
    if missing:
        raise ValueError(f"semantic prototypes missing intents: {sorted(x.value for x in missing)}")
    owners: dict[str, IntentCategory] = {}
    for intent, examples in prototypes.items():
        if not examples:
            raise ValueError(f"semantic intent {intent.value} has no prototypes")
        for example in examples:
            normalized = _normalize_prototype(example)
            previous = owners.setdefault(normalized, intent)
            if previous is not intent:
                raise ValueError(
                    f"semantic prototype belongs to both {previous.value} and {intent.value}: {example}"
                )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(x * x for x in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


class BGESemanticIntentProvider:
    """Nearest-prototype semantic evidence with explicit rejection semantics."""

    def __init__(
        self,
        encoder: TextEncoder,
        *,
        provider_name: str = "bge-m3",
        prototypes: Mapping[IntentCategory, Sequence[str]] = _SEMANTIC_PROTOTYPES,
        thresholds: Optional[Mapping[IntentCategory, float]] = None,
        default_threshold: float = 0.72,
        min_margin: float = 0.05,
    ):
        validate_semantic_prototypes(prototypes)
        self.encoder = encoder
        self.provider_name = provider_name
        self.prototypes = {intent: tuple(values) for intent, values in prototypes.items()}
        self.thresholds = dict(thresholds or {})
        self.default_threshold = float(default_threshold)
        self.min_margin = float(min_margin)
        if not 0.0 <= self.default_threshold <= 1.0:
            raise ValueError("semantic default_threshold must be between 0 and 1")
        if not 0.0 <= self.min_margin <= 1.0:
            raise ValueError("semantic min_margin must be between 0 and 1")
        self._prototype_vectors: Optional[dict[IntentCategory, tuple[Sequence[float], ...]]] = None
        self._load_lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        if self._prototype_vectors is not None:
            return
        async with self._load_lock:
            if self._prototype_vectors is not None:
                return
            intents: list[IntentCategory] = []
            texts: list[str] = []
            for intent, examples in self.prototypes.items():
                for example in examples:
                    intents.append(intent)
                    texts.append(example)
            vectors = await asyncio.to_thread(self.encoder.encode, texts)
            if len(vectors) != len(texts):
                raise RuntimeError("semantic encoder returned the wrong vector count")
            grouped: dict[IntentCategory, list[Sequence[float]]] = {
                intent: [] for intent in self.prototypes
            }
            for intent, vector in zip(intents, vectors):
                grouped[intent].append(tuple(float(value) for value in vector))
            self._prototype_vectors = {
                intent: tuple(intent_vectors)
                for intent, intent_vectors in grouped.items()
            }

    async def recognize(self, message: str) -> SemanticEvidence:
        try:
            await self._ensure_loaded()
            encoded = await asyncio.to_thread(self.encoder.encode, [message])
            if len(encoded) != 1:
                raise RuntimeError("semantic encoder returned the wrong query vector count")
            query = encoded[0]
            assert self._prototype_vectors is not None
            candidates = sorted(
                (
                    SemanticCandidate(
                        intent,
                        max(_cosine(query, prototype) for prototype in prototype_vectors),
                    )
                    for intent, prototype_vectors in self._prototype_vectors.items()
                ),
                key=lambda candidate: (-candidate.score, candidate.intent.value),
            )
            top1, top2 = candidates[:2]
            threshold = float(self.thresholds.get(top1.intent, self.default_threshold))
            margin = top1.score - top2.score
            return SemanticEvidence(
                provider=self.provider_name,
                top1=top1,
                top2=top2,
                margin=margin,
                threshold=threshold,
                min_margin=self.min_margin,
                accepted=top1.score >= threshold and margin >= self.min_margin,
            )
        except Exception as exc:
            return SemanticEvidence.failure(self.provider_name, type(exc).__name__)


def _best_positive_specific_pattern(
    evidence: Sequence[PatternEvidence],
) -> Optional[IntentCategory]:
    counts = Counter(
        item.intent
        for item in evidence
        if item.specific and item.polarity is EvidencePolarity.POSITIVE
    )
    if not counts:
        return None
    order = {intent: index for index, intent in enumerate(_SPECIFIC_PATTERNS)}
    return min(counts, key=lambda intent: (-counts[intent], order[intent]))


class FusionPolicyV2:
    """Closed decision algebra for LLM authority, fallback, and legal refinement."""

    def __init__(
        self,
        *,
        llm_accept_threshold: float = 0.5,
        out_of_scope_threshold: float = 0.7,
    ):
        self.llm_accept_threshold = float(llm_accept_threshold)
        self.out_of_scope_threshold = float(out_of_scope_threshold)

    @staticmethod
    def _llm_intent(llm: Mapping[str, Any]) -> IntentCategory:
        raw = llm.get("intent", IntentCategory.OTHER)
        if isinstance(raw, IntentCategory):
            return raw
        try:
            return IntentCategory(str(raw))
        except ValueError:
            return IntentCategory.OTHER

    @staticmethod
    def _sources(
        llm_intent: IntentCategory,
        semantic: SemanticEvidence,
        pattern: Sequence[PatternEvidence],
    ) -> Mapping[str, str]:
        positive = sorted({
            item.intent.value
            for item in pattern
            if item.polarity is EvidencePolarity.POSITIVE
        })
        return {
            "llm": llm_intent.value,
            "semantic": semantic.top1.intent.value,
            "pattern_positive": ",".join(positive) or IntentCategory.OTHER.value,
        }

    def decide(
        self,
        llm: Mapping[str, Any],
        semantic: SemanticEvidence,
        pattern: Sequence[PatternEvidence],
    ) -> FusionDecision:
        llm_intent = self._llm_intent(llm)
        llm_score = float(llm.get("confidence", 0.0) or 0.0)
        sources = self._sources(llm_intent, semantic, pattern)

        if llm.get("failed"):
            if semantic.accepted:
                return FusionDecision(
                    FusionStatus.CLASSIFIED, semantic.top1.intent, semantic.top1.score,
                    "semantic_similarity", "llm_failed_semantic_fallback", sources,
                )
            pattern_intent = _best_positive_specific_pattern(pattern)
            if pattern_intent is not None:
                return FusionDecision(
                    FusionStatus.CLASSIFIED, pattern_intent, 1.0,
                    "explicit_pattern", "llm_failed_pattern_fallback", sources,
                )
            return FusionDecision(
                FusionStatus.PROVIDER_FAILURE, IntentCategory.OTHER, 0.0,
                "typed_failure", "llm_failed_without_accepted_fallback", sources,
            )

        if llm_intent is IntentCategory.OTHER:
            if llm_score >= self.out_of_scope_threshold:
                return FusionDecision(
                    FusionStatus.OUT_OF_SCOPE, IntentCategory.OTHER, llm_score,
                    "llm_self_report", "llm_explicit_out_of_scope", sources,
                )
            if semantic.accepted and pattern_supports(pattern, semantic.top1.intent):
                return FusionDecision(
                    FusionStatus.CLASSIFIED, semantic.top1.intent, semantic.top1.score,
                    "semantic_similarity", "low_confidence_other_independent_agreement", sources,
                )
            return FusionDecision(
                FusionStatus.AMBIGUOUS, IntentCategory.OTHER, llm_score,
                "llm_self_report", "low_confidence_other_without_agreement", sources,
            )

        if llm_intent in _SPECIFIC_INTENTS and llm_score >= self.llm_accept_threshold:
            return FusionDecision(
                FusionStatus.CLASSIFIED, llm_intent, llm_score,
                "llm_self_report", "accepted_specific_llm_authority", sources,
            )

        if llm_intent in _GENERIC_INTENTS and semantic.accepted:
            semantic_intent = semantic.top1.intent
            if (
                semantic_intent in _SPECIFIC_INTENTS
                and _INTENT_GROUPS.get(semantic_intent) is llm_intent
                and pattern_supports(pattern, semantic_intent)
            ):
                return FusionDecision(
                    FusionStatus.CLASSIFIED, semantic_intent, semantic.top1.score,
                    "semantic_similarity", "legal_parent_child_refinement", sources,
                )

        if llm_score >= self.llm_accept_threshold:
            return FusionDecision(
                FusionStatus.CLASSIFIED, llm_intent, llm_score,
                "llm_self_report", "accepted_llm_authority", sources,
            )

        if semantic.accepted and semantic.top1.intent is llm_intent:
            return FusionDecision(
                FusionStatus.CLASSIFIED, llm_intent, semantic.top1.score,
                "semantic_similarity", "low_confidence_llm_semantic_agreement", sources,
            )

        return FusionDecision(
            FusionStatus.AMBIGUOUS, IntentCategory.OTHER, llm_score,
            "llm_self_report", "low_confidence_in_scope_without_agreement", sources,
        )
