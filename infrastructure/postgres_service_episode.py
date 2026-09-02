"""PostgreSQL canonical owner and retrieval resolver for ServiceEpisode."""
from __future__ import annotations

import hashlib

from psycopg.types.json import Jsonb

from application.data_location_registry import (
    DataWriteIntent,
    DurableWriteKind,
)
from application.service_episode import (
    EpisodeProjectionTarget,
    ServiceEpisodeCandidate,
    ServiceEpisodeCommit,
    ServiceEpisodeConflict,
    evidence_json,
)
from application.evidence_receipt import ServiceEpisodeLocator
from infrastructure.data_location_fence import PostgresDataLocationWriteFence


class PostgresServiceEpisodeRepository:
    location_id = "location:service-episode:v1"
    producer = "episode-projector"

    def __init__(self, pool, *, fence=None):
        self.pool = pool
        self.fence = fence or PostgresDataLocationWriteFence(pool)

    def commit(
        self,
        candidate: ServiceEpisodeCandidate,
        *,
        projection_target: EpisodeProjectionTarget | None = None,
    ) -> ServiceEpisodeCommit:
        self.fence.authorize(DataWriteIntent(
            location_id=self.location_id,
            producer=self.producer,
            kind=DurableWriteKind.PRODUCER,
            schema_version=candidate.schema_version,
            retention_class="customer_support",
            subject=candidate.subject,
            expected_deletion_epoch=candidate.source_deletion_epoch,
        ))
        projection_event_id = (
            _stable("episode-projection", candidate.episode_id,
                    candidate.revision, projection_target.backend_id,
                    projection_target.generation_id)
            if projection_target is not None else None
        )
        with self.pool.transaction() as connection:
            subject = connection.execute("""
                SELECT deletion_epoch, deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(candidate)).fetchone()
            if subject is None or subject[1] is not None or int(subject[0]) != (
                candidate.source_deletion_epoch
            ):
                raise ServiceEpisodeConflict("service episode subject is unavailable")
            head = connection.execute("""
                SELECT current_revision, expected_version
                FROM dialogpilot_app.service_episode_heads
                WHERE episode_id=%s FOR UPDATE
            """, (candidate.episode_id,)).fetchone()
            current_revision, current_version = (
                (int(head[0]), int(head[1])) if head else (0, 0)
            )
            existing = connection.execute("""
                SELECT provenance_sha256 FROM dialogpilot_app.service_episode_revisions
                WHERE episode_id=%s AND revision=%s
            """, (candidate.episode_id, candidate.revision)).fetchone()
            if existing is not None:
                if str(existing[0]) != candidate.provenance_sha256:
                    raise ServiceEpisodeConflict("service episode replay conflicts")
                if current_revision >= candidate.revision:
                    return ServiceEpisodeCommit(
                        candidate.episode_id, candidate.revision, current_version,
                        projection_event_id, True,
                    )
            if (candidate.revision, candidate.expected_version) != (
                current_revision + 1, current_version,
            ):
                raise ServiceEpisodeConflict("service episode head CAS mismatch")
            connection.execute("""
                INSERT INTO dialogpilot_app.service_episode_revisions (
                    episode_id,revision,tenant_id,user_id,conversation_id,
                    case_id,case_status,problem,product_version,symptoms,materials,
                    actions,authoritative_outcomes,resolution,root_cause,
                    outcome_verification_ref,verified_at,user_evidence,
                    assistant_evidence,source_event_refs,provenance_sha256,
                    extractor_version,schema_version,source_deletion_epoch
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s
                )
            """, (
                candidate.episode_id, candidate.revision, *_scope(candidate),
                candidate.case_id, candidate.case_status, candidate.problem,
                candidate.product_version or None, Jsonb(list(candidate.symptoms)),
                Jsonb(list(candidate.materials)), Jsonb(list(candidate.actions)),
                Jsonb(list(candidate.authoritative_outcomes)), candidate.resolution,
                candidate.root_cause or None,
                candidate.outcome_verification.verification_ref,
                candidate.outcome_verification.verified_at,
                Jsonb(evidence_json(candidate.user_evidence)),
                Jsonb(evidence_json(candidate.assistant_evidence)),
                Jsonb(list(candidate.source_event_refs)), candidate.provenance_sha256,
                candidate.extractor_version, candidate.schema_version,
                candidate.source_deletion_epoch,
            ))
            next_version = current_version + 1
            write = connection.execute("""
                INSERT INTO dialogpilot_app.service_episode_heads (
                    episode_id,current_revision,expected_version,tenant_id,user_id,
                    conversation_id,source_deletion_epoch
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (episode_id) DO UPDATE SET
                    current_revision=EXCLUDED.current_revision,
                    expected_version=EXCLUDED.expected_version,
                    updated_at=transaction_timestamp()
                WHERE service_episode_heads.expected_version=%s
            """, (
                candidate.episode_id, candidate.revision, next_version,
                *_scope(candidate), candidate.source_deletion_epoch,
                current_version,
            ))
            if write.rowcount != 1:
                raise ServiceEpisodeConflict("service episode head CAS lost")
            if projection_target is not None:
                connection.execute("""
                    INSERT INTO retrieval.canonical_projection_outbox (
                        event_id,corpus,tenant_id,backend_id,generation_id,
                        source_ref,source_revision,source_fingerprint,
                        subject_user_id,subject_conversation_id,deletion_epoch
                    ) VALUES (%s,'SERVICE_EPISODE',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id) DO NOTHING
                """, (
                    projection_event_id, candidate.subject.tenant_id,
                    projection_target.backend_id, projection_target.generation_id,
                    candidate.episode_id, str(candidate.revision),
                    candidate.provenance_sha256, candidate.subject.user_id,
                    candidate.subject.conversation_id,
                    candidate.source_deletion_epoch,
                ))
        return ServiceEpisodeCommit(
            candidate.episode_id, candidate.revision, next_version,
            projection_event_id, False,
        )


class PostgresServiceEpisodeResolver:
    corpus = "SERVICE_EPISODE"

    def project(self, connection, event):
        row = connection.execute("""
            SELECT revision.problem,revision.product_version,revision.symptoms,
                   revision.actions,revision.authoritative_outcomes,
                   revision.resolution,revision.root_cause,
                   revision.outcome_verification_ref,revision.verified_at,
                   revision.user_evidence,revision.assistant_evidence,
                   revision.provenance_sha256,revision.user_id,
                   revision.conversation_id,revision.source_deletion_epoch,
                   head.current_revision
            FROM dialogpilot_app.service_episode_revisions revision
            JOIN dialogpilot_app.service_episode_heads head
              ON head.episode_id=revision.episode_id
            WHERE revision.episode_id=%s AND revision.revision=%s
        """, (event.source_ref, int(event.source_revision))).fetchone()
        if row is None or int(row[15]) != int(event.source_revision):
            raise ServiceEpisodeConflict("canonical service episode is not current")
        if str(row[11]) != event.source_fingerprint:
            raise ServiceEpisodeConflict("canonical service episode source drift")
        user_text = " ".join(
            str(item.get("content") or "") for item in row[9]
        )
        assistant_text = " ".join(
            str(item.get("content") or "") for item in row[10]
        )
        canonical_text = " ".join(filter(None, (
            str(row[0]), str(row[1] or ""), " ".join(row[2]),
            " ".join(row[3]), " ".join(row[4]), str(row[5]),
            str(row[6] or ""),
        )))
        candidate_id = _stable(
            "service-episode-search", event.backend_id, event.generation_id,
            event.source_ref, event.source_revision,
        )
        connection.execute("""
            DELETE FROM retrieval.service_episode_search
            WHERE tenant_id=%s AND backend_id=%s AND generation_id=%s
              AND episode_id=%s
        """, (
            event.tenant_id, event.backend_id, event.generation_id,
            event.source_ref,
        ))
        connection.execute("""
            INSERT INTO retrieval.service_episode_search (
                candidate_id,tenant_id,user_id,entity_ids,
                source_conversation_id,backend_id,generation_id,episode_id,
                episode_revision,outcome_receipt_ref,provenance_sha256,
                deletion_epoch,verified_at,embedding,lexical_document,
                user_lexical_document,assistant_lexical_document,projected_at
            ) VALUES (
                %s,%s,%s,'{}',%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,
                transaction_timestamp()
            )
        """, (
            candidate_id, event.tenant_id, row[12], row[13], event.backend_id,
            event.generation_id, event.source_ref, event.source_revision,
            row[7], row[11], row[14], row[8], canonical_text,
            user_text, assistant_text,
        ))
        return (candidate_id, row[11])


class PostgresServiceEpisodeEvidenceResolver:
    """Dereference a search hit back to canonical role/provenance evidence."""

    def __init__(self, pool):
        self.pool = pool

    def resolve(self, locator: ServiceEpisodeLocator) -> dict[str, object]:
        if not isinstance(locator, ServiceEpisodeLocator):
            raise ValueError("service episode locator is required")
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT revision.outcome_verification_ref,revision.verified_at,
                       revision.user_evidence,revision.assistant_evidence,
                       revision.provenance_sha256
                FROM retrieval.service_episode_search search
                JOIN dialogpilot_app.service_episode_revisions revision
                  ON revision.episode_id=search.episode_id
                 AND revision.revision::text=search.episode_revision
                JOIN dialogpilot_app.service_episode_heads head
                  ON head.episode_id=revision.episode_id
                 AND head.current_revision=revision.revision
                WHERE search.tenant_id=%s AND search.user_id=%s
                  AND search.backend_id=%s AND search.generation_id=%s
                  AND search.episode_id=%s AND search.episode_revision=%s
                  AND search.provenance_sha256=%s
            """, (
                locator.tenant_id, locator.user_id, locator.backend_id,
                locator.generation_id, locator.episode_id,
                locator.episode_revision, locator.provenance_sha256,
            )).fetchone()
        if row is None or str(row[4]) != locator.provenance_sha256:
            raise KeyError("service episode evidence is unavailable")
        return {
            "tenant_id": locator.tenant_id,
            "user_id": locator.user_id,
            "backend_id": locator.backend_id,
            "generation_id": locator.generation_id,
            "episode_id": locator.episode_id,
            "episode_revision": locator.episode_revision,
            "outcome_receipt_ref": str(row[0]),
            "provenance_sha256": str(row[4]),
            "verified_at": row[1].isoformat(),
            "user_evidence_refs": [
                str(item["source_event_ref"]) for item in row[2]
            ],
            "assistant_evidence_refs": [
                str(item["source_event_ref"]) for item in row[3]
            ],
        }


def _scope(candidate: ServiceEpisodeCandidate) -> tuple[str, str, str]:
    return (
        candidate.subject.tenant_id, candidate.subject.user_id,
        candidate.subject.conversation_id,
    )


def _stable(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()
