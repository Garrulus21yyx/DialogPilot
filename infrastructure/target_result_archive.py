"""Scoped, content-addressed Agent originals on the framework Store."""
from __future__ import annotations

import hashlib
import json
from langgraph.store.base import BaseStore


class ResultArchiveError(ValueError):
    pass


class TargetResultArchive:
    def __init__(self, store: BaseStore):
        self.store = store

    @staticmethod
    def namespace(context):
        identity = context.trusted_context
        scope = tuple(str(identity.get(key) or "") for key in
                      ("tenant_id", "user_id", "conversation_id"))
        if not all(scope):
            raise ResultArchiveError("result archive requires a bound conversation")
        item = context.work_item
        return ("target-originals", *scope, item.owner_agent,
                item.control.control_id if item.control else item.work_item_id)

    async def save(self, context, value: dict) -> str:
        # Content identity makes checkpoint replay idempotent, with no mutable alias.
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        key = hashlib.sha256(encoded.encode()).hexdigest()
        namespace = self.namespace(context)
        try:
            await self.store.aput(namespace, key, value, index=False)
        except Exception as exc:
            raise ResultArchiveError("result archive write unavailable") from exc
        return key

    async def load(self, context, reference: str) -> dict:
        namespace = self.namespace(context)
        try:
            item = await self.store.aget(namespace, reference)
        except Exception as exc:
            raise ResultArchiveError("result archive read unavailable") from exc
        if item is None:
            raise ResultArchiveError("result reference is unavailable in this task")
        value = item.value
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(encoded.encode()).hexdigest() != reference:
            raise ResultArchiveError("result reference content mismatch")
        return value

    async def read(self, context, reference: str, offset: int = 0, limit: int = 2000) -> dict:
        if offset < 0 or not 1 <= limit <= 4000:
            raise ResultArchiveError("invalid result page")
        value = await self.load(context, reference)
        content = value["content"]
        end = min(len(content), offset + limit)
        return {"reference": reference, "offset": offset, "total_characters": len(content),
                "text": content[offset:end], "next_offset": end if end < len(content) else None,
                "historical": True}


def result_pointer(reference: str, content: str) -> str:
    return json.dumps({"result_ref": reference, "total_characters": len(content),
        "preview": content[:400], "complete": False,
        "read_tool_result": {"reference": reference, "offset": 0},
        "note": "Preview only. Read required pages before concluding; absence from a page is not absence from the result."},
        ensure_ascii=False)
