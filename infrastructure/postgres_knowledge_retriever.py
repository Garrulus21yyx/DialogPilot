"""PostgreSQL Knowledge candidate source and cache evidence validation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from application.hybrid_retrieval import (
    GenerationState,
    HybridRetrievalRequest,
    KnowledgeSearchScope,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.knowledge_retriever import (
    KnowledgeCandidateResult,
    KnowledgeRetrievalRequest,
)
from mcp.evidence_pack import EvidencePack
from mcp.rank_fusion import fuse_rankings


@dataclass(frozen=True)
class _CollectedSources:
    generation: RetrievalGeneration
    authority: dict[str, tuple[str, str, str]]
    ranks: dict[str, dict[str, int]]
    route_weights: dict[str, float]


class PostgresKnowledgeCandidateSource:
    """Own request-scoped PG retrieval, weighted RRF, and source projection reads."""

    def __init__(self, *, backend, generations, pool, embed_query: Callable):
        self._backend = backend
        self._generations = generations
        self._pool = pool
        self._embed_query = embed_query

    async def search_variants_async(
        self,
        request: KnowledgeRetrievalRequest,
        variants: list[tuple[str, str, float]],
        *,
        top_k: int,
    ) -> KnowledgeCandidateResult:
        return await asyncio.to_thread(self._search, request, variants, top_k)

    async def capture_source_rankings_async(
        self,
        request: KnowledgeRetrievalRequest,
        variants: list[tuple[str, str, float]],
        *,
        dense_k: int,
        lexical_k: int,
    ) -> KnowledgeCandidateResult:
        """Return the complete per-route source union before weighted fusion."""
        return await asyncio.to_thread(
            self._capture_source_rankings,
            request,
            variants,
            dense_k,
            lexical_k,
        )

    def _search(
        self,
        request: KnowledgeRetrievalRequest,
        variants: Sequence[tuple[str, str, float]],
        top_k: int,
    ) -> KnowledgeCandidateResult:
        collected = self._collect_sources(
            request,
            variants,
            dense_k=top_k,
            lexical_k=top_k,
        )
        if isinstance(collected, KnowledgeCandidateResult):
            return collected
        ranks = collected.ranks
        route_weights = collected.route_weights
        ranked_ids = fuse_rankings(
            {
                route: tuple(
                    candidate_id
                    for candidate_id, _rank in sorted(
                        route_rank.items(),
                        key=lambda item: (item[1], item[0]),
                    )
                )
                for route, route_rank in ranks.items()
            },
            weights=route_weights,
            rrf_k=request.policy.rrf_k,
            top_k=top_k,
        )
        selected = []
        for candidate_id in ranked_ids:
            source_ranks = {
                route: route_rank[candidate_id]
                for route, route_rank in ranks.items()
                if candidate_id in route_rank
            }
            score = sum(
                route_weights[route] / (request.policy.rrf_k + rank)
                for route, rank in source_ranks.items()
            )
            selected.append((candidate_id, score, source_ranks))
        rows = self._load_rows(
            request,
            collected.generation,
            [candidate_id for candidate_id, _, _ in selected],
        )
        if len(rows) != len(selected):
            return KnowledgeCandidateResult(
                RetrievalStatus.CONFLICT,
                detail_code="CANDIDATE_SOURCE_PROJECTION_DRIFT",
            )
        projected = []
        for candidate_id, score, source_ranks in selected:
            projected.append(
                {
                    **rows[candidate_id],
                    "index_manifest_fingerprint": request.manifest_fingerprint,
                    "ranks": source_ranks,
                    "score": score,
                    "scope_decision": "allowed_public",
                }
            )
        return KnowledgeCandidateResult(RetrievalStatus.OK, tuple(projected))

    def _capture_source_rankings(
        self,
        request: KnowledgeRetrievalRequest,
        variants: Sequence[tuple[str, str, float]],
        dense_k: int,
        lexical_k: int,
    ) -> KnowledgeCandidateResult:
        collected = self._collect_sources(
            request,
            variants,
            dense_k=dense_k,
            lexical_k=lexical_k,
        )
        if isinstance(collected, KnowledgeCandidateResult):
            return collected
        selected = sorted(collected.authority)
        rows = self._load_rows(request, collected.generation, selected)
        if len(rows) != len(selected):
            return KnowledgeCandidateResult(
                RetrievalStatus.CONFLICT,
                detail_code="CANDIDATE_SOURCE_PROJECTION_DRIFT",
            )
        projected = tuple(
            {
                **rows[candidate_id],
                "index_manifest_fingerprint": request.manifest_fingerprint,
                "ranks": {
                    route: route_ranks[candidate_id]
                    for route, route_ranks in collected.ranks.items()
                    if candidate_id in route_ranks
                },
                "scope_decision": "allowed_public",
            }
            for candidate_id in selected
        )
        return KnowledgeCandidateResult(RetrievalStatus.OK, projected)

    def _collect_sources(
        self,
        request: KnowledgeRetrievalRequest,
        variants: Sequence[tuple[str, str, float]],
        *,
        dense_k: int,
        lexical_k: int,
    ) -> _CollectedSources | KnowledgeCandidateResult:
        try:
            generation = self._generations.get(request.generation_id)
        except Exception:
            return KnowledgeCandidateResult(
                RetrievalStatus.UNAVAILABLE,
                detail_code="GENERATION_UNAVAILABLE",
            )
        invalid = self._validate_generation(generation, request)
        if invalid is not None:
            return invalid

        authority: dict[str, tuple[str, str, str]] = {}
        ranks: dict[str, dict[str, int]] = {}
        route_weights: dict[str, float] = {}
        for variant_kind, query, variant_weight in variants:
            try:
                embedding = self._embed_query(query, generation)
            except Exception:
                return KnowledgeCandidateResult(
                    RetrievalStatus.UNAVAILABLE,
                    detail_code="QUERY_EMBEDDING_UNAVAILABLE",
                )
            has_metadata_hints = bool(
                request.source_type_hints or request.region_hints
            )
            scope_routes = (
                (
                    "metadata", request.source_type_hints,
                    request.region_hints, request.policy.metadata_hint_weight,
                ),
                (
                    "global", (), (),
                    1.0 - request.policy.metadata_hint_weight,
                ),
            ) if has_metadata_hints else (("", (), (), 1.0),)
            for scope_kind, source_types, regions, scope_weight in scope_routes:
                generated = self._backend.retrieve(HybridRetrievalRequest(
                    tenant_id=request.tenant_id,
                    corpus=RetrievalCorpus.KNOWLEDGE,
                    backend_fingerprint=generation.backend_fingerprint,
                    generation_id=generation.generation_id,
                    policy_fingerprint=request.policy.fingerprint,
                    query_text=query,
                    query_embedding=embedding,
                    scope=KnowledgeSearchScope(
                        scope="public",
                        locale=request.locale,
                        product=request.product,
                        source_types=source_types,
                        regions=regions,
                        as_of=request.as_of, applicable_region=request.applicable_region,
                        applicable_channel=request.applicable_channel,
                        applicable_product=request.applicable_product,
                    ),
                    dense_limit=dense_k,
                    lexical_limit=lexical_k,
                ))
                if generated.status is RetrievalStatus.NO_EVIDENCE:
                    continue
                if generated.status is not RetrievalStatus.OK:
                    return KnowledgeCandidateResult(
                        generated.status,
                        detail_code=generated.detail_code,
                    )
                for route, weight, candidates in (
                    ("vector", request.policy.dense_weight, generated.dense_candidates),
                    (
                        "bm25" if generation.lexical_ranker == "PG_BM25_ZH_V1"
                        else "lexical",
                        request.policy.lexical_weight,
                        generated.lexical_candidates,
                    ),
                ):
                    route_name = ":".join(filter(None, (
                        variant_kind, scope_kind, route,
                    )))
                    route_weights[route_name] = (
                        variant_weight * scope_weight * weight
                    )
                    route_ranks = ranks.setdefault(route_name, {})
                    for candidate in candidates:
                        identity = (
                            candidate.source_id,
                            candidate.source_revision,
                            candidate.provenance_sha256,
                        )
                        previous = authority.setdefault(
                            candidate.candidate_id, identity,
                        )
                        if previous != identity:
                            return KnowledgeCandidateResult(
                                RetrievalStatus.CONFLICT,
                                detail_code="CANDIDATE_AUTHORITY_CONFLICT",
                            )
                        route_ranks[candidate.candidate_id] = candidate.rank

        if not authority:
            return KnowledgeCandidateResult(
                RetrievalStatus.NO_EVIDENCE,
                detail_code="NO_AUTHORIZED_CANDIDATES",
            )
        return _CollectedSources(generation, authority, ranks, route_weights)

    @staticmethod
    def _validate_generation(
        generation: RetrievalGeneration,
        request: KnowledgeRetrievalRequest,
    ) -> KnowledgeCandidateResult | None:
        if generation.corpus is not RetrievalCorpus.KNOWLEDGE:
            return KnowledgeCandidateResult(
                RetrievalStatus.INVALID_CONTRACT,
                detail_code="GENERATION_CORPUS_MISMATCH",
            )
        if generation.state is not GenerationState.ACTIVE:
            return KnowledgeCandidateResult(
                RetrievalStatus.CONFLICT,
                detail_code="GENERATION_NOT_ACTIVE",
            )
        if generation.backend_fingerprint != request.policy.backend_fingerprint:
            return KnowledgeCandidateResult(
                RetrievalStatus.CONFLICT,
                detail_code="BACKEND_FINGERPRINT_DRIFT",
            )
        if generation.manifest_hash != request.manifest_fingerprint:
            return KnowledgeCandidateResult(
                RetrievalStatus.CONFLICT,
                detail_code="MANIFEST_FINGERPRINT_DRIFT",
            )
        if (
            generation.embedding_metadata_complete
            and generation.embedding_profile.fingerprint
            != request.policy.embedding_version
        ):
            return KnowledgeCandidateResult(
                RetrievalStatus.CONFLICT,
                detail_code="EMBEDDING_PROFILE_FINGERPRINT_DRIFT",
            )
        return None

    def _load_rows(
        self,
        request: KnowledgeRetrievalRequest,
        generation: RetrievalGeneration,
        candidate_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        product_clause = "" if request.product is None else "AND chunk.product=%s"
        params: list[object] = [
            request.tenant_id,
            generation.backend_id,
            generation.generation_id,
            request.locale,
        ]
        if request.product is not None:
            params.append(request.product)
        params.extend((request.manifest_fingerprint, list(candidate_ids)))
        from psycopg import sql
        from infrastructure.knowledge_applicability import source_applicability
        applicability, values = source_applicability(
            "chunk", as_of=request.as_of, region=request.applicable_region,
            channel=request.applicable_channel, product=request.applicable_product)
        params.extend(values)
        with self._pool.transaction() as connection:
            rows = connection.execute(
                sql.SQL(f"""
                SELECT chunk.candidate_id, chunk.source_id,
                       chunk.source_revision, chunk.source_checksum,
                       (chunk.source_span->>'start_char')::integer,
                       (chunk.source_span->>'end_char')::integer,
                       substring(
                           revision.content
                           FROM (chunk.source_span->>'start_char')::integer + 1
                           FOR (chunk.source_span->>'end_char')::integer
                               - (chunk.source_span->>'start_char')::integer
                       ), revision.title,
                       revision.source_type, chunk.scope,
                       revision.region, revision.product, revision.channel,
                       revision.effective_from, revision.effective_to, revision.schema_version
                FROM retrieval.knowledge_chunk_search chunk
                JOIN retrieval.knowledge_source_revisions revision
                  ON revision.tenant_id=chunk.tenant_id
                 AND revision.source_id=chunk.source_id
                 AND revision.revision_id=chunk.source_revision
                JOIN retrieval.knowledge_source_manifests manifest
                  ON manifest.tenant_id=chunk.tenant_id
                 AND manifest.backend_id=chunk.backend_id
                 AND manifest.generation_id=chunk.generation_id
                 AND manifest.scope=chunk.scope
                 AND manifest.locale=chunk.locale
                 AND manifest.product=COALESCE(chunk.product, '')
                WHERE chunk.tenant_id=%s AND chunk.backend_id=%s
                  AND chunk.generation_id=%s AND chunk.scope='public'
                  AND chunk.locale=%s {product_clause}
                  AND manifest.manifest_hash=%s
                  AND chunk.candidate_id=ANY(%s)
                  AND {{applicability}}
                ORDER BY chunk.candidate_id
            """).format(applicability=applicability),
                params,
            ).fetchall()
        return {
            str(row[0]): {
                "chunk_id": str(row[0]),
                "source_id": str(row[1]),
                "source_revision": str(row[2]),
                "source_checksum": str(row[3]),
                "source_start_char": int(row[4]),
                "source_end_char": int(row[5]),
                "content": str(row[6]),
                "title": str(row[7]),
                "source_type": str(row[8]),
                "scope": str(row[9]),
                "applicability": ({
                    "region": row[10], "product": row[11], "channel": row[12],
                    "effective_from": row[13].isoformat(),
                    "effective_to": row[14].isoformat() if row[14] else "",
                } if row[15] == "knowledge-source-v2" else {}),
            }
            for row in rows
        }

    def validate_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        request: KnowledgeRetrievalRequest,
    ) -> bool:
        if not candidates:
            return False
        try:
            generation = self._generations.get(request.generation_id)
            if self._validate_generation(generation, request) is not None:
                return False
            stored = self._load_rows(
                request,
                generation,
                [str(item.get("chunk_id") or "") for item in candidates],
            )
        except Exception:
            return False
        if len(stored) != len(candidates):
            return False
        fields = (
            "chunk_id",
            "source_id",
            "source_revision",
            "source_checksum",
            "source_start_char",
            "source_end_char",
            "content",
            "title",
            "source_type",
            "scope",
        )
        return all(
            all(
                item.get(field) == stored[str(item["chunk_id"])][field]
                for field in fields
            )
            and item.get("applicability", {}) == stored[str(item["chunk_id"])]["applicability"]
            for item in candidates
        )


class PostgresKnowledgeEvidenceValidator:
    def __init__(self, source: PostgresKnowledgeCandidateSource):
        self._source = source

    def validate_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        request: KnowledgeRetrievalRequest,
    ) -> bool:
        return self._source.validate_candidates(candidates, request)

    def validate(
        self,
        pack: EvidencePack,
        request: KnowledgeRetrievalRequest,
    ) -> bool:
        if pack.index_manifest_fingerprint != request.manifest_fingerprint:
            return False
        candidates = [
            {
                "chunk_id": item.chunk_id,
                "source_id": item.source_ref.source_id,
                "source_revision": item.source_ref.source_revision,
                "source_checksum": item.source_ref.checksum,
                "source_start_char": item.source_ref.start_char,
                "source_end_char": item.source_ref.end_char,
                "scope": item.source_ref.scope,
                "content": item.text,
                "title": item.title,
                "source_type": item.source_ref.source_type,
                "applicability": dict(item.source_ref.applicability),
            }
            for item in pack.items
        ]
        return self._source.validate_candidates(candidates, request)
