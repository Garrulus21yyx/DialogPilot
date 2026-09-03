"""ServiceEpisode-owned fusion, freshness and explainability contract."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum

from application.hybrid_retrieval import (
    HybridRetrievalBackend,
    HybridRetrievalRequest,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)
from application.memory_retrieval_policy import MemoryRetrievalPolicy


class ServiceEpisodeRetrievalPurpose(str, Enum):
    """The two supported consumers have different correctness semantics."""

    REFERENCE_RESOLUTION = "REFERENCE_RESOLUTION"
    HISTORICAL_EVIDENCE = "HISTORICAL_EVIDENCE"


class ServiceEpisodeFreshnessMode(str, Enum):
    """Policy-owned treatment of age after relevance candidate generation."""

    HARD_WINDOW = "HARD_WINDOW"
    ANCHORED_OR_RECENT = "ANCHORED_OR_RECENT"
    RANK_ONLY = "RANK_ONLY"


class ServiceEpisodePurposeOutcome(str, Enum):
    UNIQUE_BINDING = "UNIQUE_BINDING"
    EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_EVIDENCE = "NO_EVIDENCE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ServiceEpisodeRetrievalPolicy:
    """One pinned, purpose-specific policy and its calibrated gates."""

    version: str
    fusion: MemoryRetrievalPolicy
    minimum_fused_relevance: float
    freshness_max_age_seconds: int | None
    purpose: ServiceEpisodeRetrievalPurpose = (
        ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE
    )
    freshness_mode: ServiceEpisodeFreshnessMode = (
        ServiceEpisodeFreshnessMode.HARD_WINDOW
    )
    unique_binding_margin: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, ServiceEpisodeRetrievalPurpose):
            raise ValueError("service episode retrieval purpose is invalid")
        if not isinstance(self.freshness_mode, ServiceEpisodeFreshnessMode):
            raise ValueError("service episode freshness mode is invalid")
        if not self.version.strip() or not math.isfinite(self.minimum_fused_relevance):
            raise ValueError("service episode policy is incomplete")
        if self.minimum_fused_relevance < 0:
            raise ValueError("service episode thresholds are invalid")
        if self.freshness_mode is ServiceEpisodeFreshnessMode.RANK_ONLY:
            if self.freshness_max_age_seconds is not None:
                raise ValueError("rank-only freshness must not declare a max age")
        elif (
            self.freshness_max_age_seconds is None
            or self.freshness_max_age_seconds < 1
        ):
            raise ValueError("windowed freshness requires a positive max age")
        if self.purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION:
            if (
                self.unique_binding_margin is None
                or not math.isfinite(self.unique_binding_margin)
                or self.unique_binding_margin < 0
            ):
                raise ValueError(
                    "reference resolution requires a unique-binding margin"
                )
        elif self.unique_binding_margin is not None:
            raise ValueError(
                "historical evidence must not apply a unique-binding gate"
            )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ServiceEpisodeHit:
    candidate_id: str
    episode_id: str
    episode_revision: str
    provenance_sha256: str
    score: float
    source_ranks: tuple[tuple[str, int], ...]
    freshness_at: str
    index_watermark: str
    policy_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "episode_revision": self.episode_revision,
            "provenance_sha256": self.provenance_sha256,
            "score": self.score,
            "source_ranks": dict(self.source_ranks),
            "freshness_at": self.freshness_at,
            "index_watermark": self.index_watermark,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True)
class ServiceEpisodeRetrievalResult:
    status: RetrievalStatus
    hits: tuple[ServiceEpisodeHit, ...] = ()
    detail_code: str | None = None
    purpose: ServiceEpisodeRetrievalPurpose = (
        ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE
    )
    policy_version: str | None = None
    generation_id: str | None = None
    embedding_profile_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, ServiceEpisodeRetrievalPurpose):
            raise ValueError("ServiceEpisode result purpose is invalid")
        if self.status is RetrievalStatus.OK and not self.hits:
            raise ValueError("successful ServiceEpisode retrieval requires evidence")
        if self.status is RetrievalStatus.AMBIGUOUS and (
            self.purpose is not ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            or len(self.hits) < 2
        ):
            raise ValueError("ambiguous reference resolution requires candidates")
        if self.status not in {RetrievalStatus.OK, RetrievalStatus.AMBIGUOUS} and (
            self.hits
        ):
            raise ValueError("failed ServiceEpisode retrieval must not carry evidence")
        if self.embedding_profile_fingerprint is not None and (
            len(self.embedding_profile_fingerprint) != 64
            or self.generation_id is None
        ):
            raise ValueError("embedding profile fingerprint requires a generation")

    @property
    def purpose_outcome(self) -> ServiceEpisodePurposeOutcome:
        if self.status is RetrievalStatus.OK:
            if self.purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION:
                return ServiceEpisodePurposeOutcome.UNIQUE_BINDING
            return ServiceEpisodePurposeOutcome.EVIDENCE_AVAILABLE
        if self.status is RetrievalStatus.AMBIGUOUS:
            return ServiceEpisodePurposeOutcome.AMBIGUOUS
        if self.status is RetrievalStatus.NO_EVIDENCE:
            return ServiceEpisodePurposeOutcome.NO_EVIDENCE
        return ServiceEpisodePurposeOutcome.FAILED

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "hits": [item.to_dict() for item in self.hits],
            "detail_code": self.detail_code,
            "purpose": self.purpose.value,
            "purpose_outcome": self.purpose_outcome.value,
            "policy_version": self.policy_version,
            "generation_id": self.generation_id,
            "embedding_profile_fingerprint": self.embedding_profile_fingerprint,
        }


class ServiceEpisodeRetriever:
    """Owns episode fusion; the backend owns candidate generation only."""

    def __init__(
        self,
        backend: HybridRetrievalBackend,
        policy: ServiceEpisodeRetrievalPolicy,
    ):
        self._backend = backend
        self.policy = policy

    def retrieve(
        self,
        request: HybridRetrievalRequest,
        *,
        purpose: ServiceEpisodeRetrievalPurpose | str | None = None,
        explicit_time_reference: bool = False,
        now: datetime | None = None,
        top_k: int = 5,
    ) -> ServiceEpisodeRetrievalResult:
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            return self._result(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="TOP_K_INVALID",
            )
        try:
            requested_purpose = ServiceEpisodeRetrievalPurpose(
                self.policy.purpose if purpose is None else purpose
            )
        except ValueError:
            return self._result(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="RETRIEVAL_PURPOSE_UNSUPPORTED",
            )
        if requested_purpose is not self.policy.purpose:
            return self._result(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="RETRIEVAL_PURPOSE_MISMATCH",
                purpose=requested_purpose,
            )
        if not isinstance(explicit_time_reference, bool):
            return self._result(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="EXPLICIT_TIME_REFERENCE_INVALID",
            )
        if (
            requested_purpose
            is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            and top_k < 2
        ):
            return self._result(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="REFERENCE_TOP_K_TOO_SMALL",
            )
        if (
            request.corpus is not RetrievalCorpus.SERVICE_EPISODE
            or request.policy_fingerprint != self.policy.fingerprint
        ):
            return self._result(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="POLICY_OR_SCOPE_MISMATCH",
            )
        generated = self._backend.retrieve(request)
        if generated.status is not RetrievalStatus.OK:
            if generated.status is RetrievalStatus.AMBIGUOUS:
                return self._result(
                    RetrievalStatus.INVALID_CONTRACT,
                    detail_code="BACKEND_RETURNED_POLICY_STATUS",
                )
            return self._result(
                generated.status, detail_code=generated.detail_code,
            )
        if (
            generated.backend_fingerprint != request.backend_fingerprint
            or generated.generation_id != request.generation_id
        ):
            return self._result(
                RetrievalStatus.CONFLICT,
                detail_code="BACKEND_OR_GENERATION_DRIFT",
            )
        if not generated.index_watermark.strip():
            return self._result(
                RetrievalStatus.INVALID_CONTRACT, detail_code="INDEX_WATERMARK_MISSING",
            )
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            return self._result(
                RetrievalStatus.INVALID_CONTRACT, detail_code="NOW_MUST_BE_TIMEZONE_AWARE",
            )
        candidates: dict[str, RetrievalCandidate] = {}
        route_ranks: dict[str, dict[str, int]] = {"vector": {}, "lexical": {}}
        for route, items in (
            ("vector", generated.dense_candidates),
            ("lexical", generated.lexical_candidates),
        ):
            for item in items:
                previous = candidates.setdefault(item.candidate_id, item)
                if (
                    previous.source_id, previous.source_revision,
                    previous.provenance_sha256, previous.freshness_at,
                ) != (
                    item.source_id, item.source_revision,
                    item.provenance_sha256, item.freshness_at,
                ):
                    return self._result(
                        RetrievalStatus.CONFLICT,
                        detail_code="CANDIDATE_AUTHORITY_CONFLICT",
                    )
                route_ranks[route][item.candidate_id] = item.rank
        freshness: dict[str, datetime] = {}
        try:
            for candidate_id, item in candidates.items():
                parsed = datetime.fromisoformat(item.freshness_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed > current:
                    raise ValueError
                freshness[candidate_id] = parsed
        except (TypeError, ValueError):
            return self._result(
                RetrievalStatus.INVALID_CONTRACT, detail_code="FRESHNESS_INVALID",
            )

        # Recency is deliberately bounded to the dense/lexical union.
        recency_order = sorted(
            candidates, key=lambda item: (-freshness[item].timestamp(), item),
        )
        recency_ranks = {
            candidate_id: rank
            for rank, candidate_id in enumerate(recency_order, 1)
        }
        fusion = self.policy.fusion
        weighted_routes = (
            ("vector", route_ranks["vector"], fusion.vector_weight),
            ("lexical", route_ranks["lexical"], fusion.lexical_weight),
            ("recency", recency_ranks, fusion.recency_weight),
        )
        relevant: list[tuple[RetrievalCandidate, float, tuple[tuple[str, int], ...]]] = []
        for candidate_id, item in candidates.items():
            ranks = tuple(
                (route, route_rank[candidate_id])
                for route, route_rank, weight in weighted_routes
                if weight > 0 and candidate_id in route_rank
            )
            score = sum(
                weight / (fusion.rrf_k + route_rank[candidate_id])
                for _, route_rank, weight in weighted_routes
                if weight > 0 and candidate_id in route_rank
            )
            if score >= self.policy.minimum_fused_relevance:
                relevant.append((item, score, ranks))
        if not relevant:
            return self._result(
                RetrievalStatus.NO_EVIDENCE, detail_code="RELEVANCE_THRESHOLD",
            )
        anchored = bool(
            explicit_time_reference
            or getattr(request.scope, "entity_ids", ())
        )
        if (
            self.policy.freshness_mode is ServiceEpisodeFreshnessMode.RANK_ONLY
            or (
                self.policy.freshness_mode
                is ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT
                and anchored
            )
        ):
            fresh = tuple(relevant)
        else:
            assert self.policy.freshness_max_age_seconds is not None
            fresh = tuple(
                item for item in relevant
                if (current - freshness[item[0].candidate_id]).total_seconds()
                <= self.policy.freshness_max_age_seconds
            )
        if not fresh:
            return self._result(
                RetrievalStatus.NO_EVIDENCE, detail_code="FRESHNESS_THRESHOLD",
            )
        ordered = sorted(fresh, key=lambda item: (-item[1], item[0].candidate_id))
        hits = tuple(ServiceEpisodeHit(
                candidate_id=item.candidate_id,
                episode_id=item.source_id,
                episode_revision=item.source_revision,
                provenance_sha256=item.provenance_sha256,
                score=score,
                source_ranks=ranks,
                freshness_at=item.freshness_at,
                index_watermark=generated.index_watermark,
                policy_fingerprint=self.policy.fingerprint,
            ) for item, score, ranks in ordered[:top_k])
        if (
            self.policy.purpose
            is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            and len(hits) > 1
            and hits[0].score - hits[1].score
            < (self.policy.unique_binding_margin or 0.0)
        ):
            return self._result(
                RetrievalStatus.AMBIGUOUS,
                hits,
                detail_code="REFERENCE_MARGIN_NOT_MET",
            )
        if self.policy.purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION:
            hits = hits[:1]
        return self._result(RetrievalStatus.OK, hits)

    def _result(
        self,
        status: RetrievalStatus,
        hits: tuple[ServiceEpisodeHit, ...] = (),
        *,
        detail_code: str | None = None,
        purpose: ServiceEpisodeRetrievalPurpose | None = None,
    ) -> ServiceEpisodeRetrievalResult:
        return ServiceEpisodeRetrievalResult(
            status=status,
            hits=hits,
            detail_code=detail_code,
            purpose=purpose or self.policy.purpose,
            policy_version=self.policy.version,
        )
