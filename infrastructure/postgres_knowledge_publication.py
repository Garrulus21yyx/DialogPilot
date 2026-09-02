"""PostgreSQL owner for SourceRevision lifecycle and knowledge publication."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import psycopg

from application.knowledge_lifecycle import (
    GateApproval,
    KnowledgeCandidate,
    KnowledgeLifecycleError,
    PinnedKnowledgeManifest,
    PublicationPointerKind,
    PublicationScope,
    ReviewDecision,
    SourceRevisionStatus,
    validate_transition,
)
from application.knowledge_source import SourceRevision


class PostgresKnowledgePublicationRepository:
    """Unique writer for review state, candidates, and publication pointers."""

    def __init__(self, pool):
        self.pool = pool

    def register_draft(
        self, revision: SourceRevision, *, actor_id: str, created_at: datetime,
    ) -> None:
        if revision.schema_version != "knowledge-source-v1":
            raise KnowledgeLifecycleError(
                "INVALID_SOURCE_REVISION", "operational draft requires knowledge-source-v1",
            )
        if not actor_id.strip() or created_at.tzinfo is None:
            raise KnowledgeLifecycleError("INVALID_AUDIT", "draft audit is incomplete")
        with self.pool.transaction() as connection:
            self._insert_revision(connection, revision)
            inserted = connection.execute("""
                INSERT INTO retrieval.knowledge_source_revision_lifecycle (
                    tenant_id, source_id, revision_id, status, version
                ) VALUES (%s,%s,%s,'DRAFT',1)
                ON CONFLICT DO NOTHING RETURNING revision_id
            """, (revision.tenant_id, revision.source_id, revision.revision_id)).fetchone()
            if inserted is None:
                row = connection.execute("""
                    SELECT status FROM retrieval.knowledge_source_revision_lifecycle
                    WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                """, (revision.tenant_id, revision.source_id, revision.revision_id)).fetchone()
                if row is None or row[0] != "DRAFT":
                    raise KnowledgeLifecycleError(
                        "SOURCE_REVISION_CONFLICT", "revision identity already has another lifecycle",
                    )
                return
            self._audit(
                connection, revision.tenant_id, revision.source_id,
                revision.revision_id, None, SourceRevisionStatus.DRAFT,
                actor_id, "DRAFT_CREATED", revision.operations_audit_ref, created_at,
            )

    def transition(
        self,
        *,
        tenant_id: str,
        source_id: str,
        revision_id: str,
        expected_version: int,
        target: SourceRevisionStatus | str,
        actor_id: str,
        reason_code: str,
        evidence_ref: str,
        occurred_at: datetime,
        review: ReviewDecision | None = None,
    ) -> SourceRevisionStatus:
        if not all((actor_id.strip(), reason_code.strip(), evidence_ref.strip())):
            raise KnowledgeLifecycleError("INVALID_AUDIT", "transition audit is incomplete")
        if occurred_at.tzinfo is None:
            raise KnowledgeLifecycleError("INVALID_AUDIT", "transition time must be timezone-aware")
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT status, version
                FROM retrieval.knowledge_source_revision_lifecycle
                WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                FOR UPDATE
            """, (tenant_id, source_id, revision_id)).fetchone()
            if row is None:
                raise KnowledgeLifecycleError("UNKNOWN_REVISION", "source revision does not exist")
            current, target_state = validate_transition(row[0], target)
            if int(row[1]) != expected_version:
                raise KnowledgeLifecycleError("VERSION_CONFLICT", "lifecycle version conflict")
            if target_state in {SourceRevisionStatus.REVIEWED, SourceRevisionStatus.REJECTED}:
                if review is None:
                    raise KnowledgeLifecycleError("INVALID_REVIEW", "review decision is required")
                if review.decided_at != occurred_at:
                    raise KnowledgeLifecycleError(
                        "INVALID_REVIEW", "review decision and transition time differ",
                    )
            elif review is not None:
                raise KnowledgeLifecycleError("INVALID_REVIEW", "review belongs only to review transitions")
            reviewer_id = review.reviewer_id if review else None
            review_reason = review.reason_code if review else None
            review_evidence = review.evidence_ref if review else None
            try:
                connection.execute("""
                    UPDATE retrieval.knowledge_source_revision_lifecycle
                    SET status=%s, version=version+1, reviewer_id=COALESCE(%s, reviewer_id),
                        review_reason_code=COALESCE(%s, review_reason_code),
                        review_evidence_ref=COALESCE(%s, review_evidence_ref),
                        changed_at=%s
                    WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                """, (
                    target_state.value, reviewer_id, review_reason, review_evidence,
                    occurred_at, tenant_id, source_id, revision_id,
                ))
            except psycopg.errors.ObjectNotInPrerequisiteState as exc:
                raise KnowledgeLifecycleError("INVALID_TRANSITION", str(exc)) from exc
            self._audit(
                connection, tenant_id, source_id, revision_id, current,
                target_state, actor_id, reason_code, evidence_ref, occurred_at,
            )
        return target_state

    def publish(
        self,
        *,
        publication_scope: PublicationScope,
        generation_id: str,
        manifest_hash: str,
        expected_version: int,
        gate: GateApproval,
        actor_id: str,
        occurred_at: datetime,
    ) -> PinnedKnowledgeManifest:
        gate.require(publication_scope.pointer_kind.required_gate_id)
        if not actor_id.strip() or occurred_at.tzinfo is None:
            raise KnowledgeLifecycleError("INVALID_AUDIT", "publication audit is incomplete")
        with self.pool.transaction() as connection:
            manifest = self._manifest_for_publication(
                connection, publication_scope, generation_id, manifest_hash,
            )
            pointer = self._pointer(connection, publication_scope, for_update=True)
            actual_version = int(pointer[3]) if pointer else 0
            if actual_version != expected_version:
                raise KnowledgeLifecycleError("VERSION_CONFLICT", "publication pointer version conflict")
            if pointer:
                previous_manifest = self._manifest_for_publication(
                    connection, publication_scope, str(pointer[0]), str(pointer[1]),
                    allow_historical=True,
                )
                self._supersede_active_entries(
                    connection, previous_manifest, occurred_at, actor_id,
                    "POINTER_MANIFEST_SUPERSEDED",
                )
            self._activate_staged_entries(
                connection, manifest, publication_scope, occurred_at, actor_id,
            )
            previous_generation = str(pointer[0]) if pointer else None
            previous_hash = str(pointer[1]) if pointer else None
            version = actual_version + 1
            if pointer:
                connection.execute("""
                    UPDATE retrieval.knowledge_publication_pointers
                    SET generation_id=%s, manifest_hash=%s,
                        previous_generation_id=%s, previous_manifest_hash=%s,
                        version=%s, gate_id=%s, gate_evidence_sha256=%s,
                        changed_by=%s, changed_at=%s
                    WHERE tenant_id=%s AND backend_id=%s AND pointer_kind=%s
                      AND scope=%s AND locale=%s AND product=%s AND region=%s
                """, (
                    generation_id, manifest_hash, previous_generation, previous_hash,
                    version, gate.gate_id, gate.evidence_sha256, actor_id, occurred_at,
                    *publication_scope.key,
                ))
            else:
                connection.execute("""
                    INSERT INTO retrieval.knowledge_publication_pointers (
                        tenant_id, backend_id, pointer_kind, scope, locale, product, region,
                        generation_id, manifest_hash, previous_generation_id,
                        previous_manifest_hash, version, gate_id, gate_evidence_sha256,
                        changed_by, changed_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,%s)
                """, (
                    *publication_scope.key, generation_id, manifest_hash, version,
                    gate.gate_id, gate.evidence_sha256, actor_id, occurred_at,
                ))
            self._publication_audit(
                connection, publication_scope, generation_id, manifest_hash,
                previous_generation, version, "ACTIVATE", gate, actor_id, occurred_at,
            )
        return PinnedKnowledgeManifest(
            f"publication:{version}", generation_id, manifest_hash, version,
            publication_scope,
        )

    def rollback(
        self,
        *,
        publication_scope: PublicationScope,
        expected_version: int,
        gate: GateApproval,
        actor_id: str,
        occurred_at: datetime,
    ) -> PinnedKnowledgeManifest:
        gate.require(publication_scope.pointer_kind.required_gate_id)
        with self.pool.transaction() as connection:
            pointer = self._pointer(connection, publication_scope, for_update=True)
            if pointer is None or int(pointer[3]) != expected_version:
                raise KnowledgeLifecycleError("VERSION_CONFLICT", "publication pointer version conflict")
            current_generation, current_hash, previous_generation, version, previous_hash = pointer
            if previous_generation is None or previous_hash is None:
                raise KnowledgeLifecycleError("ROLLBACK_UNAVAILABLE", "no verified previous manifest")
            manifest = self._manifest_for_publication(
                connection, publication_scope, str(previous_generation), str(previous_hash),
                allow_historical=True,
            )
            self._reject_terminal_entries(connection, manifest)
            current_manifest = self._manifest_for_publication(
                connection, publication_scope, str(current_generation),
                str(current_hash), allow_historical=True,
            )
            self._supersede_active_entries(
                connection, current_manifest, occurred_at, actor_id,
                "MANIFEST_ROLLED_BACK",
            )
            next_version = int(version) + 1
            connection.execute("""
                UPDATE retrieval.knowledge_publication_pointers
                SET generation_id=%s, manifest_hash=%s,
                    previous_generation_id=%s, previous_manifest_hash=%s,
                    version=%s, gate_id=%s, gate_evidence_sha256=%s,
                    changed_by=%s, changed_at=%s
                WHERE tenant_id=%s AND backend_id=%s AND pointer_kind=%s
                  AND scope=%s AND locale=%s AND product=%s AND region=%s
            """, (
                previous_generation, previous_hash, current_generation, current_hash,
                next_version, gate.gate_id, gate.evidence_sha256, actor_id, occurred_at,
                *publication_scope.key,
            ))
            self._publication_audit(
                connection, publication_scope, str(previous_generation),
                str(previous_hash), str(current_generation), next_version,
                "ROLLBACK", gate, actor_id, occurred_at,
            )
        return PinnedKnowledgeManifest(
            f"rollback:{next_version}", str(previous_generation), str(previous_hash),
            next_version, publication_scope,
        )

    def pin_request(
        self, *, request_id: str, tenant_id: str, backend_id: str,
        scope: str, locale: str, product: str, region: str,
    ) -> PinnedKnowledgeManifest:
        if not request_id.strip():
            raise KnowledgeLifecycleError("INVALID_MANIFEST_PIN", "request id is required")
        scoped = PublicationScope(
            tenant_id, backend_id, PublicationPointerKind.SCOPED_CANARY,
            scope, locale, product, region,
        )
        global_scope = PublicationScope(
            tenant_id, backend_id, PublicationPointerKind.GLOBAL, "*", "*", "", "*",
        )
        with self.pool.transaction() as connection:
            existing = connection.execute("""
                SELECT tenant_id, backend_id, generation_id, manifest_hash,
                       pointer_version,
                       pointer_kind, scope, locale, product, region
                FROM retrieval.knowledge_request_manifest_pins WHERE request_id=%s
            """, (request_id,)).fetchone()
            if existing:
                if existing[:2] != (tenant_id, backend_id):
                    raise KnowledgeLifecycleError(
                        "MANIFEST_PIN_CONFLICT", "request manifest authority changed",
                    )
                selected_scope = scoped if existing[5] == "SCOPED_CANARY" else global_scope
                if (
                    existing[6:] != (
                        selected_scope.scope, selected_scope.locale,
                        selected_scope.product, selected_scope.region,
                    )
                ):
                    raise KnowledgeLifecycleError(
                        "MANIFEST_PIN_CONFLICT", "request manifest scope changed",
                    )
                return PinnedKnowledgeManifest(
                    request_id, str(existing[2]), str(existing[3]), int(existing[4]),
                    selected_scope,
                )
            selected_scope = scoped
            pointer = self._pointer(connection, scoped, for_update=False)
            if pointer is None:
                selected_scope = global_scope
                pointer = self._pointer(connection, global_scope, for_update=False)
            if pointer is None:
                raise KnowledgeLifecycleError("UNAVAILABLE", "no applicable knowledge manifest")
            generation_id, manifest_hash, _previous, version, _previous_hash = pointer
            conflicts = connection.execute("""
                SELECT source_id FROM retrieval.knowledge_source_manifest_entries
                WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
                GROUP BY source_id HAVING count(DISTINCT revision_id) > 1
                LIMIT 1
            """, (tenant_id, backend_id, generation_id)).fetchone()
            if conflicts:
                raise KnowledgeLifecycleError("CONFLICT", "active source revision conflict")
            connection.execute("""
                INSERT INTO retrieval.knowledge_request_manifest_pins (
                    request_id, tenant_id, backend_id, pointer_kind, scope, locale,
                    product, region, generation_id, manifest_hash, pointer_version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                request_id, *selected_scope.key[:2], selected_scope.pointer_kind.value,
                selected_scope.scope, selected_scope.locale, selected_scope.product,
                selected_scope.region, generation_id, manifest_hash, version,
            ))
        return PinnedKnowledgeManifest(
            request_id, str(generation_id), str(manifest_hash), int(version),
            selected_scope,
        )

    def submit_candidate(self, candidate: KnowledgeCandidate) -> None:
        with self.pool.transaction() as connection:
            inserted = connection.execute("""
                INSERT INTO retrieval.knowledge_candidates (
                    candidate_id, tenant_id, kind, sanitized_payload,
                    payload_sha256, source_ref, privacy_review_ref,
                    schema_version, created_at
                ) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING RETURNING candidate_id
            """, (
                candidate.candidate_id, candidate.tenant_id, candidate.kind.value,
                json.dumps(candidate.sanitized_payload, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")),
                candidate.payload_sha256, candidate.source_ref,
                candidate.privacy_review_ref,
                candidate.schema_version, candidate.created_at,
            )).fetchone()
            if inserted is None:
                row = connection.execute("""
                    SELECT tenant_id, kind, payload_sha256, source_ref,
                           privacy_review_ref,
                           schema_version, created_at
                    FROM retrieval.knowledge_candidates
                    WHERE candidate_id=%s
                """, (candidate.candidate_id,)).fetchone()
                if row != (
                    candidate.tenant_id, candidate.kind.value,
                    candidate.payload_sha256, candidate.source_ref,
                    candidate.privacy_review_ref,
                    candidate.schema_version, candidate.created_at,
                ):
                    raise KnowledgeLifecycleError("CANDIDATE_CONFLICT", "candidate identity changed")

    @staticmethod
    def _insert_revision(connection, revision: SourceRevision) -> None:
        connection.execute("""
            INSERT INTO retrieval.knowledge_source_revisions (
                tenant_id, source_id, revision_id, checksum, title, source_type,
                content, effective_from, effective_to, immutable_fingerprint,
                owner_id, scope, locale, product, region,
                supersedes_revision_id, operations_audit_ref, schema_version
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, source_id, revision_id) DO NOTHING
        """, (
            revision.tenant_id, revision.source_id, revision.revision_id,
            revision.checksum, revision.title, revision.source_type,
            revision.content, revision.effective_from, revision.effective_to,
            revision.immutable_fingerprint, revision.owner_id, revision.scope,
            revision.locale, revision.product, revision.region,
            revision.supersedes_revision_id, revision.operations_audit_ref,
            revision.schema_version,
        ))
        row = connection.execute("""
            SELECT immutable_fingerprint FROM retrieval.knowledge_source_revisions
            WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
        """, (revision.tenant_id, revision.source_id, revision.revision_id)).fetchone()
        if row is None or row[0] != revision.immutable_fingerprint:
            raise KnowledgeLifecycleError("SOURCE_REVISION_CONFLICT", "revision identity changed")

    @staticmethod
    def _audit(
        connection, tenant_id: str, source_id: str, revision_id: str,
        current: SourceRevisionStatus | None, target: SourceRevisionStatus,
        actor_id: str, reason_code: str, evidence_ref: str, occurred_at: datetime,
    ) -> None:
        body = "\0".join((
            tenant_id, source_id, revision_id, current.value if current else "",
            target.value, actor_id, reason_code, evidence_ref, occurred_at.isoformat(),
        ))
        audit_id = f"knowledge-audit-v1-{hashlib.sha256(body.encode()).hexdigest()}"
        connection.execute("""
            INSERT INTO retrieval.knowledge_source_revision_audit (
                audit_id, tenant_id, source_id, revision_id, from_status,
                to_status, actor_id, reason_code, evidence_ref, occurred_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
        """, (
            audit_id, tenant_id, source_id, revision_id,
            current.value if current else None, target.value, actor_id,
            reason_code, evidence_ref, occurred_at,
        ))

    @staticmethod
    def _pointer(connection, publication_scope: PublicationScope, *, for_update: bool):
        return connection.execute("""
            SELECT generation_id, manifest_hash, previous_generation_id,
                   version, previous_manifest_hash
            FROM retrieval.knowledge_publication_pointers
            WHERE tenant_id=%s AND backend_id=%s AND pointer_kind=%s
              AND scope=%s AND locale=%s AND product=%s AND region=%s
        """ + (" FOR UPDATE" if for_update else ""), publication_scope.key).fetchone()

    @staticmethod
    def _manifest_for_publication(
        connection, publication_scope: PublicationScope, generation_id: str,
        manifest_hash: str, *, allow_historical: bool = False,
    ):
        generation = connection.execute("""
            SELECT state, manifest_hash FROM retrieval.retrieval_generation_registry
            WHERE corpus='KNOWLEDGE' AND backend_id=%s AND generation_id=%s
        """, (publication_scope.backend_id, generation_id)).fetchone()
        allowed_states = {"READY", "ACTIVE", "RETIRED"} if allow_historical else {"READY", "ACTIVE"}
        if generation is None or generation[0] not in allowed_states or generation[1] != manifest_hash:
            raise KnowledgeLifecycleError("MANIFEST_NOT_VERIFIED", "generation is not publishable")
        filters = [publication_scope.tenant_id, publication_scope.backend_id, generation_id, manifest_hash]
        scope_sql = ""
        if publication_scope.pointer_kind is PublicationPointerKind.SCOPED_CANARY:
            scope_sql = " AND scope=%s AND locale=%s AND product=%s"
            filters.extend((publication_scope.scope, publication_scope.locale, publication_scope.product))
        row = connection.execute("""
            SELECT tenant_id, backend_id, generation_id, scope, locale, product,
                   manifest_hash
            FROM retrieval.knowledge_source_manifests
            WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
              AND manifest_hash=%s
        """ + scope_sql, tuple(filters)).fetchone()
        if row is None:
            raise KnowledgeLifecycleError("MANIFEST_NOT_VERIFIED", "source manifest is not verified")
        return row

    @staticmethod
    def _manifest_entries(connection, manifest):
        return connection.execute("""
            SELECT entry.source_id, entry.revision_id, lifecycle.status,
                   lifecycle.version, revision.scope, revision.locale,
                   revision.product, revision.region
            FROM retrieval.knowledge_source_manifest_entries entry
            JOIN retrieval.knowledge_source_revision_lifecycle lifecycle
              ON lifecycle.tenant_id=entry.tenant_id
             AND lifecycle.source_id=entry.source_id
             AND lifecycle.revision_id=entry.revision_id
            JOIN retrieval.knowledge_source_revisions revision
              ON revision.tenant_id=entry.tenant_id
             AND revision.source_id=entry.source_id
             AND revision.revision_id=entry.revision_id
            WHERE entry.tenant_id=%s AND entry.backend_id=%s
              AND entry.generation_id=%s AND entry.scope=%s
              AND entry.locale=%s AND entry.product=%s
            ORDER BY entry.source_id, entry.revision_id
            FOR UPDATE OF lifecycle
        """, manifest[:6]).fetchall()

    def _activate_staged_entries(
        self, connection, manifest, publication_scope, occurred_at, actor_id,
    ):
        entries = self._manifest_entries(connection, manifest)
        if not entries or any(row[2] != "STAGED" for row in entries):
            raise KnowledgeLifecycleError("MANIFEST_NOT_STAGED", "all source revisions must be STAGED")
        if (
            publication_scope.pointer_kind is PublicationPointerKind.SCOPED_CANARY
            and any(
                row[4:] != (
                    publication_scope.scope, publication_scope.locale,
                    publication_scope.product, publication_scope.region,
                )
                for row in entries
            )
        ):
            raise KnowledgeLifecycleError(
                "INVALID_PUBLICATION_SCOPE",
                "source applicability differs from canary pointer",
            )
        for source_id, revision_id, _status, version, *_applicability in entries:
            if publication_scope.pointer_kind is PublicationPointerKind.GLOBAL:
                active = connection.execute("""
                    SELECT revision_id, version
                    FROM retrieval.knowledge_source_revision_lifecycle
                    WHERE tenant_id=%s AND source_id=%s AND status='ACTIVE'
                      AND revision_id<>%s FOR UPDATE
                """, (manifest[0], source_id, revision_id)).fetchall()
                for old_revision_id, old_version in active:
                    connection.execute("""
                        UPDATE retrieval.knowledge_source_revision_lifecycle
                        SET status='SUPERSEDED', version=%s, changed_at=%s
                        WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
                    """, (
                        int(old_version) + 1, occurred_at, manifest[0],
                        source_id, old_revision_id,
                    ))
                    self._audit(
                        connection, manifest[0], source_id, str(old_revision_id),
                        SourceRevisionStatus.ACTIVE,
                        SourceRevisionStatus.SUPERSEDED, actor_id,
                        "GLOBAL_MANIFEST_SUPERSEDED", manifest[6], occurred_at,
                    )
            connection.execute("""
                UPDATE retrieval.knowledge_source_revision_lifecycle
                SET status='ACTIVE', version=version+1, changed_at=%s
                WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
            """, (occurred_at, manifest[0], source_id, revision_id))
            self._audit(
                connection, manifest[0], source_id, revision_id,
                SourceRevisionStatus.STAGED, SourceRevisionStatus.ACTIVE,
                actor_id, "MANIFEST_ACTIVATED", manifest[6], occurred_at,
            )

    def _supersede_active_entries(
        self, connection, manifest, occurred_at, actor_id, reason_code,
    ):
        for source_id, revision_id, status, version, *_applicability in self._manifest_entries(
            connection, manifest,
        ):
            if status != "ACTIVE":
                continue
            connection.execute("""
                UPDATE retrieval.knowledge_source_revision_lifecycle
                SET status='SUPERSEDED', version=%s, changed_at=%s
                WHERE tenant_id=%s AND source_id=%s AND revision_id=%s
            """, (
                int(version) + 1, occurred_at, manifest[0], source_id, revision_id,
            ))
            self._audit(
                connection, manifest[0], source_id, revision_id,
                SourceRevisionStatus.ACTIVE, SourceRevisionStatus.SUPERSEDED,
                actor_id, reason_code, manifest[6], occurred_at,
            )

    def _reject_terminal_entries(self, connection, manifest):
        entries = self._manifest_entries(connection, manifest)
        if not entries or any(row[2] in {"RETRACTED", "REJECTED"} for row in entries):
            raise KnowledgeLifecycleError("ROLLBACK_UNAVAILABLE", "historical manifest contains terminal revision")

    @staticmethod
    def _publication_audit(
        connection, scope: PublicationScope, generation_id: str,
        manifest_hash: str, previous_generation: str | None, version: int,
        action: str, gate: GateApproval, actor_id: str, occurred_at: datetime,
    ) -> None:
        body = "\0".join((*scope.key, generation_id, manifest_hash,
                           previous_generation or "", str(version), action))
        publication_id = f"knowledge-publication-v1-{hashlib.sha256(body.encode()).hexdigest()}"
        connection.execute("""
            INSERT INTO retrieval.knowledge_publication_audit (
                publication_id, tenant_id, backend_id, pointer_kind, scope,
                locale, product, region, generation_id, manifest_hash,
                previous_generation_id, pointer_version, action, gate_id,
                gate_evidence_sha256, actor_id, occurred_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            publication_id, *scope.key, generation_id, manifest_hash,
            previous_generation, version, action, gate.gate_id,
            gate.evidence_sha256, actor_id, occurred_at,
        ))
