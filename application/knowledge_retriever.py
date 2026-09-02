"""Knowledge-owned retrieval orchestration and cache boundary."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from application.hybrid_retrieval import RetrievalStatus
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
    raw_query_weight: float = 0.25
    standalone_query_weight: float = 0.75
    dense_weight: float = 0.25
    lexical_weight: float = 0.75
    rrf_k: int = 10
    candidate_k: int = 20
    final_k: int = 5
    context_max_tokens: int = 2600

    def __post_init__(self) -> None:
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
            self.dense_weight, self.lexical_weight,
        )
        if any(not math.isfinite(value) or value < 0 for value in weights):
            raise KnowledgeRetrievalContractError("retrieval weights are invalid")
        if self.raw_query_weight + self.standalone_query_weight <= 0:
            raise KnowledgeRetrievalContractError("query variant mass is required")
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

    def __post_init__(self) -> None:
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


@dataclass(frozen=True)
class KnowledgeRetrievalTrace:
    variants: tuple[tuple[str, str, float], ...]
    source_ranks: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    policy_fingerprint: str
    backend_fingerprint: str
    generation_id: str
    manifest_fingerprint: str
    rewrite_fallback: bool
    rerank_fallback: bool


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


class RetrievalCachePort(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, *, ttl_seconds: int) -> bool: ...
    def delete(self, key: str) -> bool: ...


class KnowledgeCandidateSource(Protocol):
    async def search_variants_async(
        self,
        variants: list[tuple[str, str, float]],
        *,
        top_k: int,
        retrieval_policy: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]: ...


class KnowledgeQueryTransformer(Protocol):
    async def standalone(
        self, query: str, history: Sequence[str],
    ) -> tuple[str, str | None]: ...


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
    ):
        self._source = candidate_source
        self._transformer = transformer
        self._reranker = reranker
        self._packer = packer or ContextPacker()
        self._cache = cache

    async def retrieve(self, request: KnowledgeRetrievalRequest) -> EvidencePackResult:
        policy = request.policy
        standalone, rewrite_error = await self._transformer.standalone(
            request.query, request.history,
        )
        if rewrite_error or not standalone.strip() or standalone == request.query:
            variants = (("raw", request.query, 1.0),)
            rewrite_fallback = True
        else:
            total = policy.raw_query_weight + policy.standalone_query_weight
            variants = (
                ("raw", request.query, policy.raw_query_weight / total),
                ("standalone", standalone, policy.standalone_query_weight / total),
            )
            rewrite_fallback = False
        try:
            raw = tuple(await self._source.search_variants_async(
                list(variants), top_k=policy.candidate_k,
                retrieval_policy=policy.legacy_mapping(),
            ))
        except Exception:
            return EvidencePackResult(
                RetrievalStatus.UNAVAILABLE, None, None, "CANDIDATE_SOURCE_UNAVAILABLE",
            )
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
        ordered_ids, rerank_fallback = await self._reranker.rerank(
            request.query, raw,
        )
        if len(ordered_ids) != len(ids) or set(ordered_ids) != set(ids):
            ordered_ids, rerank_fallback = ids, True
        by_id = {item.chunk_id: item for item in candidates}
        ordered = tuple(by_id[item] for item in ordered_ids)
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
            request.manifest_fingerprint, rewrite_fallback, rerank_fallback,
        )
        pack = EvidencePack.from_packed(
            request.query, packed,
            retrieval_policy=policy.legacy_mapping(),
            retrieval_trace={
                "variants": [
                    {"kind": kind, "query": query, "weight": weight}
                    for kind, query, weight in variants
                ],
                "rewrite_prompt_version": policy.transformer_version,
                "rewrite_error": "fallback" if rewrite_fallback else "",
                "rerank_prompt_version": policy.reranker_version,
                "rerank_error": "fallback" if rerank_fallback else "",
            },
        )
        return EvidencePackResult(RetrievalStatus.OK, pack, trace)

    @staticmethod
    def _candidate(
        item: Mapping[str, Any], request: KnowledgeRetrievalRequest,
    ) -> ContextCandidate:
        content = str(item.get("content") or "").strip()
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
        )
