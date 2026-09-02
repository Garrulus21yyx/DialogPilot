"""PostgreSQL SourceRevision backfill and canonical projection enqueue."""
from __future__ import annotations

import hashlib
import json
from typing import Sequence

from application.chinese_lexical import postgres_lexical_document
from application.evidence_receipt import (
    EvidenceReceipt,
    KnowledgeLocator,
    RequirementStatus,
)
from application.knowledge_source import (
    KnowledgeChunkProjection,
    KnowledgeSourceContractError,
    KnowledgeSourceManifest,
    SourceRevision,
)


class KnowledgeSourceConflict(RuntimeError):
    pass


class PostgresKnowledgeSourceRepository:
    """Knowledge Owner repository; retrieval chunks remain derived projections."""

    def __init__(self, pool):
        self.pool = pool

    def backfill_generation(
        self,
        manifest: KnowledgeSourceManifest,
        sources: Sequence[SourceRevision],
        chunks: Sequence[KnowledgeChunkProjection],
    ) -> None:
        source_by_identity = {
            (item.source_id, item.revision_id): item for item in sources
        }
        if len(source_by_identity) != len(sources):
            raise KnowledgeSourceContractError("source revisions must be unique")
        if any(item.tenant_id != manifest.tenant_id for item in sources):
            raise KnowledgeSourceContractError("source tenant differs from manifest")
        if any(
            item.schema_version == "knowledge-source-v1" and (
                item.scope != manifest.scope
                or item.locale != manifest.locale
                or item.product != manifest.product
            )
            for item in sources
        ):
            raise KnowledgeSourceContractError(
                "operational source applicability differs from manifest"
            )
        expected_entries = {
            (item.source_id, item.revision_id, item.checksum)
            for item in manifest.entries
        }
        actual_entries = {
            (item.source_id, item.revision_id, item.checksum) for item in sources
        }
        if actual_entries != expected_entries:
            raise KnowledgeSourceContractError("manifest/source set mismatch")
        for chunk in chunks:
            source = source_by_identity.get((chunk.source_id, chunk.revision_id))
            if source is None or source.checksum != chunk.source_checksum:
                raise KnowledgeSourceContractError("chunk source revision is unknown")
            source_text = source.content[chunk.start_char:chunk.end_char]
            if postgres_lexical_document(source_text) != chunk.lexical_document:
                raise KnowledgeSourceContractError(
                    "chunk lexical text is not a source revision projection"
                )

        with self.pool.transaction() as connection:
            generation = connection.execute("""
                SELECT state, embedding_dimension, manifest_hash
                FROM retrieval.retrieval_generation_registry
                WHERE corpus='KNOWLEDGE' AND backend_id=%s AND generation_id=%s
                FOR UPDATE
            """, (manifest.backend_id, manifest.generation_id)).fetchone()
            if generation is None or generation[0] != "BUILDING":
                raise KnowledgeSourceConflict(
                    "backfill requires a registered BUILDING generation"
                )
            if str(generation[2]) != manifest.manifest_hash:
                raise KnowledgeSourceConflict(
                    "retrieval and source manifest hashes differ"
                )
            dimension = int(generation[1])
            for source in sources:
                connection.execute("""
                    INSERT INTO retrieval.knowledge_source_revisions (
                        tenant_id, source_id, revision_id, checksum, title,
                        source_type, content, effective_from, effective_to,
                        immutable_fingerprint, owner_id, scope, locale, product,
                        region, supersedes_revision_id, operations_audit_ref,
                        schema_version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id, source_id, revision_id) DO NOTHING
                """, (
                    source.tenant_id, source.source_id, source.revision_id,
                    source.checksum, source.title, source.source_type,
                    source.content, source.effective_from, source.effective_to,
                    source.immutable_fingerprint,
                    source.owner_id, source.scope, source.locale, source.product,
                    source.region, source.supersedes_revision_id,
                    source.operations_audit_ref, source.schema_version,
                ))
                row = connection.execute("""
                    SELECT immutable_fingerprint
                    FROM retrieval.knowledge_source_revisions
                    WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                """, (
                    source.tenant_id, source.source_id, source.revision_id,
                )).fetchone()
                if row is None or row[0] != source.immutable_fingerprint:
                    raise KnowledgeSourceConflict("SourceRevision identity changed")
                initial_status = (
                    "ACTIVE" if source.schema_version == "knowledge-source-v0"
                    else "DRAFT"
                )
                connection.execute("""
                    INSERT INTO retrieval.knowledge_source_revision_lifecycle (
                        tenant_id, source_id, revision_id, status, version
                    ) VALUES (%s,%s,%s,%s,1) ON CONFLICT DO NOTHING
                """, (
                    source.tenant_id, source.source_id, source.revision_id,
                    initial_status,
                ))
                lifecycle = connection.execute("""
                    SELECT status FROM retrieval.knowledge_source_revision_lifecycle
                    WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                """, (
                    source.tenant_id, source.source_id, source.revision_id,
                )).fetchone()
                if source.schema_version == "knowledge-source-v1" and (
                    lifecycle is None or lifecycle[0] != "STAGED"
                ):
                    raise KnowledgeSourceConflict(
                        "operational SourceRevision must be STAGED before indexing"
                    )

            connection.execute("""
                INSERT INTO retrieval.knowledge_source_manifests (
                    tenant_id, backend_id, generation_id, scope, locale, product,
                    manifest_hash, source_count, schema_version,
                    reviewer_manifest_ref
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (
                manifest.tenant_id, manifest.backend_id, manifest.generation_id,
                manifest.scope, manifest.locale, manifest.product,
                manifest.manifest_hash, len(manifest.entries),
                manifest.schema_version, manifest.reviewer_manifest_ref,
            ))
            stored_manifest = connection.execute("""
                SELECT manifest_hash, source_count, schema_version,
                       reviewer_manifest_ref
                FROM retrieval.knowledge_source_manifests
                WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
                  AND scope=%s AND locale=%s AND product=%s
            """, (
                manifest.tenant_id, manifest.backend_id, manifest.generation_id,
                manifest.scope, manifest.locale, manifest.product,
            )).fetchone()
            if stored_manifest != (
                manifest.manifest_hash, len(manifest.entries),
                manifest.schema_version, manifest.reviewer_manifest_ref,
            ):
                raise KnowledgeSourceConflict("source manifest identity changed")

            for entry in manifest.entries:
                connection.execute("""
                    INSERT INTO retrieval.knowledge_source_manifest_entries (
                        tenant_id, backend_id, generation_id, scope, locale,
                        product, source_id, revision_id, checksum
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """, (
                    manifest.tenant_id, manifest.backend_id,
                    manifest.generation_id, manifest.scope, manifest.locale,
                    manifest.product, entry.source_id, entry.revision_id,
                    entry.checksum,
                ))

            for chunk in chunks:
                if chunk.embedding is not None and len(chunk.embedding) != dimension:
                    raise KnowledgeSourceContractError(
                        "chunk embedding dimension differs from generation"
                    )
                embedding = (
                    "[" + ",".join(map(str, chunk.embedding)) + "]"
                    if chunk.embedding is not None else None
                )
                connection.execute("""
                    INSERT INTO retrieval.knowledge_source_chunk_specs (
                        candidate_id, tenant_id, backend_id, generation_id,
                        scope, locale, product, source_id, revision_id,
                        source_checksum, start_char, end_char,
                        provenance_sha256, embedding, lexical_document,
                        immutable_fingerprint
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s::vector,%s,%s
                    ) ON CONFLICT (candidate_id) DO NOTHING
                """, (
                    chunk.candidate_id, manifest.tenant_id, manifest.backend_id,
                    manifest.generation_id, manifest.scope, manifest.locale,
                    manifest.product, chunk.source_id, chunk.revision_id,
                    chunk.source_checksum, chunk.start_char, chunk.end_char,
                    chunk.provenance_sha256, embedding, chunk.lexical_document,
                    chunk.immutable_fingerprint,
                ))
                stored = connection.execute("""
                    SELECT immutable_fingerprint
                    FROM retrieval.knowledge_source_chunk_specs WHERE candidate_id=%s
                """, (chunk.candidate_id,)).fetchone()
                if stored is None or stored[0] != chunk.immutable_fingerprint:
                    raise KnowledgeSourceConflict("chunk specification identity changed")

            event_body = {
                "corpus": "KNOWLEDGE",
                "tenant_id": manifest.tenant_id,
                "backend_id": manifest.backend_id,
                "generation_id": manifest.generation_id,
                "manifest_hash": manifest.manifest_hash,
                "chunk_fingerprints": sorted(
                    item.immutable_fingerprint for item in chunks
                ),
            }
            event_fingerprint = hashlib.sha256(json.dumps(
                event_body, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            event_id = f"knowledge-projection-v1-{event_fingerprint}"
            connection.execute("""
                INSERT INTO retrieval.canonical_projection_outbox (
                    event_id, corpus, tenant_id, backend_id, generation_id,
                    source_ref, source_revision, source_fingerprint,
                    deletion_epoch
                ) VALUES (%s,'KNOWLEDGE',%s,%s,%s,%s,%s,%s,0)
                ON CONFLICT (event_id) DO NOTHING
            """, (
                event_id, manifest.tenant_id, manifest.backend_id,
                manifest.generation_id, manifest.manifest_hash,
                manifest.schema_version, event_fingerprint,
            ))
            event = connection.execute("""
                SELECT corpus, tenant_id, backend_id, generation_id, source_ref,
                       source_revision, source_fingerprint
                FROM retrieval.canonical_projection_outbox WHERE event_id=%s
            """, (event_id,)).fetchone()
            if event != (
                "KNOWLEDGE", manifest.tenant_id, manifest.backend_id,
                manifest.generation_id, manifest.manifest_hash,
                manifest.schema_version, event_fingerprint,
            ):
                raise KnowledgeSourceConflict("projection event identity changed")

            counts = connection.execute("""
                SELECT
                    (SELECT count(*) FROM retrieval.knowledge_source_manifest_entries
                     WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
                       AND scope=%s AND locale=%s AND product=%s),
                    (SELECT count(*) FROM retrieval.knowledge_source_chunk_specs
                     WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
                       AND scope=%s AND locale=%s
                       AND product=%s)
            """, (
                manifest.tenant_id, manifest.backend_id, manifest.generation_id,
                manifest.scope, manifest.locale, manifest.product,
                manifest.tenant_id, manifest.backend_id, manifest.generation_id,
                manifest.scope, manifest.locale, manifest.product,
            )).fetchone()
            if counts != (len(manifest.entries), len(chunks)):
                raise KnowledgeSourceConflict("backfill generation is incomplete")

    def resolve(self, locator: KnowledgeLocator) -> dict[str, object]:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT revision.checksum, revision.content
                FROM retrieval.knowledge_source_manifest_entries entry
                JOIN retrieval.knowledge_source_revisions revision
                  ON revision.tenant_id=entry.tenant_id
                 AND revision.source_id=entry.source_id
                 AND revision.revision_id=entry.revision_id
                JOIN retrieval.knowledge_source_revision_lifecycle lifecycle
                  ON lifecycle.tenant_id=revision.tenant_id
                 AND lifecycle.source_id=revision.source_id
                 AND lifecycle.revision_id=revision.revision_id
                WHERE entry.tenant_id=%s AND entry.backend_id=%s
                  AND entry.generation_id=%s AND entry.scope=%s
                  AND entry.locale=%s AND entry.product=%s
                  AND entry.source_id=%s AND entry.revision_id=%s
                  AND entry.checksum=%s
                  AND lifecycle.status NOT IN ('REJECTED','RETRACTED')
            """, (
                locator.tenant_id, locator.backend_id, locator.generation_id,
                locator.scope, locator.locale, locator.product or "",
                locator.source_id, locator.source_revision,
                locator.source_checksum,
            )).fetchone()
        if row is None:
            raise KnowledgeSourceContractError("source revision cannot be resolved")
        content = str(row[1])
        if locator.end_char > len(content):
            raise KnowledgeSourceContractError("source span exceeds revision")
        return {
            "source_id": locator.source_id,
            "source_revision": locator.source_revision,
            "checksum": str(row[0]),
            "content": content[locator.start_char:locator.end_char],
        }

    def validate_active(self, receipt: EvidenceReceipt) -> RequirementStatus:
        if not isinstance(receipt.locator, KnowledgeLocator):
            return RequirementStatus.INVALID_EVIDENCE
        locator = receipt.locator
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT pointer.active_generation_id=entry.generation_id AS active
                FROM retrieval.knowledge_source_manifest_entries entry
                JOIN retrieval.retrieval_generation_pointers pointer
                  ON pointer.corpus='KNOWLEDGE'
                 AND pointer.backend_id=entry.backend_id
                WHERE entry.tenant_id=%s AND entry.backend_id=%s
                  AND entry.generation_id=%s AND entry.scope=%s
                  AND entry.locale=%s AND entry.product=%s
                  AND entry.source_id=%s AND entry.revision_id=%s
                  AND entry.checksum=%s
            """, (
                locator.tenant_id, locator.backend_id, locator.generation_id,
                locator.scope, locator.locale, locator.product or "",
                locator.source_id, locator.source_revision,
                locator.source_checksum,
            )).fetchone()
        if row is None:
            return RequirementStatus.INVALID_EVIDENCE
        return (
            RequirementStatus.SATISFIED if row[0]
            else RequirementStatus.STALE
        )
