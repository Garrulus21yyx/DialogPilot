"""Read bounded evidence from committed publications, without running retrieval."""
from __future__ import annotations

from datetime import datetime, timezone

from application.conversation_evidence import ConversationEvidence, evidence_identity
from application.business_observation import BusinessObservation
from application.knowledge_tool_contract import evidence_items


class BusinessObservationUnavailable(ValueError):
    """The requested original is absent from the caller's conversation scope."""


class PostgresConversationEvidence:
    def __init__(self, pool, validator=None, *, recent_limit=8, pack_limit=3):
        self._pool, self._validator = pool, validator
        self._recent_limit, self._pack_limit = recent_limit, pack_limit

    def load(self, invocation):
        # No Redis dependency or second mutable evidence cache. Both final and
        # interaction publications carry the same private evidence metadata.
        with self._pool.transaction() as connection:
            rows = connection.execute("""
                SELECT publication_id, verification
                FROM dialogpilot_app.response_deliveries
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND invocation_key IS DISTINCT FROM %s
                ORDER BY seq DESC LIMIT %s
            """, (str(invocation.tenant_id), str(invocation.user_id),
                  str(invocation.conversation_id), str(invocation.invocation_key),
                  self._recent_limit)).fetchall()
        return ConversationEvidence(self._knowledge(rows), self._business(rows))

    def read_business(self, invocation, *, publication_id, observation_id):
        """Resolve an immutable original, not a new business lookup or cached state.

        Read by authenticated scope before resolving the content identity. A
        guessed reference cannot reveal whether another conversation owns it.
        """
        with self._pool.transaction() as connection:
            row = connection.execute("""
                SELECT verification FROM dialogpilot_app.response_deliveries
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND publication_id=%s
            """, (str(invocation.tenant_id), str(invocation.user_id),
                  str(invocation.conversation_id), publication_id)).fetchone()
        if row is not None:
            for entry in (row[0] or {}).get("business_observations", ()):
                try:
                    original = BusinessObservation.model_validate(entry)
                except ValueError:
                    continue
                if original.observation_id == observation_id:
                    return original
        raise BusinessObservationUnavailable("business observation unavailable in this conversation")

    @staticmethod
    def _business(rows):
        result, seen = [], set()
        for publication_id, verification in rows:
            for entry in (verification or {}).get("business_observations", ()):
                try:
                    observation = BusinessObservation.model_validate(entry)
                except ValueError:
                    result.append({"publication_id": publication_id, "status": "INVALID",
                                   "reason_code": "BUSINESS_OBSERVATION_INVALID"})
                    continue
                key = observation.observation_id
                if key in seen:
                    continue
                seen.add(key)
                result.append({"publication_id": publication_id, "status": "HISTORICAL",
                               "observation_id": key,
                               "observation": observation.model_dump(mode="json")})
        return tuple(result)

    def _knowledge(self, rows):
        seen, result = set(), []
        checked_at = datetime.now(timezone.utc).isoformat()
        for publication_id, verification in rows:
            for entry in (verification or {}).get("knowledge_evidence", ()):
                try:
                    evidence_items(entry["pack"])
                    observed = datetime.fromisoformat(entry["observed_at"])
                    if observed.utcoffset() is None:
                        raise ValueError("knowledge observation time requires timezone")
                    key = evidence_identity(entry)
                except (ValueError, KeyError, TypeError) as exc:
                    result.append({"publication_id": publication_id,
                        "status": "INVALID", "reason_code": type(exc).__name__})
                    if len(result) == self._pack_limit:
                        return tuple(result)
                    continue
                if key in seen:
                    continue
                seen.add(key)
                valid = self._validator is not None and self._validator([entry["pack"]])
                result.append({"publication_id": publication_id,
                    "observed_at": entry["observed_at"], "checked_at": checked_at,
                    "status": "CURRENT" if valid else "NOT_REUSABLE",
                    **({"pack": entry["pack"]} if valid else {})})
                if len(result) == self._pack_limit:
                    return tuple(result)
        return tuple(result)
