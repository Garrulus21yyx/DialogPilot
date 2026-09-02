"""PostgreSQL persistence adapter for versioned user memory facts."""
from __future__ import annotations

import json
from typing import Any


class PostgresMemoryFactStore:
    """Expose the narrow document-store contract consumed by ``MemoryManager``."""

    backend = {"mode": "postgres", "location": "dialogpilot_app.memory_facts"}

    def __init__(self, pool):
        self.pool = pool

    def get(self, *, where: dict[str, Any], include=None) -> dict[str, list[str]]:
        user_id = str(where.get("user_id") or "").strip()
        if not user_id:
            raise ValueError("memory fact user_id is required")
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT document
                FROM dialogpilot_app.memory_facts
                WHERE user_id=%s
                ORDER BY fact_key, observed_at, fact_id
            """, (user_id,)).fetchall()
        return {"documents": [json.dumps(row[0], ensure_ascii=False) for row in rows]}

    def upsert(self, *, ids, documents, metadatas) -> None:
        if not (len(ids) == len(documents) == len(metadatas)):
            raise ValueError("memory fact batch lengths differ")
        with self.pool.transaction() as connection:
            for fact_id, document, metadata in zip(ids, documents, metadatas):
                payload = json.loads(document)
                if str(payload.get("fact_id") or "") != str(fact_id):
                    raise ValueError("memory fact identity differs from document")
                row = connection.execute("""
                    INSERT INTO dialogpilot_app.memory_facts (
                        fact_id,user_id,fact_key,status,document,observed_at,
                        source_conversation_id,updated_at
                    ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                    ON CONFLICT (fact_id) DO UPDATE SET
                        status=EXCLUDED.status,
                        document=EXCLUDED.document,
                        observed_at=EXCLUDED.observed_at,
                        source_conversation_id=EXCLUDED.source_conversation_id,
                        updated_at=EXCLUDED.updated_at
                    WHERE dialogpilot_app.memory_facts.user_id=EXCLUDED.user_id
                      AND dialogpilot_app.memory_facts.fact_key=EXCLUDED.fact_key
                    RETURNING fact_id
                """, (
                    str(fact_id), str(metadata["user_id"]),
                    str(metadata["fact_key"]), str(metadata["status"]),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    str(metadata["observed_at"]),
                    str(metadata.get("source_conversation_id") or ""),
                    str(payload["updated_at"]),
                )).fetchone()
                if row is None:
                    raise ValueError("memory fact identity is immutable")

    def delete(self, *, where: dict[str, Any]) -> None:
        clauses = []
        values = []
        filters = {}
        for item in where.get("$and", [where]):
            filters.update(item)
        for field in ("user_id", "source_conversation_id"):
            raw = filters.get(field)
            if isinstance(raw, dict):
                raw = raw.get("$eq")
            value = str(raw or "").strip()
            if value:
                clauses.append(f"{field}=%s")
                values.append(value)
        if not clauses:
            raise ValueError("memory fact deletion scope is required")
        with self.pool.transaction() as connection:
            connection.execute(
                "DELETE FROM dialogpilot_app.memory_facts WHERE "
                + " AND ".join(clauses),
                tuple(values),
            )
