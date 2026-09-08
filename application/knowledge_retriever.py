"""Knowledge-owned retrieval orchestration and cache boundary."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncio
import hashlib
import json
import math
import secrets
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from application.hybrid_retrieval import (
    RetrievalContractError,
    RetrievalStatus,
    normalize_metadata_facets,
)
from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY as _RAG_DEFAULTS
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.evidence_pack import EvidencePack


class KnowledgeRetrievalContractError(ValueError):
    pass


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KnowledgeRetrievalPolicy:
    policy_version: str
    backend_fingerprint: str
    lexical_provider: str
    transformer_version: str
    embedding_version: str
    reranker_version: str
    packer_version: str
    raw_query_weight: float = _RAG_DEFAULTS["raw_query_weight"]
    standalone_query_weight: float = _RAG_DEFAULTS["standalone_query_weight"]
    expansion_query_weight: float = _RAG_DEFAULTS["expansion_query_weight"]
    query_expansion_count: int = _RAG_DEFAULTS["query_expansion_count"]
    metadata_hint_weight: float = _RAG_DEFAULTS["metadata_hint_weight"]
    dense_weight: float = _RAG_DEFAULTS["vector_weight"]
    lexical_weight: float = _RAG_DEFAULTS["lexical_weight"]
    rrf_k: int = _RAG_DEFAULTS["rrf_k"]
    candidate_k: int = _RAG_DEFAULTS["candidate_k"]
    final_k: int = _RAG_DEFAULTS["top_k"]
    context_max_tokens: int = _RAG_DEFAULTS["context_max_tokens"]

    def __post_init__(self) -> None:
        from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY, validate_rag_policy
        try:
            validate_rag_policy({key: value for key, value in self.legacy_mapping().items()
                                 if key in DEFAULT_RAG_RETRIEVAL_POLICY})
        except ValueError as exc:
            raise KnowledgeRetrievalContractError(str(exc)) from exc
        versions = (
            self.policy_version, self.backend_fingerprint,
            self.lexical_provider, self.transformer_version,
            self.embedding_version, self.reranker_version,
            self.packer_version,
        )
        if any(not value.strip() for value in versions):
            raise KnowledgeRetrievalContractError("retrieval versions are required")
        weights = (
            self.raw_query_weight, self.standalone_query_weight,
            self.expansion_query_weight,
            self.dense_weight, self.lexical_weight,
        )
        if any(not math.isfinite(value) or value < 0 for value in weights):
            raise KnowledgeRetrievalContractError("retrieval weights are invalid")
        if (
            self.raw_query_weight + self.standalone_query_weight
            + self.expansion_query_weight <= 0
        ):
            raise KnowledgeRetrievalContractError("query variant mass is required")
        if self.query_expansion_count < 0 or self.query_expansion_count > 2:
            raise KnowledgeRetrievalContractError(
                "query expansion count must be between zero and two"
            )
        if self.query_expansion_count == 0 and self.expansion_query_weight != 0:
            raise KnowledgeRetrievalContractError(
                "query expansion weight requires enabled expansions"
            )
        if not math.isfinite(self.metadata_hint_weight) or not (
            0 < self.metadata_hint_weight < 1
        ):
            raise KnowledgeRetrievalContractError(
                "metadata hint weight must be strictly between zero and one"
            )
        if self.dense_weight + self.lexical_weight <= 0:
            raise KnowledgeRetrievalContractError("candidate route mass is required")
        if min(
            self.rrf_k, self.candidate_k, self.final_k,
            self.context_max_tokens,
        ) < 1 or self.final_k > self.candidate_k:
            raise KnowledgeRetrievalContractError("retrieval budgets are invalid")

    @property
    def fingerprint(self) -> str:
        return _hash(self.__dict__)

    def legacy_mapping(self) -> dict[str, Any]:
        return {
            "rrf_k": self.rrf_k,
            "candidate_k": self.candidate_k,
            "top_k": self.final_k,
            "context_max_tokens": self.context_max_tokens,
            "vector_weight": self.dense_weight,
            "lexical_weight": self.lexical_weight,
            "raw_query_weight": self.raw_query_weight,
            "standalone_query_weight": self.standalone_query_weight,
            "expansion_query_weight": self.expansion_query_weight,
            "query_expansion_count": self.query_expansion_count,
            "metadata_hint_weight": self.metadata_hint_weight,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.fingerprint,
            "backend_fingerprint": self.backend_fingerprint,
            "lexical_provider": self.lexical_provider,
        }


@dataclass(frozen=True)
class KnowledgeRetrievalRequest:
    tenant_id: str
    user_scope: str
    authorization_fingerprint: str
    acl_policy_fingerprint: str
    deletion_epoch: int
    requirement_signature: str
    query: str
    history: tuple[str, ...]
    conversation_range_hash: str
    locale: str
    product: str | None
    manifest_fingerprint: str
    generation_id: str
    policy: KnowledgeRetrievalPolicy
    source_type_hints: tuple[str, ...] = ()
    region_hints: tuple[str, ...] = ()
    force_recompute: bool = False
    query_mode: str = "HISTORY"
    original_user_message: str | None = None
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    as_of_end: datetime | None = None
    applicable_region: str | None = None
    applicable_channel: str | None = None
    applicable_product: str | None = None

    def __post_init__(self) -> None:
        if self.as_of_end is not None and (
            not isinstance(self.as_of, datetime) or self.as_of.utcoffset() is None
            or not isinstance(self.as_of_end, datetime) or self.as_of_end.utcoffset() is None
            or self.as_of_end <= self.as_of):
            raise KnowledgeRetrievalContractError("knowledge time window requires ordered aware instants")
        if not isinstance(self.as_of, datetime) or self.as_of.utcoffset() is None:
            raise KnowledgeRetrievalContractError("as_of must be timezone-aware")
        for value in (self.applicable_region, self.applicable_channel, self.applicable_product):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 128):
                raise KnowledgeRetrievalContractError("invalid source applicability")
        from application.sales_channels import validate_sales_channel
        try:
            validate_sales_channel(self.applicable_channel) if self.applicable_channel is not None else None
        except ValueError as exc:
            raise KnowledgeRetrievalContractError(str(exc)) from exc
        if self.original_user_message is not None and not isinstance(self.original_user_message, str):
            raise KnowledgeRetrievalContractError("original user message must be text")
        if self.query_mode not in {"RESOLVED", "HISTORY"}:
            raise KnowledgeRetrievalContractError("unsupported query mode")
        if self.query_mode == "RESOLVED" and self.history:
            raise KnowledgeRetrievalContractError("resolved query must not reinterpret conversation history")
        required = (
            self.tenant_id, self.user_scope, self.authorization_fingerprint,
            self.acl_policy_fingerprint, self.requirement_signature,
            self.query, self.conversation_range_hash, self.locale,
            self.manifest_fingerprint, self.generation_id,
        )
        if any(not value.strip() for value in required):
            raise KnowledgeRetrievalContractError("retrieval identity is incomplete")
        if self.deletion_epoch < 0:
            raise KnowledgeRetrievalContractError("deletion epoch is invalid")
        normalized_product = (
            self.product.strip() if self.product is not None else None
        )
        object.__setattr__(self, "product", normalized_product or None)
        object.__setattr__(
            self, "source_type_hints", self._normalize_hints(self.source_type_hints),
        )
        object.__setattr__(
            self, "region_hints", self._normalize_hints(self.region_hints),
        )

    @staticmethod
    def _normalize_hints(values: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return normalize_metadata_facets(values)
        except RetrievalContractError as exc:
            raise KnowledgeRetrievalContractError(
                "retrieval metadata hints are invalid"
            ) from exc


@dataclass(frozen=True)
class KnowledgeRetrievalTrace:
    variants: tuple[tuple[str, str, float], ...]
    source_ranks: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    policy_fingerprint: str
    backend_fingerprint: str
    generation_id: str
    manifest_fingerprint: str
    rewrite_fallback: bool
    expansion_fallback: bool
    rerank_fallback: bool
    cache_hits: tuple[str, ...] = ()
    original_user_message: str | None = None
    query_mode: str = "HISTORY"


@dataclass(frozen=True)
class EvidencePackResult:
    status: RetrievalStatus
    evidence_pack: EvidencePack | None
    trace: KnowledgeRetrievalTrace | None
    detail_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is RetrievalStatus.OK:
            if self.evidence_pack is None or not self.evidence_pack.items:
                raise KnowledgeRetrievalContractError("OK requires evidence")
        elif self.evidence_pack is not None:
            raise KnowledgeRetrievalContractError(
                "non-OK retrieval must not carry partial evidence",
            )

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence_pack": (
                self.evidence_pack.to_dict(include_text=include_text)
                if self.evidence_pack is not None else None
            ),
            "trace": (
                {
                    "variants": [list(item) for item in self.trace.variants],
                    "source_ranks": [
                        [chunk_id, dict(ranks)]
                        for chunk_id, ranks in self.trace.source_ranks
                    ],
                    "policy_fingerprint": self.trace.policy_fingerprint,
                    "backend_fingerprint": self.trace.backend_fingerprint,
                    "generation_id": self.trace.generation_id,
                    "manifest_fingerprint": self.trace.manifest_fingerprint,
                    "rewrite_fallback": self.trace.rewrite_fallback,
                    "expansion_fallback": self.trace.expansion_fallback,
                    "rerank_fallback": self.trace.rerank_fallback,
                    "cache_hits": list(self.trace.cache_hits),
                    "original_user_message": self.trace.original_user_message,
                    "query_mode": self.trace.query_mode,
                }
                if self.trace is not None else None
            ),
            "detail_code": self.detail_code,
        }


@dataclass(frozen=True)
class KnowledgeCandidateResult:
    """Candidate-source outcome without collapsing backend failures into absence."""

    status: RetrievalStatus
    candidates: tuple[Mapping[str, Any], ...] = ()
    detail_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is RetrievalStatus.OK and not self.candidates:
            raise KnowledgeRetrievalContractError("OK requires candidates")
        if self.status is not RetrievalStatus.OK and self.candidates:
            raise KnowledgeRetrievalContractError(
                "non-OK candidate result must not carry partial candidates",
            )


class RetrievalCachePort(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, *, ttl_seconds: int) -> bool: ...
    def delete(self, key: str) -> bool: ...


class KnowledgeEvidenceValidator(Protocol):
    def validate_candidates(
        self, candidates: Sequence[Mapping[str, Any]],
        request: KnowledgeRetrievalRequest,
    ) -> bool: ...

    def validate(
        self, pack: EvidencePack, request: KnowledgeRetrievalRequest,
    ) -> bool: ...


def normalize_retrieval_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class RetrievalCacheKeyBuilder:
    """Exact layered keys; no layer incorporates a future stage output."""

    schema = "knowledge-retrieval-cache-v2-resolved"

    @classmethod
    def transform(cls, request: KnowledgeRetrievalRequest) -> str:
        return cls._key("transform", {
            "tenant": request.tenant_id, "user_scope": request.user_scope,
            "query": request.query,
            "requirement": request.requirement_signature,
            "conversation_range": request.conversation_range_hash,
            "query_mode": request.query_mode,
            "transformer": request.policy.transformer_version,
            "query_expansion_count": request.policy.query_expansion_count,
        })

    @classmethod
    def embedding(
        cls, request: KnowledgeRetrievalRequest, *, normalized_text: str,
    ) -> str:
        return cls._key("embedding", {
            "tenant": request.tenant_id, "user_scope": request.user_scope,
            "deletion_epoch": request.deletion_epoch,
            "text": normalized_text,
            "embedding": request.policy.embedding_version,
            "normalizer": "exact-provider-input-v2",
        })

    @classmethod
    def candidates(
        cls, request: KnowledgeRetrievalRequest,
        variants: Sequence[tuple[str, str, float]],
    ) -> str:
        return cls._key("candidates", {
            "tenant": request.tenant_id, "user_scope": request.user_scope,
            "authorization": request.authorization_fingerprint,
            "acl_policy": request.acl_policy_fingerprint,
            "deletion_epoch": request.deletion_epoch,
            "locale": request.locale, "product": request.product,
            "source_type_hints": request.source_type_hints,
            "region_hints": request.region_hints,
            "as_of": request.as_of.isoformat(),
            "as_of_end": request.as_of_end.isoformat() if request.as_of_end else None,
            "applicable_region": request.applicable_region,
            "applicable_channel": request.applicable_channel,
            "applicable_product": request.applicable_product,
            "variants": [
                [kind, query, weight]
                for kind, query, weight in variants
            ],
            "manifest": request.manifest_fingerprint,
            "generation": request.generation_id,
            "backend": request.policy.backend_fingerprint,
            "lexical_provider": request.policy.lexical_provider,
            "dense_policy": request.policy.dense_weight,
            "lexical_policy": request.policy.lexical_weight,
            "metadata_hint_weight": request.policy.metadata_hint_weight,
            "rrf_k": request.policy.rrf_k,
            "candidate_k": request.policy.candidate_k,
        })

    @classmethod
    def rerank(
        cls, request: KnowledgeRetrievalRequest,
        candidates: Sequence[ContextCandidate],
        *, resolved_query: str | None = None,
    ) -> str:
        return cls._key("rerank", {
            "candidate_input": _hash([
                [item.chunk_id, item.document_id, item.source_revision,
                 item.source_checksum, item.title, item.text]
                for item in candidates
            ]),
            "query": resolved_query if resolved_query is not None else request.query,
            "reranker": request.policy.reranker_version,
            "policy": request.policy.fingerprint,
        })

    @classmethod
    def evidence_pack(
        cls, request: KnowledgeRetrievalRequest,
        *,
        variants: Sequence[tuple[str, str, float]],
        candidates: Sequence[ContextCandidate],
        ordered_ids: Sequence[str],
    ) -> str:
        return cls._key("evidence-pack", {
            "tenant": request.tenant_id, "user_scope": request.user_scope,
            "authorization": request.authorization_fingerprint,
            "acl_policy": request.acl_policy_fingerprint,
            "deletion_epoch": request.deletion_epoch,
            "requirement": request.requirement_signature,
            "manifest": request.manifest_fingerprint,
            "generation": request.generation_id,
            "source_revisions": sorted([
                [item.document_id, item.source_revision, item.source_checksum]
                for item in candidates
            ]),
            "variants": [list(item) for item in variants],
            "ordered_ids": list(ordered_ids),
            "packer": request.policy.packer_version,
            "context_max_tokens": request.policy.context_max_tokens,
            "final_k": request.policy.final_k,
            "policy": request.policy.fingerprint,
        })

    @classmethod
    def _key(cls, layer: str, value: Mapping[str, Any]) -> str:
        return f"{cls.schema}:{layer}:{_hash(value)}"


class KnowledgeCandidateSource(Protocol):
    async def search_variants_async(
        self,
        request: KnowledgeRetrievalRequest,
        variants: list[tuple[str, str, float]],
        *,
        top_k: int,
    ) -> KnowledgeCandidateResult: ...


class KnowledgeQueryTransformer(Protocol):
    async def standalone(
        self, query: str, history: Sequence[str],
    ) -> tuple[str, str | None]: ...

    async def expand(
        self, query: str, *, n: int,
    ) -> tuple[tuple[str, ...], str | None]: ...


class KnowledgeReranker(Protocol):
    async def rerank(
        self, query: str, candidates: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[str, ...], bool]: ...


class KnowledgeRetriever:
    """The sole owner of Knowledge query, fusion, rerank and packing order."""

    def __init__(
        self,
        *,
        candidate_source: KnowledgeCandidateSource,
        transformer: KnowledgeQueryTransformer,
        reranker: KnowledgeReranker,
        packer: ContextPacker | None = None,
        cache: RetrievalCachePort | None = None,
        evidence_validator: KnowledgeEvidenceValidator | None = None,
    ):
        self._source = candidate_source
        self._transformer = transformer
        self._reranker = reranker
        self._packer = packer or ContextPacker()
        self._cache = cache
        self._evidence_validator = evidence_validator

    @property
    def reranker_version(self):
        return getattr(self._reranker, "version", None)

    async def retrieve(self, request: KnowledgeRetrievalRequest) -> EvidencePackResult:
        policy = request.policy
        if self.reranker_version and policy.reranker_version != self.reranker_version:
            return EvidencePackResult(RetrievalStatus.INVALID_CONTRACT, None, None, "RERANKER_IDENTITY_MISMATCH")
        cache_hits: list[str] = []
        transform_key = RetrievalCacheKeyBuilder.transform(request)
        transformed = self._cache_get(transform_key, request)
        if (
            isinstance(transformed, dict)
            and set(transformed) == {
                "query", "error", "expansions", "expansion_error",
            }
            and isinstance(transformed["query"], str)
            and isinstance(transformed["error"], str)
            and isinstance(transformed["expansion_error"], str)
            and isinstance(transformed["expansions"], list)
            and all(isinstance(item, str) for item in transformed["expansions"])
        ):
            standalone = str(transformed["query"])
            rewrite_error = str(transformed["error"]) or None
            expansions = tuple(map(str, transformed["expansions"]))
            expansion_error = str(transformed["expansion_error"]) or None
            cache_hits.append("transform")
        else:
            if request.query_mode == "RESOLVED":
                standalone, rewrite_error = request.query, None
            else:
                standalone, rewrite_error = await self._transformer.standalone(
                    request.query, request.history,
                )
            expansion_base = (
                standalone if not rewrite_error and standalone.strip()
                else request.query
            )
            if policy.query_expansion_count:
                expansions, expansion_error = await self._transformer.expand(
                    expansion_base, n=policy.query_expansion_count,
                )
            else:
                expansions, expansion_error = (), None
            self._cache_set(transform_key, {
                "query": standalone, "error": rewrite_error or "",
                "expansions": list(expansions),
                "expansion_error": expansion_error or "",
            })
        standalone_usable = bool(
            not rewrite_error and standalone.strip() and standalone != request.query
        )
        clean_expansions = tuple(dict.fromkeys(
            value.strip() for value in expansions
            if value.strip() and value.strip() not in {request.query, standalone}
        ))[:policy.query_expansion_count]
        weighted_variants = [("raw", request.query, policy.raw_query_weight +
            (0.0 if standalone_usable else policy.standalone_query_weight))]
        if standalone_usable:
            weighted_variants.append((
                "standalone", standalone, policy.standalone_query_weight,
            ))
        if not expansion_error and clean_expansions:
            per_expansion = policy.expansion_query_weight / len(clean_expansions)
            weighted_variants.extend(
                (f"expansion-{index}", value, per_expansion)
                for index, value in enumerate(clean_expansions, start=1)
            )
        total = sum(weight for _kind, _query, weight in weighted_variants)
        if total <= 0:
            weighted_variants = [("raw", request.query, 1.0)]
            total = 1.0
        variants = tuple(
            (kind, query, weight / total)
            for kind, query, weight in weighted_variants
            if weight > 0
        )
        resolved_query = standalone if not rewrite_error and standalone.strip() else request.query
        rewrite_fallback = request.query_mode == "HISTORY" and not standalone_usable
        expansion_fallback = bool(
            policy.query_expansion_count
            and (expansion_error or not clean_expansions)
        )
        candidate_key = RetrievalCacheKeyBuilder.candidates(request, variants)
        cached_candidates = self._cache_get(candidate_key, request)
        if (
            isinstance(cached_candidates, list) and bool(cached_candidates)
            and all(
                isinstance(item, dict) for item in cached_candidates
            ) and self._evidence_validator is not None
            and self._evidence_validator.validate_candidates(
                cached_candidates, request,
            )
        ):
            raw = tuple(cached_candidates)
            cache_hits.append("candidates")
        else:
            lease_token = self._acquire_lease(candidate_key)
            raw = None
            if lease_token == "":
                for _attempt in range(10):
                    await asyncio.sleep(0.01)
                    waited = self._cache_get(candidate_key, request)
                    if (
                        isinstance(waited, list) and bool(waited)
                        and all(isinstance(item, dict) for item in waited)
                        and self._evidence_validator is not None
                        and self._evidence_validator.validate_candidates(
                            waited, request,
                        )
                    ):
                        raw = tuple(waited)
                        cache_hits.append("candidates")
                        break
            try:
                if raw is None:
                    generated = await self._source.search_variants_async(
                        request, list(variants), top_k=policy.candidate_k,
                    )
                    if generated.status is not RetrievalStatus.OK:
                        return EvidencePackResult(
                            generated.status, None, None, generated.detail_code,
                        )
                    raw = generated.candidates
                if raw and "candidates" not in cache_hits:
                    self._cache_set(candidate_key, [dict(item) for item in raw])
            except Exception:
                return EvidencePackResult(
                    RetrievalStatus.UNAVAILABLE, None, None,
                    "CANDIDATE_SOURCE_UNAVAILABLE",
                )
            finally:
                if lease_token:
                    self._release_lease(candidate_key, lease_token)
        if not raw:
            return EvidencePackResult(
                RetrievalStatus.NO_EVIDENCE, None, None, "NO_AUTHORIZED_CANDIDATES",
            )
        try:
            candidates = tuple(self._candidate(item, request) for item in raw)
        except KnowledgeRetrievalContractError:
            return EvidencePackResult(
                RetrievalStatus.INVALID_CONTRACT, None, None,
                "CANDIDATE_PROVENANCE_INVALID",
            )
        ids = tuple(item.chunk_id for item in candidates)
        if len(set(ids)) != len(ids):
            return EvidencePackResult(
                RetrievalStatus.CONFLICT, None, None, "DUPLICATE_CANDIDATE_ID",
            )
        revisions_by_source: dict[str, set[str]] = {}
        for item in candidates:
            revisions_by_source.setdefault(
                item.document_id, set(),
            ).add(item.source_revision)
        if any(len(revisions) > 1 for revisions in revisions_by_source.values()):
            return EvidencePackResult(
                RetrievalStatus.CONFLICT, None, None,
                "ACTIVE_SOURCE_REVISION_CONFLICT",
            )
        rerank_key = RetrievalCacheKeyBuilder.rerank(request, candidates, resolved_query=resolved_query)
        cached_rerank = self._cache_get(rerank_key, request)
        if isinstance(cached_rerank, dict) and isinstance(
            cached_rerank.get("ordered_ids"), list,
        ):
            ordered_ids = tuple(map(str, cached_rerank["ordered_ids"]))
            rerank_fallback = bool(cached_rerank.get("fallback"))
            cache_hits.append("rerank")
        else:
            ordered_ids, rerank_fallback = await self._reranker.rerank(
                resolved_query, raw,
            )
        if len(ordered_ids) != len(ids) or set(ordered_ids) != set(ids):
            ordered_ids, rerank_fallback = ids, True
        self._cache_set(rerank_key, {
            "ordered_ids": list(ordered_ids), "fallback": rerank_fallback,
        })
        by_id = {item.chunk_id: item for item in candidates}
        ordered = tuple(by_id[item] for item in ordered_ids)
        pack_key = RetrievalCacheKeyBuilder.evidence_pack(
            request, variants=variants, candidates=candidates,
            ordered_ids=ordered_ids,
        )
        cached_pack = self._cache_get(pack_key, request)
        if isinstance(cached_pack, dict):
            try:
                pack = self._pack_from_dict(cached_pack)
            except (KeyError, TypeError, ValueError):
                pack = None
            if (
                pack is not None and self._evidence_validator is not None
                and self._evidence_validator.validate(pack, request)
            ):
                cache_hits.append("evidence-pack")
                trace = KnowledgeRetrievalTrace(
                    variants, tuple((item.chunk_id, item.ranks) for item in candidates),
                    policy.fingerprint, policy.backend_fingerprint,
                    request.generation_id, request.manifest_fingerprint,
                    rewrite_fallback, expansion_fallback, rerank_fallback,
                    tuple(cache_hits), request.original_user_message, request.query_mode,
                )
                return EvidencePackResult(RetrievalStatus.OK, pack, trace)
        packed = self._packer.pack(
            ordered, max_tokens=policy.context_max_tokens,
            max_chunks=policy.final_k, redundancy_threshold=1.0,
        )
        if not packed.selected:
            return EvidencePackResult(
                RetrievalStatus.NO_EVIDENCE, None, None, "PACKING_EMPTY",
            )
        source_ranks = tuple(
            (item.chunk_id, item.ranks) for item in candidates
        )
        trace = KnowledgeRetrievalTrace(
            variants, source_ranks, policy.fingerprint,
            policy.backend_fingerprint, request.generation_id,
            request.manifest_fingerprint, rewrite_fallback,
            expansion_fallback, rerank_fallback,
            tuple(cache_hits), request.original_user_message, request.query_mode,
        )
        pack = EvidencePack.from_packed(
            resolved_query, packed,
            retrieval_policy=policy.legacy_mapping(),
            retrieval_trace={
                "variants": [
                    {"kind": kind, "query": query, "weight": weight}
                    for kind, query, weight in variants
                ],
                "rewrite_prompt_version": policy.transformer_version,
                "rewrite_error": "fallback" if rewrite_fallback else "",
                "expansion_error": "fallback" if expansion_fallback else "",
                "rerank_prompt_version": policy.reranker_version,
                "rerank_error": "fallback" if rerank_fallback else "",
            },
        )
        if self._evidence_validator is not None and not self._evidence_validator.validate(pack, request):
            return EvidencePackResult(RetrievalStatus.CONFLICT, None, trace, "EVIDENCE_CHANGED_DURING_RETRIEVAL")
        self._cache_set(pack_key, pack.to_dict(include_text=True))
        return EvidencePackResult(RetrievalStatus.OK, pack, trace)

    def _cache_get(
        self, key: str, request: KnowledgeRetrievalRequest,
    ) -> object | None:
        if self._cache is None or request.force_recompute:
            return None
        raw = self._cache.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._cache.delete(key)
            return None

    def _cache_set(self, key: str, value: object) -> None:
        if self._cache is None:
            return
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        jitter = int(key[-2:], 16) % 61
        self._cache.set(key, encoded, ttl_seconds=300 + jitter)

    def _acquire_lease(self, key: str) -> str | None:
        if self._cache is None or not hasattr(self._cache, "acquire"):
            return None
        token = secrets.token_hex(16)
        acquired = self._cache.acquire(key, token, lease_seconds=10)
        return token if acquired else ""

    def _release_lease(self, key: str, token: str) -> None:
        if self._cache is not None and hasattr(self._cache, "release"):
            self._cache.release(key, token)

    @staticmethod
    def _pack_from_dict(value: Mapping[str, Any]) -> EvidencePack:
        from mcp.evidence_pack import EvidenceItem, SourceReference

        items = []
        for item in value["items"]:
            source = item["source_ref"]
            items.append(EvidenceItem(
                chunk_id=str(item["chunk_id"]), title=str(item["title"]),
                source_ref=SourceReference(
                    source_id=str(source["source_id"]),
                    source_revision=str(source["source_revision"]),
                    start_char=int(source["start_char"]),
                    end_char=int(source["end_char"]),
                    source_type=str(source["source_type"]),
                    checksum=str(source["checksum"]), scope=str(source["scope"]),
                    applicability=tuple(sorted(source.get("applicability", {}).items())),
                ),
                score=float(item["score"]), rank=int(item["rank"]),
                source_ranks=tuple(sorted(
                    (str(key), int(rank))
                    for key, rank in item["source_ranks"].items()
                )),
                scope_decision=str(item["scope_decision"]),
                text=str(item["text"]),
            ))
        return EvidencePack(
            query=str(value["query"]),
            index_manifest_fingerprint=str(value["index_manifest_fingerprint"]),
            retrieval_policy=tuple(sorted(value["retrieval_policy"].items())),
            items=tuple(items),
            query_variants=tuple(
                (str(item["kind"]), str(item["query"]), float(item["weight"]))
                for item in value.get("query_variants", [])
            ),
            rewrite_prompt_version=str(value.get("rewrite_prompt_version") or ""),
            rewrite_error=str(value.get("rewrite_error") or ""),
            rerank_prompt_version=str(value.get("rerank_prompt_version") or ""),
            rerank_error=str(value.get("rerank_error") or ""),
            skipped_redundant=tuple(map(str, value.get("skipped_redundant", []))),
            skipped_budget=tuple(map(str, value.get("skipped_budget", []))),
        )

    @staticmethod
    def _candidate(
        item: Mapping[str, Any], request: KnowledgeRetrievalRequest,
    ) -> ContextCandidate:
        content = str(item.get("content") or "")
        chunk_id = str(item.get("chunk_id") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        revision = str(item.get("source_revision") or "").strip()
        checksum = str(item.get("source_checksum") or "").strip()
        manifest = str(item.get("index_manifest_fingerprint") or "").strip()
        scope = str(item.get("scope") or "").strip()
        if (
            not all((content, chunk_id, source_id, revision, checksum, manifest, scope))
            or len(checksum) != 64
            or manifest != request.manifest_fingerprint
            or scope != "public"
        ):
            raise KnowledgeRetrievalContractError("candidate provenance is invalid")
        ranks = tuple(sorted(
            (str(key), int(value))
            for key, value in dict(item.get("ranks") or {}).items()
        ))
        return ContextCandidate(
            chunk_id=chunk_id, document_id=source_id, text=content,
            start_char=int(item.get("source_start_char") or 0),
            end_char=int(item.get("source_end_char") or len(content)),
            title=str(item.get("title") or ""), score=float(item.get("score") or 0),
            ranks=ranks, source_type=str(item.get("source_type") or ""),
            source_checksum=checksum, source_revision=revision, scope=scope,
            scope_decision=str(item.get("scope_decision") or ""),
            index_manifest_fingerprint=manifest,
            applicability=tuple(sorted(item.get("applicability", {}).items())),
        )
