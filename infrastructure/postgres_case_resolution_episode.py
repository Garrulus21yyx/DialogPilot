"""Project accepted case-owner facts into canonical ServiceEpisode records."""

from __future__ import annotations

from application.case_resolution import (
    CASE_RESOLUTION_ACCEPTED,
    AcceptedCaseResolution,
    CaseResolutionSourceTurn,
    CaseResolutionSourceUnavailable,
    compile_service_episode,
)
from application.service_episode import ServiceEpisodeCommit


class PostgresCaseResolutionEpisodeProjector:
    """Compile only immutable CASE_RESOLUTION_ACCEPTED facts."""

    def __init__(self, pool, repository):
        self._pool = pool
        self._repository = repository

    def project_pending(self, *, limit: int = 100) -> tuple[ServiceEpisodeCommit, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("case resolution projection limit is invalid")
        with self._pool.transaction() as connection:
            rows = connection.execute(
                """
                SELECT event.event_id
                FROM dialogpilot_app.conversation_events event
                WHERE event.event_type=%s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM dialogpilot_app.service_episode_revisions revision
                    WHERE revision.episode_id=event.payload->>'ticket_id'
                      AND revision.revision=(event.payload->>'revision')::bigint
                  )
                ORDER BY event.created_at,event.event_id
                LIMIT %s
            """,
                (CASE_RESOLUTION_ACCEPTED, limit),
            ).fetchall()
        return tuple(self.project(str(row[0])) for row in rows)

    def project(self, event_id: str) -> ServiceEpisodeCommit:
        accepted, source_turns = self._load(event_id)
        candidate = compile_service_episode(accepted, source_turns)
        return self._repository.commit(candidate)

    def _load(
        self,
        event_id: str,
    ) -> tuple[AcceptedCaseResolution, tuple[CaseResolutionSourceTurn, ...]]:
        with self._pool.transaction() as connection:
            event = connection.execute(
                """
                SELECT event_id,event_type,payload,tenant_id,user_id,
                       conversation_id,deletion_epoch
                FROM dialogpilot_app.conversation_events
                WHERE event_id=%s
            """,
                (event_id,),
            ).fetchone()
            if event is None or str(event[1]) != CASE_RESOLUTION_ACCEPTED:
                raise CaseResolutionSourceUnavailable(
                    "accepted case resolution fact is unavailable"
                )
            accepted = AcceptedCaseResolution.from_event(
                str(event[0]),
                dict(event[2]),
            )
            envelope = (
                str(event[3]),
                str(event[4]),
                str(event[5]),
                int(event[6]),
            )
            expected = (
                accepted.subject.tenant_id,
                accepted.subject.user_id,
                accepted.subject.conversation_id,
                accepted.source_deletion_epoch,
            )
            if envelope != expected:
                raise CaseResolutionSourceUnavailable(
                    "accepted fact subject does not match its event envelope"
                )
            rows = connection.execute(
                """
                SELECT turn_key,role,content
                FROM dialogpilot_app.conversation_turns
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND invocation_key=%s AND turn_key=ANY(%s)
            """,
                (
                    accepted.subject.tenant_id,
                    accepted.subject.user_id,
                    accepted.subject.conversation_id,
                    accepted.source_invocation_key,
                    list(accepted.source_turn_refs),
                ),
            ).fetchall()
        by_ref = {
            str(row[0]): CaseResolutionSourceTurn(
                str(row[0]),
                str(row[1]),
                str(row[2]),
            )
            for row in rows
        }
        try:
            source_turns = tuple(by_ref[ref] for ref in accepted.source_turn_refs)
        except KeyError as exc:
            raise CaseResolutionSourceUnavailable(
                "accepted case resolution source turn is unavailable"
            ) from exc
        return accepted, source_turns
