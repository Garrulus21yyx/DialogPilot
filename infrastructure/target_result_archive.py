"""Scoped, content-addressed Agent originals on the framework Store."""
from __future__ import annotations

import hashlib
import json
import asyncio
from langgraph.store.base import BaseStore
from application.conversation_projection import ConversationSubject


MAX_RESULT_PAGE_CHARS = 4000


class ResultArchiveError(ValueError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class ResultReferenceNotFound(ResultArchiveError):
    """The scoped lookup has no matching result or evidence item."""


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
            raise ResultReferenceNotFound("result reference is unavailable in this task")
        value = item.value
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(encoded.encode()).hexdigest() != reference:
            raise ResultArchiveError("result reference content mismatch")
        await self._check_subject(context)
        return value

    async def read(self, context, reference: str, offset: int = 0, limit: int | None = None,
                   evidence_id: str | None = None, *, max_tokens: int | None = None) -> dict:
        if offset < 0 or (limit is not None and not 1 <= limit <= MAX_RESULT_PAGE_CHARS):
            raise ResultArchiveError("invalid result page")
        value = await self.load(context, reference)
        content = value["content"]
        selected = None
        if evidence_id is not None:
            items = _evidence_view(content)
            selected = next((item for item in items if item["evidence_id"] == evidence_id), None)
            if selected is None:
                raise ResultReferenceNotFound("evidence reference is unavailable in this result")
            content = selected["text"]
        end = min(len(content), offset + limit) if limit is not None else len(content)
        if limit is None and max_tokens is None:
            end = min(end, offset + MAX_RESULT_PAGE_CHARS)

        def page(end):
            return {"reference": reference, "offset": offset, "total_characters": len(content),
                "text": content[offset:end], "next_offset": end if end < len(content) else None,
                "historical": True, **({"evidence_id": evidence_id,
                    "title": selected.get("title", ""), "source": selected["source"],
                    "offset_basis": "evidence_text"} if selected is not None else {})}
        if max_tokens is not None:
            from langchain_core.messages import ToolMessage
            from langchain_core.messages.utils import count_tokens_approximately
            def size(end):
                return count_tokens_approximately([ToolMessage(
                    content=json.dumps(page(end), ensure_ascii=False), tool_call_id="archive-read")])
            if size(end) <= max_tokens:
                return page(end)
            if size(min(len(content), offset + 1)) > max_tokens:
                raise ResultArchiveError("result read allowance cannot hold an envelope")
            lo, hi = min(len(content), offset + 1), end
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if size(mid) <= max_tokens:
                    lo = mid
                else:
                    hi = mid - 1
            end = lo
        return page(end)


def _evidence_view(content: str) -> list[dict]:
    """Recognize the bounded model evidence view; this does not attest its truth."""
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, dict) or value.get("status") != "OK":
        return []
    if "evidence_pack" in value:
        from application.knowledge_tool_contract import model_evidence
        value = model_evidence(value)
    items = value.get("evidence")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        return []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return []
        identity = item.get("evidence_id")
        if not isinstance(identity, str) or not identity or len(identity) > 128 or identity in seen:
            return []
        if not isinstance(item.get("text"), str) or not isinstance(item.get("source"), dict):
            return []
        if not isinstance(item.get("title", ""), str):
            return []
        seen.add(identity)
    return items


def result_pointer(reference: str, content: str) -> str:
    try:
        existing = json.loads(content)
    except (ValueError, TypeError):
        existing = None
    if isinstance(existing, dict) and existing.get("result_ref") == reference and existing.get("complete") is False:
        return content
    items = _evidence_view(content)
    navigation = ({"evidence_directory": [
        {"evidence_id": item["evidence_id"], "title": item.get("title", "")[:80],
         "preview": item["text"][:100], "total_characters": len(item["text"]),
         "read_tool_result": {"reference": reference, "evidence_id": item["evidence_id"],
                              "offset": 0, "limit": min(MAX_RESULT_PAGE_CHARS, max(1, len(item["text"])))}}
        for item in items],
        "evidence_reading": "Use read_tool_result with reference and evidence_id to read a specific evidence text with its source. Directory previews are incomplete, not sufficient evidence."} if items else {})
    return json.dumps({"result_ref": reference, "total_characters": len(content),
        "preview": content[:400], "complete": False, **navigation,
        "read_tool_result": {"reference": reference, "offset": 0},
        "note": "Preview only. Read required pages before concluding; absence from a page is not absence from the result."},
        ensure_ascii=False)
