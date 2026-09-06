"""Replayable projector from canonical references into retrieval search rows."""
from __future__ import annotations

import hashlib
import json

from application.retrieval_projection import (
    ProjectionEvent,
    ProjectionEventStatus,
    ProjectionResult,
    ProjectionResultCode,
)


class PostgresCanonicalRetrievalProjector:
    """Platform projector. Canonical meaning stays with corpus-owner resolvers."""

    def __init__(self, pool, *, resolvers=None):
        self.pool = pool
        self.resolvers = dict(resolvers or {})

    def project_next(self) -> ProjectionResult | None:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT event_id FROM retrieval.canonical_projection_outbox
                WHERE status='PENDING'
                ORDER BY created_at, event_id
                FOR UPDATE SKIP LOCKED LIMIT 1
            """).fetchone()
            if row is None:
                return None
            return self._project_locked(connection, str(row[0]))

    def project(self, event_id: str) -> ProjectionResult:
        with self.pool.transaction() as connection:
            connection.execute("""
                SELECT event_id FROM retrieval.canonical_projection_outbox
                WHERE event_id=%s FOR UPDATE
            """, (event_id,)).fetchone()
            return self._project_locked(connection, event_id)

    def _project_locked(self, connection, event_id: str) -> ProjectionResult:
        row = connection.execute("""
            SELECT event_id, corpus, tenant_id, backend_id, generation_id,
                   source_ref, source_revision, source_fingerprint,
                   subject_user_id, subject_conversation_id, deletion_epoch,
                   status
            FROM retrieval.canonical_projection_outbox WHERE event_id=%s
        """, (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        event = ProjectionEvent(
            *row[:11], ProjectionEventStatus(str(row[11])),
        )
        if event.status is ProjectionEventStatus.APPLIED:
            receipt = connection.execute("""
                SELECT candidate_count FROM retrieval.canonical_projection_receipts
                WHERE event_id=%s
            """, (event_id,)).fetchone()
            return ProjectionResult(
                event_id, ProjectionResultCode.ALREADY_APPLIED, int(receipt[0]),
            )
        if event.status is ProjectionEventStatus.REJECTED:
            code = connection.execute("""
                SELECT result_code FROM retrieval.canonical_projection_outbox
                WHERE event_id=%s
            """, (event_id,)).fetchone()[0]
            return ProjectionResult(event_id, ProjectionResultCode(str(code)), 0)

        connection.execute("""
            UPDATE retrieval.canonical_projection_outbox
            SET status='PROCESSING', attempts=attempts+1,
                lease_until=transaction_timestamp()+interval '30 seconds',
                updated_at=transaction_timestamp()
            WHERE event_id=%s
        """, (event_id,))
        generation = connection.execute("""
            SELECT state FROM retrieval.retrieval_generation_registry
            WHERE corpus=%s AND backend_id=%s AND generation_id=%s FOR SHARE
        """, (event.corpus, event.backend_id, event.generation_id)).fetchone()
        if generation is None or generation[0] != "BUILDING":
            return self._reject(
                connection, event, ProjectionResultCode.GENERATION_NOT_BUILDING,
            )
        if event.corpus == "KNOWLEDGE":
            return self._project_knowledge(connection, event)
        resolver = self.resolvers.get(event.corpus)
        if resolver is None:
            return self._reject(
                connection, event, ProjectionResultCode.RESOLVER_UNAVAILABLE,
            )
        # M4 owns ServiceEpisode meaning. Its resolver will be plugged into this
        # bounded port; the platform still rechecks the subject fence here.
        if not self._subject_is_current(connection, event):
            return self._reject(
                connection, event, ProjectionResultCode.SUBJECT_DELETION_FENCED,
            )
        projected = resolver.project(connection, event)
        return self._complete(connection, event, projected)

    def _project_knowledge(self, connection, event: ProjectionEvent) -> ProjectionResult:
        manifest = connection.execute("""
            SELECT manifest_hash, schema_version, scope, locale, product
            FROM retrieval.knowledge_source_manifests
            WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
              AND manifest_hash=%s
        """, (
            event.tenant_id, event.backend_id, event.generation_id,
            event.source_ref,
        )).fetchall()
        if len(manifest) != 1:
            return self._reject(
                connection, event, ProjectionResultCode.CANONICAL_SOURCE_MISSING,
            )
        if manifest[0][:2] != (event.source_ref, event.source_revision):
            return self._reject(
                connection, event, ProjectionResultCode.CANONICAL_SOURCE_DRIFT,
            )
        rows = connection.execute("""
            SELECT candidate_id, source_id, revision_id, source_checksum,
                   start_char, end_char, provenance_sha256, scope, locale,
                   NULLIF(product,''), embedding::text, lexical_document,
                   retrieval_text, section_path, source_type, region,
                   immutable_fingerprint
            FROM retrieval.knowledge_source_chunk_specs
            WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
              AND scope=%s AND locale=%s AND product=%s
            ORDER BY candidate_id
        """, (
            event.tenant_id, event.backend_id, event.generation_id,
            manifest[0][2], manifest[0][3], manifest[0][4],
        )).fetchall()
        if not rows:
            return self._reject(
                connection, event, ProjectionResultCode.CANONICAL_SOURCE_MISSING,
            )
        actual_fingerprint = hashlib.sha256(json.dumps({
            "corpus": event.corpus,
            "tenant_id": event.tenant_id,
            "backend_id": event.backend_id,
            "generation_id": event.generation_id,
            "manifest_hash": event.source_ref,
            "chunk_fingerprints": sorted(str(row[16]) for row in rows),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if actual_fingerprint != event.source_fingerprint:
            return self._reject(
                connection, event, ProjectionResultCode.CANONICAL_SOURCE_DRIFT,
            )
        for row in rows:
            connection.execute("""
                INSERT INTO retrieval.knowledge_chunk_search (
                    candidate_id, tenant_id, backend_id, generation_id,
                    source_id, source_revision, source_checksum, source_span,
                    provenance_sha256, scope, locale, product,
                    subject_user_id, subject_conversation_id, deletion_epoch,
                    embedding, lexical_document, retrieval_text, section_path,
                    source_type, region, projected_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,jsonb_build_object(
                    'start_char',%s,'end_char',%s),%s,%s,%s,%s,NULL,NULL,0,
                    %s::vector,%s,%s,%s::text[],%s,%s,transaction_timestamp())
                ON CONFLICT (candidate_id) DO NOTHING
            """, (
                row[0], event.tenant_id, event.backend_id, event.generation_id,
                row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                row[8], row[9], row[10], row[11], row[12], list(row[13]),
                row[14], row[15],
            ))
        projected = connection.execute("""
            SELECT candidate_id, provenance_sha256
            FROM retrieval.knowledge_chunk_search
            WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
              AND scope=%s AND locale=%s
              AND product IS NOT DISTINCT FROM NULLIF(%s,'')
            ORDER BY candidate_id
        """, (
            event.tenant_id, event.backend_id, event.generation_id,
            manifest[0][2], manifest[0][3], manifest[0][4],
        )).fetchall()
        expected = [(str(row[0]), str(row[6])) for row in rows]
        if projected != expected:
            return self._reject(
                connection, event, ProjectionResultCode.CANONICAL_SOURCE_DRIFT,
            )
        return self._complete(connection, event, projected)

    @staticmethod
    def _subject_is_current(connection, event: ProjectionEvent) -> bool:
        row = connection.execute("""
            SELECT deleted_at IS NULL, deletion_epoch
            FROM dialogpilot_app.conversations
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s FOR SHARE
        """, (
            event.tenant_id, event.subject_user_id,
            event.subject_conversation_id,
        )).fetchone()
        return bool(row and row[0] and int(row[1]) == event.deletion_epoch)

    @staticmethod
    def _reject(connection, event, code):
        connection.execute("""
            UPDATE retrieval.canonical_projection_outbox
            SET status='REJECTED', result_code=%s, lease_until=NULL,
                updated_at=transaction_timestamp() WHERE event_id=%s
        """, (code.value, event.event_id))
        return ProjectionResult(event.event_id, code, 0)

    @staticmethod
    def _complete(connection, event, projected):
        digest = hashlib.sha256(json.dumps(
            list(projected), separators=(",", ":"), default=str,
        ).encode("utf-8")).hexdigest()
        connection.execute("""
            INSERT INTO retrieval.canonical_projection_receipts (
                event_id, result_code, candidate_count, projection_sha256
            ) VALUES (%s,'APPLIED',%s,%s)
        """, (event.event_id, len(projected), digest))
        connection.execute("""
            UPDATE retrieval.canonical_projection_outbox
            SET status='APPLIED', result_code='APPLIED', lease_until=NULL,
                updated_at=transaction_timestamp() WHERE event_id=%s
        """, (event.event_id,))
        return ProjectionResult(
            event.event_id, ProjectionResultCode.APPLIED, len(projected),
        )
