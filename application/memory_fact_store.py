"""Memory-fact persistence port and local test implementation."""
from __future__ import annotations

import json
from typing import Any, Protocol


class MemoryFactStore(Protocol):
    backend: dict[str, str]

    def get(self, *, where: dict[str, Any], include=None) -> dict[str, list[str]]: ...

    def upsert(self, *, ids, documents, metadatas) -> None: ...

    def delete(self, *, where: dict[str, Any]) -> None: ...


class InMemoryMemoryFactStore:
    """Test/CLI implementation with the same narrow contract."""

    backend = {"mode": "memory", "location": "process"}

    def __init__(self):
        self.documents: dict[str, dict[str, Any]] = {}

    def get(self, *, where: dict[str, Any], include=None) -> dict[str, list[str]]:
        user_id = str(where.get("user_id") or "")
        values = [
            payload for payload in self.documents.values()
            if str(payload.get("user_id") or "") == user_id
        ]
        values.sort(key=lambda item: (
            str(item.get("key") or ""), str(item.get("observed_at") or ""),
            str(item.get("fact_id") or ""),
        ))
        return {"documents": [json.dumps(item, ensure_ascii=False) for item in values]}

    def upsert(self, *, ids, documents, metadatas) -> None:
        for fact_id, document in zip(ids, documents):
            self.documents[str(fact_id)] = json.loads(document)

    def delete(self, *, where: dict[str, Any]) -> None:
        filters: dict[str, Any] = {}
        items = where.get("$and", [where])
        for item in items:
            for field, raw in item.items():
                if isinstance(raw, dict):
                    raw = raw.get("$eq")
                filters[field] = raw
        if not filters:
            raise ValueError("memory fact deletion scope is required")
        self.documents = {
            fact_id: payload for fact_id, payload in self.documents.items()
            if not all(payload.get(field) == value for field, value in filters.items())
        }
