"""Read bounded evidence from committed publications, without running retrieval."""
from __future__ import annotations

from datetime import datetime, timezone

from application.conversation_evidence import evidence_identity
from application.knowledge_tool_contract import evidence_items


class PostgresConversationEvidence:
    def __init__(self, pool, validator, *, recent_limit=8, pack_limit=3):
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
                valid = self._validator([entry["pack"]])
                result.append({"publication_id": publication_id,
                    "observed_at": entry["observed_at"], "checked_at": checked_at,
                    "status": "CURRENT" if valid else "NOT_REUSABLE",
                    **({"pack": entry["pack"]} if valid else {})})
                if len(result) == self._pack_limit:
                    return tuple(result)
        return tuple(result)
