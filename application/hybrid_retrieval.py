"""Backend-neutral contracts for corpus-scoped hybrid candidate generation.

The platform backend owns candidate generation only.  Corpus policies own fusion,
recency, reranking, packing, evidence sufficiency, and answer publication.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class RetrievalContractError(ValueError):
    """The caller supplied a request outside the supported retrieval algebra."""


class GenerationConflict(RuntimeError):
    """An immutable generation or corpus pointer would be changed inconsistently."""


class RetrievalCorpus(str, Enum):
    KNOWLEDGE = "KNOWLEDGE"
    SERVICE_EPISODE = "SERVICE_EPISODE"


class RetrievalStatus(str, Enum):
    OK = "OK"
    NO_EVIDENCE = "NO_EVIDENCE"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    CONFLICT = "CONFLICT"


class GenerationState(str, Enum):
    REGISTERED = "REGISTERED"
    BUILDING = "BUILDING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class DistanceMetric(str, Enum):
    COSINE = "COSINE"


@dataclass(frozen=True)
class KnowledgeSearchScope:
    scope: str
    locale: str
    product: str | None = None

    def __post_init__(self) -> None:
        if not self.scope.strip() or not self.locale.strip():
            raise RetrievalContractError("knowledge scope and locale are required")


@dataclass(frozen=True)
class EpisodeSearchScope:
    user_id: str
    entity_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise RetrievalContractError("episode user_id is required")
        if any(not item.strip() for item in self.entity_ids):
            raise RetrievalContractError("episode entity IDs must not be blank")


SearchScope = KnowledgeSearchScope | EpisodeSearchScope


@dataclass(frozen=True)
class HybridRetrievalRequest:
    tenant_id: str
    corpus: RetrievalCorpus
    backend_fingerprint: str
    generation_id: str
    policy_fingerprint: str
    query_text: str
    query_embedding: tuple[float, ...] | None
    scope: SearchScope
    dense_limit: int = 20
    lexical_limit: int = 20
    exact_dense: bool = False

    def __post_init__(self) -> None:
        for label, value in (
            ("tenant_id", self.tenant_id),
            ("backend_fingerprint", self.backend_fingerprint),
            ("generation_id", self.generation_id),
            ("policy_fingerprint", self.policy_fingerprint),
        ):
            if not value.strip():
                raise RetrievalContractError(f"{label} is required")
        if not self.query_text.strip() and self.query_embedding is None:
            raise RetrievalContractError("text or embedding query is required")
        if self.dense_limit < 0 or self.lexical_limit < 0:
            raise RetrievalContractError("candidate limits must be non-negative")
        if self.dense_limit == 0 and self.lexical_limit == 0:
            raise RetrievalContractError("at least one candidate route is required")
        if self.query_embedding is not None and (
            not self.query_embedding
            or any(not math.isfinite(value) for value in self.query_embedding)
        ):
            raise RetrievalContractError("query embedding must contain finite values")
        if self.corpus is RetrievalCorpus.KNOWLEDGE and not isinstance(
            self.scope, KnowledgeSearchScope,
        ):
            raise RetrievalContractError("knowledge corpus requires knowledge scope")
        if self.corpus is RetrievalCorpus.SERVICE_EPISODE and not isinstance(
            self.scope, EpisodeSearchScope,
        ):
            raise RetrievalContractError("episode corpus requires episode scope")


@dataclass(frozen=True)
class RetrievalCandidate:
    candidate_id: str
    corpus: RetrievalCorpus
    generation_id: str
    source_id: str
    source_revision: str
    rank: int
    score: float
    provenance_sha256: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.candidate_id, self.generation_id, self.source_id,
            self.source_revision, self.provenance_sha256,
        )):
            raise RetrievalContractError("candidate references must not be blank")
        if self.rank < 1 or not math.isfinite(self.score):
            raise RetrievalContractError("candidate rank/score is invalid")
        if len(self.provenance_sha256) != 64:
            raise RetrievalContractError("candidate provenance must be SHA-256")


@dataclass(frozen=True)
class HybridRetrievalResult:
    status: RetrievalStatus
    backend_fingerprint: str
    generation_id: str
    dense_candidates: tuple[RetrievalCandidate, ...] = ()
    lexical_candidates: tuple[RetrievalCandidate, ...] = ()
    detail_code: str | None = None

    def __post_init__(self) -> None:
        for route in (self.dense_candidates, self.lexical_candidates):
            if tuple(item.rank for item in route) != tuple(range(1, len(route) + 1)):
                raise RetrievalContractError("candidate source ranks must be contiguous")
            if len({item.candidate_id for item in route}) != len(route):
                raise RetrievalContractError("candidate IDs must be unique per route")
            if any(
                item.generation_id != self.generation_id for item in route
            ):
                raise RetrievalContractError("candidate generation drift")
        has_candidates = bool(self.dense_candidates or self.lexical_candidates)
        if self.status is RetrievalStatus.OK and not has_candidates:
            raise RetrievalContractError("OK requires at least one candidate")
        if self.status in {
            RetrievalStatus.NO_EVIDENCE,
            RetrievalStatus.UNAVAILABLE,
            RetrievalStatus.INVALID_CONTRACT,
            RetrievalStatus.CONFLICT,
        } and has_candidates:
            raise RetrievalContractError(
                f"{self.status.value} must not carry partial candidates"
            )


class HybridRetrievalBackend(Protocol):
    """Stable port shared by legacy and PostgreSQL candidate backends."""

    def retrieve(self, request: HybridRetrievalRequest) -> HybridRetrievalResult: ...


@dataclass(frozen=True)
class RetrievalGeneration:
    generation_id: str
    corpus: RetrievalCorpus
    backend_id: str
    backend_fingerprint: str
    schema_version: str
    source_watermark: str
    embedding_model: str
    embedding_dimension: int
    embedding_model_digest: str
    distance_metric: DistanceMetric
    vector_extension_version: str
    index_method: str
    index_params_json: str
    chinese_tokenizer: str
    lexical_ranker: str
    manifest_hash: str
    state: GenerationState = GenerationState.REGISTERED

    def __post_init__(self) -> None:
        required = (
            self.generation_id, self.backend_id, self.backend_fingerprint,
            self.schema_version, self.source_watermark, self.embedding_model,
            self.embedding_model_digest, self.vector_extension_version,
            self.index_method, self.chinese_tokenizer, self.lexical_ranker,
            self.manifest_hash,
        )
        if any(not value.strip() for value in required):
            raise RetrievalContractError("generation fields must not be blank")
        if self.embedding_dimension < 1:
            raise RetrievalContractError("embedding dimension must be positive")
        if len(self.manifest_hash) != 64:
            raise RetrievalContractError("generation manifest must be SHA-256")
        try:
            parsed = json.loads(self.index_params_json)
        except json.JSONDecodeError as exc:
            raise RetrievalContractError("index params must be canonical JSON") from exc
        if not isinstance(parsed, dict) or self.index_params_json != _canonical_json(parsed):
            raise RetrievalContractError("index params must be a canonical JSON object")

    @property
    def replayable_across_environments(self) -> bool:
        return len(self.embedding_model_digest) == 64

    def immutable_fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json({
            "generation_id": self.generation_id,
            "corpus": self.corpus.value,
            "backend_id": self.backend_id,
            "backend_fingerprint": self.backend_fingerprint,
            "schema_version": self.schema_version,
            "source_watermark": self.source_watermark,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "embedding_model_digest": self.embedding_model_digest,
            "distance_metric": self.distance_metric.value,
            "vector_extension_version": self.vector_extension_version,
            "index_method": self.index_method,
            "index_params": json.loads(self.index_params_json),
            "chinese_tokenizer": self.chinese_tokenizer,
            "lexical_ranker": self.lexical_ranker,
            "manifest_hash": self.manifest_hash,
        }).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GenerationPointer:
    corpus: RetrievalCorpus
    backend_id: str
    active_generation_id: str
    previous_generation_id: str | None
    version: int


class InMemoryRetrievalGenerationRegistry:
    """Reference state machine used by adapters and conformance tests."""

    _transitions = {
        GenerationState.REGISTERED: {GenerationState.BUILDING, GenerationState.FAILED},
        GenerationState.BUILDING: {GenerationState.READY, GenerationState.FAILED},
        GenerationState.READY: {GenerationState.ACTIVE, GenerationState.FAILED},
        GenerationState.ACTIVE: {GenerationState.RETIRED},
        GenerationState.RETIRED: set(),
        GenerationState.FAILED: set(),
    }

    def __init__(self) -> None:
        self._generations: dict[str, RetrievalGeneration] = {}
        self._pointers: dict[tuple[RetrievalCorpus, str], GenerationPointer] = {}

    def register(self, generation: RetrievalGeneration) -> RetrievalGeneration:
        existing = self._generations.get(generation.generation_id)
        if existing is not None and existing != generation:
            raise GenerationConflict("generation identity is immutable")
        self._generations[generation.generation_id] = generation
        return existing or generation

    def transition(
        self, generation_id: str, target: GenerationState,
    ) -> RetrievalGeneration:
        current = self._generations[generation_id]
        if target not in self._transitions[current.state]:
            raise GenerationConflict(
                f"illegal generation transition {current.state.value}->{target.value}"
            )
        updated = RetrievalGeneration(**{
            **current.__dict__, "state": target,
        })
        self._generations[generation_id] = updated
        return updated

    def activate(self, generation_id: str, *, expected_version: int) -> GenerationPointer:
        generation = self._generations[generation_id]
        if generation.state is not GenerationState.READY:
            raise GenerationConflict("only READY generation can become active")
        key = (generation.corpus, generation.backend_id)
        current = self._pointers.get(key)
        actual_version = current.version if current else 0
        if actual_version != expected_version:
            raise GenerationConflict("generation pointer version conflict")
        if current and current.active_generation_id == generation_id:
            raise GenerationConflict("generation is already active")
        pointer = GenerationPointer(
            corpus=generation.corpus,
            backend_id=generation.backend_id,
            active_generation_id=generation_id,
            previous_generation_id=(current.active_generation_id if current else None),
            version=actual_version + 1,
        )
        if current:
            self.transition(current.active_generation_id, GenerationState.RETIRED)
        self._generations[generation_id] = RetrievalGeneration(**{
            **generation.__dict__, "state": GenerationState.ACTIVE,
        })
        self._pointers[key] = pointer
        return pointer

    def pointer(self, corpus: RetrievalCorpus, backend_id: str) -> GenerationPointer | None:
        return self._pointers.get((corpus, backend_id))


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
