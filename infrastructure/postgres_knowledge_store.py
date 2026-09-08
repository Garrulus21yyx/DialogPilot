"""Direct local Knowledge ingestion into canonical PostgreSQL generations."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import replace, dataclass
from datetime import datetime, timezone
from typing import Sequence

from application.chinese_lexical import TOKENIZER_VERSION, postgres_lexical_document
from application.knowledge_retrieval_text import (
    RETRIEVAL_TEXT_VERSION,
    build_child_retrieval_text,
)
from application.cost_budget_policy import OFFLINE_KNOWLEDGE_INGEST_BUDGET
from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    GenerationConflict,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from application.knowledge_source import (
    KnowledgeChunkProjection,
    KnowledgeSourceManifest,
    SourceRevision,
)
from core.cost_budget import OfflineIngestBudgetExceeded
from mcp.document_chunker import ChunkStrategy, DocumentChunker, ChunkStructureError
from mcp.source_document import SourceDocument

from .hybrid_retrieval_backend import create_generation_hnsw_index
from .knowledge_embedding import (
    KnowledgeDocumentEmbedder,
    KnowledgeEmbeddingProvider,
    KnowledgeQueryEmbedder,
    LocalHashKnowledgeEmbeddingBaseline,
)
from .postgres_knowledge_source import PostgresKnowledgeSourceRepository
from .postgres_retrieval_projection import PostgresCanonicalRetrievalProjector
from .retrieval_postgres import PostgresRetrievalGenerationRegistry


DEFAULT_KNOWLEDGE_DOCUMENTS = (
    SourceDocument.create(
        source_id="refund-policy", title="退款政策", content=(
            "用户在购买后 7 天内可以申请无理由退款。退款申请将在 1-3 个工作日内审核；"
            "审核通过后，款项将在 5-7 个工作日内原路退回。商品已发货时需先完成退货。"
        ),
    ),
    SourceDocument.create(
        source_id="order-query", title="订单查询", content=(
            "用户可以通过订单号查询订单状态。物流信息通常在发货后 24 小时内更新；"
            "已发货超过 7 天未收到时，可以联系客服申请查件。"
        ),
    ),
    SourceDocument.create(
        source_id="account-security", title="账户安全", content=(
            "忘记密码时可以通过绑定手机号或邮箱重置。发现异常登录时系统会锁定账户并通知用户。"
            "客服人员不会索要用户密码。"
        ),
    ),
    SourceDocument.create(
        source_id="technical-troubleshooting", title="技术故障排查", content=(
            "应用崩溃时请清除缓存并重启，问题持续时更新到最新版。401 表示认证失败；"
            "500 表示服务端异常，可稍后重试或联系技术支持。"
        ),
    ),
    SourceDocument.create(
        source_id="membership-points", title="会员与积分", content=(
            "每消费 1 元累积 1 积分，100 积分可抵扣 1 元。积分有效期为 1 年。"
            "银卡会员享受 95 折，金卡会员享受 9 折。"
        ),
    ),
    SourceDocument.create(
        source_id="delivery", title="配送说明", content=(
            "标准配送 3-5 个工作日送达；加急配送 1-2 个工作日送达。偏远地区可能额外需要 2-3 天。"
            "修改收货地址需在发货前联系客服。"
        ),
    ),
)


@dataclass(frozen=True)
class KnowledgeImportResult:
    chunk_count: int
    revisions: tuple[SourceRevision, ...]
    generation_id: str


class PostgresKnowledgeStore:
    """Replace the active local corpus by building one immutable PG generation."""

    backend_id = "POSTGRES_PG_BM25_ZH_V1"
    backend_fingerprint = "POSTGRES_PGVECTOR_PG_BM25_ZH_V1"
    chunk_schema_version = "knowledge-direct-ingest-v4-source-format"

    def __init__(
        self,
        pool,
        *,
        tenant_id: str,
        locale: str = "zh-CN",
        product: str = "",
        chunk_strategy: ChunkStrategy | str = ChunkStrategy.STRUCTURE_AWARE,
        chunk_max_tokens: int = 512,
        chunk_overlap_tokens: int = 64,
        embedding_provider: KnowledgeEmbeddingProvider | None = None,
        filter_contract_factory=None,
    ):
        from infrastructure.knowledge_filter_config import load_knowledge_filter_contract
        self._filter_contract_factory = filter_contract_factory or load_knowledge_filter_contract
        provider = embedding_provider or LocalHashKnowledgeEmbeddingBaseline()
        self._pool = pool
        self._tenant_id = tenant_id
        self._locale = locale
        self._product = product
        self._chunker = DocumentChunker()
        self._chunk_strategy = ChunkStrategy(chunk_strategy)
        self._chunk_max_tokens = chunk_max_tokens
        self._chunk_overlap_tokens = chunk_overlap_tokens
        self._embedding_provider = provider
        self._document_embedder = KnowledgeDocumentEmbedder(provider)
        self._query_embedder = KnowledgeQueryEmbedder(provider)
        self._generations = PostgresRetrievalGenerationRegistry(pool)
        self._sources = PostgresKnowledgeSourceRepository(pool)
        self._projector = PostgresCanonicalRetrievalProjector(pool)
        self._write_lock = threading.Lock()

    def filter_contract_snapshot(self):
        """One host-owned directory snapshot for importing or planning a turn."""
        from application.sales_channels import filter_contract
        from application.knowledge_source import KnowledgeSourceContractError
        try:
            return filter_contract(self._filter_contract_factory())
        except (ValueError, OSError) as exc:
            raise KnowledgeSourceContractError("knowledge filter configuration unavailable") from exc

    async def ensure_defaults_async(self) -> RetrievalGeneration:
        try:
            generation = await asyncio.to_thread(self.active_generation)
        except GenerationConflict:
            await self.add_documents_async(DEFAULT_KNOWLEDGE_DOCUMENTS)
            return await asyncio.to_thread(self.active_generation)
        sources = await asyncio.to_thread(self._active_sources)
        if (
            self.embedding_profile.matches_generation(generation)
            and generation.generation_id == self._generation_id(sources)
        ):
            return generation
        # A pre-profile or differently configured active generation remains
        # immutable; build and activate a new identity rather than relabel it.
        await self.add_documents_async(())
        return await asyncio.to_thread(self.active_generation)

    async def add_documents_async(
        self, documents: Sequence[SourceDocument],
    ) -> int:
        return await asyncio.to_thread(self.add_documents, documents)

    def add_documents(self, documents: Sequence[SourceDocument]) -> int:
        return self.import_documents(documents).chunk_count

    async def import_documents_async(self, documents: Sequence[SourceDocument]) -> KnowledgeImportResult:
        return await asyncio.to_thread(self.import_documents, documents)

    def import_documents(self, documents: Sequence[SourceDocument]) -> KnowledgeImportResult:
        incoming = tuple(documents)
        from application.sales_channels import require_catalog_channel
        from application.knowledge_source import KnowledgeSourceContractError
        contract = self.filter_contract_snapshot()
        for document in incoming:
            try:
                require_catalog_channel(document.channel, contract, source=True)
            except ValueError as exc:
                raise KnowledgeSourceContractError(str(exc)) from exc
        self._validate_budget(incoming)
        with self._write_lock, self._pool.transaction() as lock_connection:
            lock_connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.backend_id,))
            current = {(item.source_id, item.revision_id): item for item in self._active_sources()}
            imported = []
            for document in incoming:
                revision = self._source_revision(document)
                for prior in current.values():
                    if (prior.source_id == revision.source_id
                        and prior.effective_from == revision.effective_from
                        and prior.revision_id != revision.revision_id):
                        from application.knowledge_source import KnowledgeSourceContractError
                        raise KnowledgeSourceContractError("source versions cannot share an effective_from")
                current[(revision.source_id, revision.revision_id)] = revision
                imported.append(revision)
            sources = tuple(sorted(current.values(), key=lambda item: (item.source_id, item.effective_from, item.revision_id)))
            try:
                chunks = self._chunks(sources)
            except ChunkStructureError as exc:
                from application.knowledge_source import KnowledgeSourceContractError
                raise KnowledgeSourceContractError(str(exc)) from exc
            # Admission limits describe this request, not the accumulated corpus.
            # A revision already present in the active generation is still an
            # input to this batch; historical revisions are not.
            imported_ids = {(item.source_id, item.revision_id) for item in imported}
            self._validate_chunk_budget(tuple(
                chunk for chunk in chunks
                if (chunk.source_id, chunk.revision_id) in imported_ids
            ))
            generation_id = self._generation_id(sources)
            try:
                active = self.active_generation()
                if active.generation_id == generation_id:
                    return KnowledgeImportResult(len(chunks), tuple(imported), generation_id)
            except GenerationConflict:
                pass
            manifest = KnowledgeSourceManifest.build(
                tenant_id=self._tenant_id, backend_id=self.backend_id,
                generation_id=generation_id, scope="public", locale=self._locale,
                product=self._product, sources=sources,
                reviewer_manifest_ref="local-direct-ingest",
                schema_version="knowledge-source-temporal-v2",
            )
            vectors = self._embed_changed_chunks(chunks)
            chunks = tuple(
                replace(chunk, embedding=vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
            generation = self._generations.register(RetrievalGeneration(
                generation_id=generation_id, corpus=RetrievalCorpus.KNOWLEDGE,
                backend_id=self.backend_id,
                backend_fingerprint=self.backend_fingerprint,
                schema_version="retrieval-v1",
                source_watermark=hashlib.sha256("\n".join(
                    item.revision_id for item in sources
                ).encode()).hexdigest(),
                embedding_model=self.embedding_profile.model,
                embedding_dimension=self.embedding_profile.dimension,
                embedding_model_digest=self.embedding_profile.model_digest,
                distance_metric=DistanceMetric.COSINE,
                vector_extension_version="0.8.6", index_method="HNSW",
                index_params_json='{"ef_construction":64,"m":16}',
                chinese_tokenizer=TOKENIZER_VERSION,
                lexical_ranker="PG_BM25_ZH_V1", manifest_hash=manifest.manifest_hash,
                embedding_provider=self.embedding_profile.provider,
                embedding_provider_kind=self.embedding_profile.provider_kind,
                embedding_model_version=self.embedding_profile.model_version,
                embedding_document_preprocessing=(
                    self.embedding_profile.document_preprocessing
                ),
                embedding_query_preprocessing=(
                    self.embedding_profile.query_preprocessing
                ),
            ))
            if generation.state is GenerationState.REGISTERED:
                self._generations.transition(generation_id, GenerationState.BUILDING)
            self._sources.write_generation(manifest, sources, chunks)
            event_id = self._event_id(generation_id)
            result = self._projector.project(event_id)
            if result.code.value not in {"APPLIED", "ALREADY_APPLIED"}:
                raise RuntimeError(f"knowledge projection failed: {result.code.value}")
            with self._pool.transaction() as connection:
                create_generation_hnsw_index(connection, generation)
            current_generation = self._generations.get(generation_id)
            if current_generation.state is GenerationState.BUILDING:
                self._generations.transition(generation_id, GenerationState.READY)
            self._generations.activate_direct(generation_id)
            return KnowledgeImportResult(len(chunks), tuple(imported), generation_id)

    def _embed_changed_chunks(self, chunks):
        """Reuse immutable vectors only for identical source spans and model profile."""
        cached = {}
        try:
            active = self.active_generation()
        except GenerationConflict:
            active = None
        if active is not None and self.embedding_profile.matches_generation(active):
            with self._pool.transaction() as connection:
                rows = connection.execute("""
                    SELECT source_id, revision_id, source_checksum, start_char, end_char,
                           retrieval_text, embedding::text
                    FROM retrieval.knowledge_source_chunk_specs
                    WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
                      AND embedding IS NOT NULL
                """, (self._tenant_id, self.backend_id, active.generation_id)).fetchall()
            cached = {tuple(row[:6]): tuple(json.loads(row[6])) for row in rows}
        def identity(chunk):
            return (chunk.source_id, chunk.revision_id, chunk.source_checksum,
                    chunk.start_char, chunk.end_char, chunk.retrieval_text)
        missing = [chunk for chunk in chunks if identity(chunk) not in cached]
        # Profile changes or a missing cache can require rebuilding more than
        # one import batch. Bound provider calls while keeping generation
        # publication after every required vector has been obtained.
        batch_size = OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_chunks_per_batch
        for start in range(0, len(missing), batch_size):
            batch = missing[start:start + batch_size]
            vectors = self._document_embedder(tuple(chunk.retrieval_text for chunk in batch))
            cached.update((identity(chunk), vector) for chunk, vector in zip(batch, vectors, strict=True))
        return tuple(cached[identity(chunk)] for chunk in chunks)

    def collection_scope(self, generation: RetrievalGeneration) -> tuple[str, str]:
        """Read collection identity from the immutable generation manifest."""
        from application.knowledge_source import KnowledgeSourceContractError
        with self._pool.transaction() as connection:
            rows = connection.execute("""
                SELECT locale, product, scope, manifest_hash
                FROM retrieval.knowledge_source_manifests
                WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
            """, (self._tenant_id, self.backend_id, generation.generation_id)).fetchall()
        if (len(rows) != 1 or rows[0][2] != "public"
                or rows[0][3] != generation.manifest_hash):
            raise KnowledgeSourceContractError("knowledge collection manifest mismatch")
        return str(rows[0][0]), str(rows[0][1])

    def active_generation(self) -> RetrievalGeneration:
        return self._generations.active(
            RetrievalCorpus.KNOWLEDGE, backend_id=self.backend_id,
        )

    def applicability_catalog(self, *, as_of: datetime,
                              expected_generation: RetrievalGeneration | None = None) -> dict[str, object]:
        """Source-owned facet vocabulary and evidence, not subject applicability.

        Query only source metadata from the pinned generation. The same temporal
        predicate as retrieval excludes superseded, expired and withdrawn
        sources. A returned facet's existence does not establish that it applies
        to a user's mentioned product or authorize a business operation.
        """
        from infrastructure.knowledge_applicability import source_applicability
        from psycopg import sql
        if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
            raise ValueError("applicability catalog requires an aware instant")
        generation = self.active_generation()
        if expected_generation is not None and (
            expected_generation.backend_id, expected_generation.generation_id, expected_generation.manifest_hash
        ) != (generation.backend_id, generation.generation_id, generation.manifest_hash):
            raise GenerationConflict("scope catalog does not match pinned knowledge generation")
        predicate, params = source_applicability("chunk", as_of=as_of)
        with self._pool.transaction() as connection:
            rows = connection.execute(sql.SQL("""
                SELECT DISTINCT source.source_id, source.revision_id, source.title,
                       source.product, source.region, source.channel,
                       source.effective_from, source.effective_to
                FROM retrieval.knowledge_chunk_search chunk
                JOIN retrieval.knowledge_source_revisions source
                  ON source.tenant_id=chunk.tenant_id
                 AND source.source_id=chunk.source_id
                 AND source.revision_id=chunk.source_revision
                WHERE chunk.tenant_id=%s AND chunk.backend_id=%s
                  AND chunk.generation_id=%s AND chunk.scope='public'
                  AND chunk.locale=%s AND COALESCE(chunk.product,'')=%s
                  AND {}
                ORDER BY source.source_id, source.revision_id
            """).format(predicate), (
                self._tenant_id, self.backend_id, generation.generation_id,
                self._locale, self._product, *params,
            )).fetchall()
        current = self.active_generation()
        if (current.generation_id, current.manifest_hash) != (generation.generation_id, generation.manifest_hash):
            raise GenerationConflict("knowledge generation changed during scope catalog read")
        entries = [{
            "source_id": row[0], "source_revision": row[1], "title": row[2],
            "product": row[3], "region": row[4], "channel": row[5],
            "effective_from": row[6].isoformat(),
            "effective_to": row[7].isoformat() if row[7] else None,
        } for row in rows]
        payload = {
            "schema_version": "knowledge-applicability-catalog-v1",
            "tenant_id": self._tenant_id, "backend_id": self.backend_id,
            "generation_id": generation.generation_id,
            "manifest_fingerprint": generation.manifest_hash,
            "as_of": as_of.isoformat(), "entries": entries,
            "facet_ids": {dimension: sorted({entry[dimension] for entry in entries
                                             if entry[dimension] != general})
                          for dimension, general in (("product", ""), ("region", "global"), ("channel", "global"))},
        }
        return {**payload, "catalog_fingerprint": hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()}

    @property
    def embedding_profile(self) -> EmbeddingProfile:
        return self._embedding_provider.profile

    def embed_query(
        self, raw_query: str, generation: RetrievalGeneration,
    ) -> tuple[float, ...]:
        return self._query_embedder(raw_query, generation)

    async def doc_count_async(self) -> int:
        return await asyncio.to_thread(self.doc_count)

    def doc_count(self) -> int:
        generation = self.active_generation()
        with self._pool.transaction() as connection:
            return int(connection.execute("""
                SELECT count(*) FROM retrieval.knowledge_chunk_search
                WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
            """, (
                self._tenant_id, self.backend_id, generation.generation_id,
            )).fetchone()[0])

    @property
    def index_manifest(self) -> dict[str, object]:
        generation = self.active_generation()
        return {
            "manifest_fingerprint": generation.manifest_hash,
            "generation_id": generation.generation_id,
            "backend_fingerprint": generation.backend_fingerprint,
            "embedding_profile_fingerprint": (
                generation.embedding_profile.fingerprint
            ),
            "embedding_provider_kind": generation.embedding_provider_kind.value,
            "embedding_model": generation.embedding_model,
            "embedding_model_version": generation.embedding_model_version,
            "embedding_document_preprocessing": (
                generation.embedding_document_preprocessing
            ),
            "embedding_query_preprocessing": (
                generation.embedding_query_preprocessing
            ),
            "chunk_strategy": self._chunk_strategy.value,
            "chunk_max_tokens": self._chunk_max_tokens,
            "chunk_overlap_tokens": self._chunk_overlap_tokens,
            "retrieval_text_version": RETRIEVAL_TEXT_VERSION,
        }

    @property
    def storage_backend(self) -> dict[str, str]:
        generation = self.active_generation()
        return {
            "engine": "postgresql+pgvector+bm25",
            "backend_id": generation.backend_id,
            "generation_id": generation.generation_id,
            "manifest_fingerprint": generation.manifest_hash,
        }

    def _active_sources(self) -> tuple[SourceRevision, ...]:
        try:
            generation = self.active_generation()
        except GenerationConflict:
            return ()
        with self._pool.transaction() as connection:
            rows = connection.execute("""
                SELECT revision.tenant_id, revision.source_id,
                       revision.revision_id, revision.checksum, revision.title,
                       revision.source_type, revision.content,
                       revision.effective_from, revision.effective_to,
                       revision.owner_id, revision.scope, revision.locale,
                       revision.product, revision.region,
                       revision.supersedes_revision_id,
                       revision.operations_audit_ref, revision.schema_version, revision.channel
                FROM retrieval.knowledge_source_manifest_entries entry
                JOIN retrieval.knowledge_source_revisions revision
                  ON revision.tenant_id=entry.tenant_id
                 AND revision.source_id=entry.source_id
                 AND revision.revision_id=entry.revision_id
                WHERE entry.tenant_id=%s AND entry.backend_id=%s
                  AND entry.generation_id=%s AND entry.scope='public'
                  AND entry.locale=%s AND entry.product=%s
                ORDER BY revision.source_id, revision.effective_from, revision.revision_id
            """, (
                self._tenant_id, self.backend_id, generation.generation_id,
                self._locale, self._product,
            )).fetchall()
        return tuple(SourceRevision(*row) for row in rows)

    def _source_revision(self, document: SourceDocument) -> SourceRevision:
        # Reuse only the latest identical import; an old matching body must not
        # implicitly roll back a newer policy. Explicit dates identify history.
        with self._pool.transaction() as connection:
            rows = connection.execute("""
                SELECT tenant_id, source_id, revision_id, checksum, title,
                       source_type, content, effective_from, effective_to,
                       owner_id, scope, locale, product, region,
                       supersedes_revision_id, operations_audit_ref, schema_version, channel,
                       withdrawn_at
                FROM retrieval.knowledge_source_revisions
                WHERE tenant_id=%s AND source_id=%s
                ORDER BY effective_from DESC, revision_id
            """, (self._tenant_id, document.source_id)).fetchall()
        prior = SourceRevision(*rows[0][:-1]) if rows else None
        possible = rows if document.effective_from is not None else rows[:1]
        for row in possible:
            item = SourceRevision(*row[:-1])
            if (item.checksum == document.checksum and item.title == document.title
                and item.source_type == document.source_type and item.scope == document.scope
                and item.locale == self._locale and item.product == document.product
                and item.region == document.region and item.channel == document.channel
                and item.effective_to == document.effective_to
                and (document.effective_from is None or item.effective_from == document.effective_from)):
                if row[-1] is not None:
                    from application.knowledge_source import KnowledgeSourceContractError
                    raise KnowledgeSourceContractError("withdrawn revision requires a new explicit effective_from")
                return item
        return SourceRevision.create(
            tenant_id=self._tenant_id, source_id=document.source_id,
            title=document.title, source_type=document.source_type,
            content=document.content, effective_from=document.effective_from or datetime.now(timezone.utc),
            effective_to=document.effective_to,
            owner_id="local-admin", scope=document.scope, locale=self._locale,
            product=document.product, region=document.region, channel=document.channel,
            supersedes_revision_id=prior.revision_id if prior else None,
            operations_audit_ref="local-direct-ingest", schema_version="knowledge-source-v2",
        )

    def validate_publication_evidence(self, packs) -> bool:
        """Linearize source authorization after model verification, before assembly.

        Share locks make the decision coherent with concurrent withdrawal and
        generation activation. Already published answers are historical records.
        """
        from application.knowledge_tool_contract import evidence_items
        try:
            with self._pool.transaction() as connection:
                active = connection.execute("""
                    SELECT manifest_hash, generation_id FROM retrieval.retrieval_generation_registry
                    WHERE corpus='KNOWLEDGE' AND backend_id=%s AND state='ACTIVE' FOR SHARE
                """, (self.backend_id,)).fetchone()
                if active is None or not packs:
                    return False
                for pack in packs:
                    if pack['evidence_pack']['index_manifest_fingerprint'] != active[0]:
                        return False
                    for item in evidence_items(pack):
                        ref = item['source_ref']
                        member = connection.execute("""
                            SELECT 1 FROM retrieval.knowledge_source_manifest_entries
                            WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
                              AND source_id=%s AND revision_id=%s AND scope='public'
                              AND locale=%s AND product=%s
                        """, (self._tenant_id, self.backend_id, active[1], ref['source_id'],
                              ref['source_revision'], self._locale, self._product)).fetchone()
                        if member is None:
                            return False
                        row = connection.execute("""
                            SELECT content, checksum, withdrawn_at FROM retrieval.knowledge_source_revisions
                            WHERE tenant_id=%s AND source_id=%s AND revision_id=%s FOR SHARE
                        """, (self._tenant_id, ref['source_id'], ref['source_revision'])).fetchone()
                        if (row is None or row[2] is not None or row[1] != ref['checksum']
                            or row[0][ref['start_char']:ref['end_char']] != item['text']):
                            return False
            return True
        except Exception:
            return False

    def withdraw_revision(self, source_id: str, revision_id: str, *, reason: str) -> None:
        from application.knowledge_source import KnowledgeSourceContractError
        if not reason.strip() or len(reason) > 1000:
            raise KnowledgeSourceContractError("withdrawal requires a bounded reason")
        # Withdrawal is authoritative immediately, including for retired generations
        # and cache revalidation. It does not depend on successful re-embedding.
        with self._write_lock, self._pool.transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.backend_id,))
            row = connection.execute("""
                SELECT withdrawn_at FROM retrieval.knowledge_source_revisions
                WHERE tenant_id=%s AND source_id=%s AND revision_id=%s FOR UPDATE
            """, (self._tenant_id, source_id, revision_id)).fetchone()
            if row is None:
                raise KnowledgeSourceContractError("unknown source revision")
            if row[0] is None:
                connection.execute("""
                    UPDATE retrieval.knowledge_source_revisions
                    SET withdrawn_at=transaction_timestamp(), withdrawal_reason=%s
                    WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                """, (reason, self._tenant_id, source_id, revision_id))

    def _chunks(
        self, sources: Sequence[SourceRevision],
    ) -> tuple[KnowledgeChunkProjection, ...]:
        rows = []
        generation_id = self._generation_id(sources)
        for source in sources:
            for chunk in self._chunker.split(
                source.content, max_tokens=self._chunk_max_tokens,
                overlap_tokens=self._chunk_overlap_tokens,
                strategy=self._chunk_strategy,
                source_type=source.source_type,
            ):
                retrieval_text = build_child_retrieval_text(
                    title=source.title,
                    section_path=chunk.section_path,
                    content=chunk.content,
                    product=source.product,
                    region=source.region,
                )
                identity = (
                    f"{generation_id}\0{source.source_id}\0{source.revision_id}\0"
                    f"{chunk.start_char}\0{chunk.end_char}"
                )
                rows.append(KnowledgeChunkProjection(
                    candidate_id=(
                        f"knowledge-chunk-{hashlib.sha256(identity.encode()).hexdigest()}"
                    ),
                    source_id=source.source_id, revision_id=source.revision_id,
                    source_checksum=source.checksum,
                    start_char=chunk.start_char, end_char=chunk.end_char,
                    retrieval_text=retrieval_text,
                    section_path=chunk.section_path,
                    source_type=source.source_type,
                    region=source.region,
                    lexical_document=postgres_lexical_document(retrieval_text),
                    provenance_sha256=hashlib.sha256(
                        f"{identity}\0{source.checksum}".encode()
                    ).hexdigest(),
                ))
        return tuple(rows)

    def _generation_id(self, sources: Sequence[SourceRevision]) -> str:
        digest = hashlib.sha256(json.dumps({
            "tenant_id": self._tenant_id,
            "locale": self._locale,
            "product": self._product,
            "sources": sorted((item.source_id, item.revision_id) for item in sources),
            "chunk_schema": self.chunk_schema_version,
            "chunk_strategy": self._chunk_strategy.value,
            "chunk_max_tokens": self._chunk_max_tokens,
            "chunk_overlap_tokens": self._chunk_overlap_tokens,
            "retrieval_text_version": RETRIEVAL_TEXT_VERSION,
            "embedding_profile": self.embedding_profile.fingerprint,
            "lexical_tokenizer": TOKENIZER_VERSION,
            "lexical_ranker": "PG_BM25_ZH_V1",
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return f"knowledge-generation-{digest[:32]}"

    def _event_id(self, generation_id: str) -> str:
        with self._pool.transaction() as connection:
            rows = connection.execute("""
                SELECT event_id FROM retrieval.canonical_projection_outbox
                WHERE corpus='KNOWLEDGE' AND tenant_id=%s
                  AND backend_id=%s AND generation_id=%s
            """, (self._tenant_id, self.backend_id, generation_id)).fetchall()
        if len(rows) != 1:
            raise RuntimeError("knowledge projection event is not unique")
        return str(rows[0][0])

    @staticmethod
    def _validate_budget(documents: Sequence[SourceDocument]) -> None:
        budget = OFFLINE_KNOWLEDGE_INGEST_BUDGET
        dimensions = (
            ("sources", len(documents), budget.max_sources_per_batch),
            ("source_bytes", max(
                (len(item.content.encode()) for item in documents), default=0,
            ), budget.max_source_bytes),
            ("total_source_bytes", sum(
                len(item.content.encode()) for item in documents
            ), budget.max_total_source_bytes),
        )
        for dimension, observed, limit in dimensions:
            if observed > limit:
                raise OfflineIngestBudgetExceeded(
                    dimension=dimension, observed=observed, limit=limit,
                    policy_version=budget.policy_version,
                )

    @staticmethod
    def _validate_chunk_budget(chunks: Sequence[KnowledgeChunkProjection]) -> None:
        budget = OFFLINE_KNOWLEDGE_INGEST_BUDGET
        if len(chunks) > budget.max_chunks_per_batch:
            raise OfflineIngestBudgetExceeded(
                dimension="chunks", observed=len(chunks),
                limit=budget.max_chunks_per_batch,
                policy_version=budget.policy_version,
            )
