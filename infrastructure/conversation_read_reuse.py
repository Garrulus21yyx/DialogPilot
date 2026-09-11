"""Tool-owned read snapshots on the existing durable Store, not Agent memory.

Private working messages remain goal-scoped. Only explicitly reusable, successful
tool results are shared between authorized callers in the same conversation.
"""
from copy import deepcopy
from uuid import uuid4
import asyncio
import logging

from application.conversation_projection import ConversationSubject
from infrastructure.target_agent_result_adapter import framework_artifact, restore_framework_artifact
from infrastructure.target_result_archive import ResultArchiveError, TargetResultArchive
from mcp.read_reuse import ReadReusePolicy, read_key


class ConversationReadReuse:
    def __init__(self, store, registry, *, subject_fence=None, trace_sink=None):
        self.store, self.registry = store, registry
        self.subject_fence, self.trace_sink = subject_fence, trace_sink

    async def _namespace(self, context):
        scope = tuple(str(context.get(key) or "") for key in
                      ("tenant_id", "user_id", "conversation_id"))
        if not all(scope):
            raise ResultArchiveError("read reuse requires a bound conversation")
        if self.subject_fence is not None:
            subject = ConversationSubject(*scope)
            fence = await asyncio.to_thread(self.subject_fence, subject)
            if fence.deleted:
                await TargetResultArchive(self.store).delete_subject(subject)
                raise ResultArchiveError("conversation deleted")
        return ("target-originals", *scope, "shared-reads")

    async def _epoch(self, namespace):
        item = await self.store.aget(namespace, "epoch")
        return item.value["id"] if item else "initial"

    async def resolve_write(self, context, operation_key):
        """Called only by the governed reconciliation owner after receipt checks."""
        try:
            namespace = await self._namespace(context)
            await self.store.aput(namespace, "epoch", {"id": uuid4().hex}, index=False, ttl=None)
            while markers := await self.store.asearch((*namespace, "writers"),
                                                    filter={"operation_key": operation_key}, limit=100):
                for marker in markers:
                    await self.store.adelete(marker.namespace, marker.key)
            await self._namespace(context)
        except Exception as exc:
            self._maintenance_failure(exc, context)

    async def execute(self, tool, arguments, context, invoke, *, finalize, refresh=False):
        namespace = await self._namespace(context)
        if not tool.read_only:
            # Each writer owns its marker, so overlapping writers cannot clear
            # one another. A crashed/unknown write keeps reuse disabled, while
            # real reads and existing reconciliation remain available.
            marker = uuid4().hex
            await self.store.aput((*namespace, "writers"), marker,
                {"operation_key": context.get("business_operation_key", context["tool_call_id"])}, index=False, ttl=None)
            await self.store.aput(namespace, "epoch", {"id": uuid4().hex}, index=False, ttl=None)
            result = finalize(await invoke())
            try:
                await self.store.aput(namespace, "epoch", {"id": uuid4().hex}, index=False, ttl=None)
                if result.effect_status in {"committed", "not_committed"}:
                    await self.store.adelete((*namespace, "writers"), marker)
                await self._namespace(context)
            except Exception as exc:
                # The write receipt is authoritative even if cache maintenance
                # fails. The already-durable marker keeps old snapshots fenced.
                self._maintenance_failure(exc, context)
            return result
        if tool.task_read_reuse is None:
            return finalize(await invoke())

        limits = [requirement.freshness_seconds for requirement in self.registry.requirements
                  if tool.name in requirement.allowed_tools and requirement.freshness_seconds is not None]
        if tool.task_read_reuse.max_age_seconds is not None:
            limits.append(tool.task_read_reuse.max_age_seconds)
        policy = ReadReusePolicy(min(limits) if limits else None)
        # Principal is part of identity: handler output may be permission-dependent.
        key = read_key(tool, arguments, context, self.registry.fingerprint)
        epoch = await self._epoch(namespace)
        active = bool(await self.store.asearch((*namespace, "writers"), limit=1))
        saved = await self.store.aget((*namespace, "results"), key)
        record = saved.value if saved else None
        valid = (not active and not refresh and record is not None
                 and policy.valid(record, epoch=epoch))
        # A whole write may have started and finished during the lookup.
        valid = valid and epoch == await self._epoch(namespace)
        valid = valid and not await self.store.asearch((*namespace, "writers"), limit=1)
        await self._namespace(context)
        decision = ("REFRESH_REQUESTED" if refresh else "WRITE_UNRESOLVED" if active
                    else "NO_PRIOR_RESULT" if record is None
                    else "REUSE_VALID_RESULT" if valid else "PRIOR_RESULT_INVALIDATED")
        if self.trace_sink is not None:
            self.trace_sink.record_causal_event("READ_REUSE_DECIDED", owner="tool_read_reuse",
                turn_id=str(context.get("invocation_key") or ""),
                work_item_id=str(context.get("work_item_id") or ""),
                tool_call_id=context["tool_call_id"], emitted_business_call_id=context["tool_call_id"],
                read_identity=key, reuse_decision=decision, refresh_requested=refresh,
                prior_result_present=record is not None)
        if valid:
            result = deepcopy(restore_framework_artifact(record["artifact"]))
            result.cached = True
            tool.stats.total += 1
            tool.stats.success += 1
            return finalize(result)
        result = finalize(await invoke())
        await self._namespace(context)
        try:
            if (result.success and result.observed_at is not None and not active
                    and epoch == await self._epoch(namespace)
                    and not await self.store.asearch((*namespace, "writers"), limit=1)):
                await self.store.aput((*namespace, "results"), key, {
                    "observed_at": result.observed_at.isoformat(), "epoch": epoch,
                    "artifact": framework_artifact(result)}, index=False)
        except Exception as exc:
            self._maintenance_failure(exc, context)
        await self._namespace(context)
        return result

    def _maintenance_failure(self, exc, context):
        logging.getLogger(__name__).warning("Read snapshot maintenance unavailable: %s", type(exc).__name__)
        if self.trace_sink is not None:
            self.trace_sink.record_causal_event("READ_REUSE_UNAVAILABLE", owner="tool_read_reuse",
                turn_id=str(context.get("invocation_key") or ""),
                work_item_id=str(context.get("work_item_id") or ""),
                tool_call_id=str(context.get("tool_call_id") or ""), error_type=type(exc).__name__)
