"""ServiceEpisode-owned fusion, freshness and explainability contract."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from application.hybrid_retrieval import (
    HybridRetrievalBackend,
    HybridRetrievalRequest,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)
from application.memory_retrieval_policy import MemoryRetrievalPolicy


@dataclass(frozen=True)
class ServiceEpisodeRetrievalPolicy:
    """One pinned policy: fusion and calibrated gates change together."""

    version: str
    fusion: MemoryRetrievalPolicy
    minimum_fused_relevance: float
    freshness_max_age_seconds: int

    def __post_init__(self) -> None:
        if not self.version.strip() or not math.isfinite(self.minimum_fused_relevance):
            raise ValueError("service episode policy is incomplete")
        if self.minimum_fused_relevance < 0 or self.freshness_max_age_seconds < 1:
            raise ValueError("service episode thresholds are invalid")

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


@dataclass(frozen=True)
class ServiceEpisodeRetrievalResult:
    status: RetrievalStatus
    hits: tuple[ServiceEpisodeHit, ...] = ()
    detail_code: str | None = None


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
        now: datetime | None = None,
        top_k: int = 5,
    ) -> ServiceEpisodeRetrievalResult:
        if (
            request.corpus is not RetrievalCorpus.SERVICE_EPISODE
            or request.policy_fingerprint != self.policy.fingerprint
            or top_k < 1
        ):
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.INVALID_CONTRACT, detail_code="POLICY_OR_SCOPE_MISMATCH",
            )
        generated = self._backend.retrieve(request)
        if generated.status is not RetrievalStatus.OK:
            return ServiceEpisodeRetrievalResult(
                generated.status, detail_code=generated.detail_code,
            )
        if not generated.index_watermark.strip():
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.INVALID_CONTRACT, detail_code="INDEX_WATERMARK_MISSING",
            )
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            return ServiceEpisodeRetrievalResult(
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
                    return ServiceEpisodeRetrievalResult(
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
            return ServiceEpisodeRetrievalResult(
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
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.NO_EVIDENCE, detail_code="RELEVANCE_THRESHOLD",
            )
        fresh = tuple(
            item for item in relevant
            if (current - freshness[item[0].candidate_id]).total_seconds()
            <= self.policy.freshness_max_age_seconds
        )
        if not fresh:
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.NO_EVIDENCE, detail_code="FRESHNESS_THRESHOLD",
            )
        ordered = sorted(fresh, key=lambda item: (-item[1], item[0].candidate_id))
        return ServiceEpisodeRetrievalResult(
            RetrievalStatus.OK,
            tuple(ServiceEpisodeHit(
                candidate_id=item.candidate_id,
                episode_id=item.source_id,
                episode_revision=item.source_revision,
                provenance_sha256=item.provenance_sha256,
                score=score,
                source_ranks=ranks,
                freshness_at=item.freshness_at,
                index_watermark=generated.index_watermark,
                policy_fingerprint=self.policy.fingerprint,
            ) for item, score, ranks in ordered[:top_k]),
        )
