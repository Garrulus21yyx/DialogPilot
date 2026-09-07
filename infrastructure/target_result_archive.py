"""Scoped, content-addressed Agent originals on the framework Store."""
from __future__ import annotations

import hashlib
import json
import asyncio
from langgraph.store.base import BaseStore
from application.conversation_projection import ConversationSubject


class ResultArchiveError(ValueError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class TargetResultArchive:
    def __init__(self, store: BaseStore, *, subject_fence=None):
        self.store = store
        self.subject_fence = subject_fence

    async def _check_subject(self, context):
        if self.subject_fence is None:
            return
        scope = self.namespace(context)[1:4]
        subject = ConversationSubject(*scope)
        try:
            fence = await asyncio.to_thread(self.subject_fence, subject)
        except Exception as exc:
            raise ResultArchiveError("conversation lifecycle unavailable", retryable=True) from exc
        if fence.deleted:
            await self.delete_subject(subject)
            raise ResultArchiveError("conversation deleted")

    async def delete_subject(self, subject):
        """Use SDK deletion; the conversation repository owns the tombstone."""
        prefix = ("target-originals", subject.tenant_id, subject.user_id, subject.conversation_id)
        while items := await self.store.asearch(prefix, limit=100):
            for item in items:
                await self.store.adelete(item.namespace, item.key)

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
        await self._check_subject(context)
        # Content identity makes checkpoint replay idempotent, with no mutable alias.
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        key = hashlib.sha256(encoded.encode()).hexdigest()
        namespace = self.namespace(context)
        try:
            await self.store.aput(namespace, key, value, index=False)
        except Exception as exc:
            raise ResultArchiveError("result archive write unavailable", retryable=True) from exc
        await self._check_subject(context)
        return key

    async def load(self, context, reference: str) -> dict:
        await self._check_subject(context)
        namespace = self.namespace(context)
        try:
            item = await self.store.aget(namespace, reference)
        except Exception as exc:
            raise ResultArchiveError("result archive read unavailable", retryable=True) from exc
        if item is None:
            raise ResultArchiveError("result reference is unavailable in this task")
        value = item.value
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(encoded.encode()).hexdigest() != reference:
            raise ResultArchiveError("result reference content mismatch")
        await self._check_subject(context)
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
