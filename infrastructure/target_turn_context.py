"""Current-thread context plus one bounded, on-demand ServiceEpisode lookup."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from application.deterministic_resolution import ResolutionKind
from application.target_conversation_manager import (
    TargetContextMessage,
    TargetContextProjectionStatus,
    TargetContextSummary,
    TargetTurnContext,
)


_HISTORICAL_REFERENCES = (
    "之前那个", "之前的", "上次", "以前", "前几天", "历史上",
    "the previous one", "last time", "before",
)


class TargetTurnContextLoader:
    """Load mandatory current context; retrieve cross-session memory only on demand."""

    version = "target-turn-context-loader-v2-historical-view"

    def __init__(self, projection_reader, tool_manager, *, recent_limit: int = 8,
                 evidence_reader=None, historical_context_budget=None) -> None:
        self._projection_reader = projection_reader
        self._tools = tool_manager
        self._recent_limit = max(1, int(recent_limit))
        self._evidence_reader = evidence_reader
        self._historical_context_budget = historical_context_budget

    async def load(self, invocation, observations, state, deterministic):
        recent = []
        summary = None
        status = TargetContextProjectionStatus.UNAVAILABLE
        reason_codes = ("CURRENT_CONTEXT_PROVIDER_MISSING",)
        watermark = 0
        if self._projection_reader is not None:
            try:
                projection = await self._projection_reader.get_projection_result(
                    str(invocation.tenant_id), str(invocation.user_id), str(invocation.conversation_id),
                    current_request_id=str(invocation.request_id),
                )
                current = projection.context
                watermark = projection.source_watermark
                if str(current.summary or "").strip():
                    content = str(current.summary).strip()
                    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    summary = TargetContextSummary(
                        content,
                        f"conversation-summary:{invocation.conversation_id}:{digest}",
                        # The legacy MemoryContext does not expose the summary's
                        # covered range.  Zero means unknown; do not infer it from
                        # the separate recent-message window.
                        0,
                    )
                for position, message in enumerate(
                    current.recent_messages[-self._recent_limit:]
                ):
                    content = str(getattr(message, "content", "") or "").strip()
                    if not content:
                        continue
                    role_value = getattr(getattr(message, "role", None), "value", None)
                    role = str(role_value or getattr(message, "role", ""))
                    seq = int(getattr(message, "seq", 0) or 0)
                    message_id = str(getattr(message, "message_id", "") or "")
                    identity = message_id or hashlib.sha256(
                        f"{position}:{role}:{seq}:{content}".encode("utf-8")
                    ).hexdigest()
                    observed = getattr(message, "timestamp", None)
                    recent.append(TargetContextMessage(
                        role,
                        content,
                        f"conversation-message:{invocation.conversation_id}:{identity}",
                        seq,
                        observed.isoformat() if hasattr(observed, "isoformat") else None,
                    ))
                status = (TargetContextProjectionStatus.READY if projection.state.value == "READY"
                          else TargetContextProjectionStatus.UNAVAILABLE if projection.state.value == "UNAVAILABLE"
                          else TargetContextProjectionStatus.DEGRADED)
                reason_codes = projection.reason_codes + (
                    ("CURRENT_CONTEXT_PROJECTION_" + projection.state.value,)
                    if status is not TargetContextProjectionStatus.READY else ())
            except Exception:
                # Current-thread projections are rebuildable and non-authoritative.
                recent = []
                summary = None
                status = TargetContextProjectionStatus.DEGRADED
                reason_codes = ("CURRENT_CONTEXT_READ_FAILED",)

        context = TargetTurnContext(
            tuple(recent),
            summary,
            projection_status=status,
            source_watermark=watermark,
            projection_reason_codes=reason_codes,
        )
        if self._evidence_reader is not None:
            import asyncio
            try:
                evidence = await asyncio.to_thread(self._evidence_reader.load, invocation)
                context = replace(context, knowledge_evidence=evidence.knowledge,
                                  business_observations=evidence.business)
            except Exception as exc:
                # Preserve conversation if the optional evidence read fails;
                # absence is not an instruction to rerun every prior lookup.
                context = replace(context,
                    projection_status=TargetContextProjectionStatus.DEGRADED,
                    projection_reason_codes=(*context.projection_reason_codes,
                        "EVIDENCE_CONTEXT_" + type(exc).__name__))

        # Host-side selection also covers deterministic input/approval resumes,
        # which correctly bypass the planning model. Model calls still enforce
        # their complete input budget after adding task-specific context.
        if self._historical_context_budget is not None and context.business_observations:
            from application.historical_context_budget import fit_historical_payload
            projected = fit_historical_payload(self._historical_context_budget,
                {'business_observations': list(context.business_observations)},
                observation_path=('business_observations',))
            context = replace(context, business_observations=tuple(projected.payload['business_observations']))

        needs_history = (
            deterministic.kind is ResolutionKind.UNRESOLVED
            and any(
                token in observations.raw_text.casefold()
                for token in _HISTORICAL_REFERENCES
            )
        )
        if not needs_history:
            return context

        call_hash = hashlib.sha256(
            f"{invocation.invocation_key}:memory-understanding".encode("utf-8")
        ).hexdigest()
        result = await self._tools.execute_for_agent(
            "service_episode_search",
            {
                "query": observations.raw_text,
                "purpose": "REFERENCE_RESOLUTION",
                "explicit_time_reference": True,
                "top_k": 3,
            },
            agent_type="general",
            context=invocation.metadata(),
            call_id=f"memory-understanding:{call_hash}",
            allowed_tool_ids=("service_episode_search",),
        )
        data = result.data if isinstance(result.data, dict) else {}
        retrieval_status = str(data.get("status") or "")
        if not result.success or retrieval_status not in {"OK", "AMBIGUOUS"}:
            return replace(
                context,
                memory_attempted=True,
                memory_status=(
                    retrieval_status
                    or str(result.status or "FAILED").upper()
                ),
            )
        hits = tuple(data.get("hits") or ())
        refs = tuple(dict.fromkeys(
            f"service-episode:{item.get('episode_id')}:{item.get('episode_revision')}:{item.get('provenance_sha256')}"
            for item in hits if isinstance(item, dict)
            and item.get("episode_id") and item.get("episode_revision")
            and item.get("provenance_sha256")
        ))
        evidence = (("memory.service_episode", json.loads(json.dumps(
            data, ensure_ascii=False, sort_keys=True,
        ))),)
        return replace(
            context,
            evidence_refs=refs,
            understanding_evidence=evidence,
            memory_attempted=True,
            memory_status=str(
                data.get("purpose_outcome") or data.get("status") or "OK"
            ),
        )
