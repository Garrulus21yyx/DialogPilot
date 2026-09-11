"""Version-fenced Redis projection of the PostgreSQL conversation reader."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import logging
import os
import random
from copy import deepcopy

from pydantic import TypeAdapter
from application.memory_projection import MemoryProjectionResult, MemoryProjectionState, MemoryRetrievalOutcome
from memory.conversation_memory import MemoryContext
from core.capacity_metrics import decisions

logger = logging.getLogger(__name__)
_RESULT = TypeAdapter(MemoryProjectionResult)
_CONTEXT = TypeAdapter(MemoryContext)
_DELETED = b'{"deleted":true}'


class ConversationContextCache:
    """One replaceable entry per tenant/user/conversation; TTL is not freshness."""

    def __init__(self, redis, *, ttl_seconds=300, jitter_seconds=60):
        if ttl_seconds < 1 or jitter_seconds < 0:
            raise ValueError("invalid cache TTL")
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.jitter_seconds = jitter_seconds

    @staticmethod
    def key(scope):
        digest = hashlib.sha256(json.dumps(list(scope), ensure_ascii=False).encode()).hexdigest()
        return f"target-context:v1:{digest}"

    async def get(self, scope, revision, request_id):
        try:
            raw = await asyncio.to_thread(self.redis.get, self.key(scope))
            if raw is None:
                return None
            if raw == _DELETED or raw == _DELETED.decode():
                return None
            value = json.loads(raw)
            if value["identity"] != [list(scope), list(revision), request_id]:
                return None
            result = _RESULT.validate_python(value["result"])
            return replace(result, context=_CONTEXT.validate_python(result.context))
        except Exception as error:
            logger.warning("Conversation cache read unavailable or invalid: %s", type(error).__name__)
            return None

    async def put(self, scope, revision, request_id, result):
        await asyncio.to_thread(self.put_sync, scope, revision, request_id, result)

    def put_sync(self, scope, revision, request_id, result):
        try:
            payload = json.dumps({"identity": [list(scope), list(revision), request_id],
                "result": _RESULT.dump_python(result, mode="json")}, ensure_ascii=False)
            key = self.key(scope)
            # SDK optimistic transaction also fences an unknown/timed-out EXEC:
            # deletion changes the watched key, so a delayed EXEC cannot revive it.
            # WatchError is a discarded cache fill, never a retry of an old value.
            with self.redis.pipeline() as pipe:
                pipe.watch(key)
                current = pipe.get(key)
                if current == _DELETED or current == _DELETED.decode():
                    return
                pipe.multi()
                pipe.set(key, payload, ex=self.ttl_seconds + random.randint(0, self.jitter_seconds))
                pipe.execute()
        except Exception as error:
            logger.warning("Conversation cache fill unavailable: %s", type(error).__name__)

    async def delete_subject(self, subject):
        # Deletion worker retries failures. Normal reads do not require Redis.
        await asyncio.to_thread(self.redis.set,
            self.key((subject.tenant_id, subject.user_id, subject.conversation_id)),
            _DELETED, ex=self.ttl_seconds)


class CachedConversationReader:
    """Redis never supplies authority. A PostgreSQL revision fences every read.

    A concurrent commit invalidates the attempt, including cache hits. Retrying
    reads does not replay tools or business writes. Late cache fills are harmless:
    the envelope must match the current revision and request exclusion.
    """

    def __init__(self, reader, cache, *, max_inflight=None, max_callers=None):
        self.reader = reader
        self.cache = cache
        self.max_inflight = int(os.getenv("CONTEXT_MAX_INFLIGHT", "8")) if max_inflight is None else max_inflight
        self.max_callers = int(os.getenv("CONTEXT_MAX_CALLERS", "128")) if max_callers is None else max_callers
        if min(self.max_inflight, self.max_callers) < 1:
            raise ValueError("context capacity must be positive")
        self._inflight = {}
        self._callers = 0

    def revision(self, scope):
        with self.reader.pool.transaction() as connection:
            return self._revision(connection, scope)

    @staticmethod
    def _revision(connection, scope):
        row = connection.execute("""
                SELECT c.next_event_seq, c.deletion_epoch,
                       COALESCE(s.generation,0), COALESCE(s.expected_version,0),
                       COALESCE(s.projection_watermark,0), COALESCE(s.state,'EMPTY'),
                       (SELECT COALESCE(jsonb_agg(jsonb_build_array(
                           r.projection_name, r.generation, r.enabled,
                           COALESCE(w.last_event_seq,0)) ORDER BY r.projection_name), '[]')::text
                        FROM dialogpilot_app.projection_registry r
                        LEFT JOIN dialogpilot_app.projection_watermarks w
                          ON w.projection_name=r.projection_name AND w.generation=r.generation
                         AND (w.tenant_id,w.user_id,w.conversation_id)=
                             (c.tenant_id,c.user_id,c.conversation_id)
                        WHERE r.projection_name IN ('working_window','thread_summary','fact_extraction'))
                FROM dialogpilot_app.conversations c
                LEFT JOIN dialogpilot_app.thread_summary_checkpoints s
                  ON (s.tenant_id,s.user_id,s.conversation_id)=(c.tenant_id,c.user_id,c.conversation_id)
                WHERE c.tenant_id=%s AND c.user_id=%s AND c.conversation_id=%s
                  AND c.deleted_at IS NULL
            """, tuple(scope)).fetchone()
        return tuple(row) if row else None

    async def fill(self, scope, revision, request_id, result):
        await asyncio.to_thread(self._fill_sync, scope, revision, request_id, result)

    def _fill_sync(self, scope, revision, request_id, result):
        # Serialize cache fills against deletion's authoritative row update.
        # The bounded Redis write completes before deletion can commit and its
        # existing outbox can purge the entry. No late fill can resurrect data.
        with self.reader.pool.transaction() as connection:
            connection.execute("""
                SELECT 1 FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND deleted_at IS NULL FOR SHARE
            """, scope).fetchone()
            if self._revision(connection, scope) != revision:
                return
            self.cache.put_sync(scope, revision, request_id, result)

    async def get_projection_result(self, tenant_id, user_id, conv_id, *, query="", current_request_id=""):
        # Coalesce the entire fenced read (including revision queries), never
        # persist a second cache of completed results. Cancellation of a caller
        # cannot free a slot while its shared thread/Redis request still runs.
        key = (tenant_id, user_id, conv_id, query, current_request_id)
        if self._callers >= self.max_callers:
            decisions.labels("context", "rejected").inc()
            return self._unavailable("CONTEXT_SOURCE_BUSY")
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= self.max_inflight:
                decisions.labels("context", "rejected").inc()
                return self._unavailable("CONTEXT_SOURCE_BUSY")
            task = asyncio.create_task(self._read(*key[:3], query=query,
                current_request_id=current_request_id))
            self._inflight[key] = task
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        else:
            decisions.labels("context", "coalesced").inc()
        self._callers += 1
        try:
            return deepcopy(await asyncio.shield(task))
        finally:
            self._callers -= 1

    async def _read(self, tenant_id, user_id, conv_id, *, query="", current_request_id=""):
        scope = (tenant_id, user_id, conv_id)
        for _ in range(2):
            try:
                revision = await asyncio.to_thread(self.revision, scope)
                if revision is None:
                    return self._unavailable("CONVERSATION_UNAVAILABLE")
                result = await self.cache.get(scope, revision, current_request_id) if self.cache else None
                hit = result is not None
                if not hit:
                    decisions.labels("context", "source_read").inc()
                    result = await self.reader.get_projection_result(*scope,
                        query=query, current_request_id=current_request_id)
                if await asyncio.to_thread(self.revision, scope) != revision:
                    continue
                if self.cache and not hit and not result.reason_codes and not result.conflicts:
                    await self.fill(scope, revision, current_request_id, result)
                    if await asyncio.to_thread(self.revision, scope) != revision:
                        continue
                return result
            except Exception as error:
                logger.warning("Conversation source revision unavailable: %s", type(error).__name__)
                return self._unavailable("CONTEXT_SOURCE_UNAVAILABLE")
        return self._unavailable("CONTEXT_SNAPSHOT_CHANGED")

    @staticmethod
    def _unavailable(reason):
        return MemoryProjectionResult(MemoryProjectionState.UNAVAILABLE,
            MemoryContext([], [], {}, "", []), 0, {}, MemoryRetrievalOutcome.UNAVAILABLE,
            reason_codes=(reason,))
