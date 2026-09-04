"""Current-thread context plus one bounded, on-demand ServiceEpisode lookup."""
from __future__ import annotations

import hashlib
import json

from application.deterministic_resolution import ResolutionKind
from application.target_conversation_manager import TargetTurnContext


_HISTORICAL_REFERENCES = (
    "之前那个", "之前的", "上次", "以前", "前几天", "历史上",
    "the previous one", "last time", "before",
)


class TargetTurnContextLoader:
    """Load mandatory current context; retrieve cross-session memory only on demand."""

    version = "target-turn-context-loader-v1"

    def __init__(self, memory, tool_manager, *, recent_limit: int = 8) -> None:
        self._memory = memory
        self._tools = tool_manager
        self._recent_limit = max(1, int(recent_limit))

    async def load(self, invocation, observations, state, deterministic):
        recent = []
        if self._memory is not None:
            try:
                current = await self._memory.get_current_context(
                    str(invocation.user_id), str(invocation.conversation_id),
                )
                if str(current.summary or "").strip():
                    recent.append(f"summary: {current.summary}")
                recent.extend(
                    f"{message.role.value}: {message.content}"
                    for message in current.recent_messages[-self._recent_limit:]
                    if str(message.content or "").strip()
                )
            except Exception:
                # Current-thread projections are rebuildable and non-authoritative.
                recent = []

        needs_history = (
            deterministic.kind is ResolutionKind.UNRESOLVED
            and any(
                token in observations.raw_text.casefold()
                for token in _HISTORICAL_REFERENCES
            )
        )
        if not needs_history:
            return TargetTurnContext(tuple(recent))

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
            return TargetTurnContext(
                tuple(recent), memory_attempted=True,
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
        return TargetTurnContext(
            tuple(recent), refs, evidence, True,
            str(data.get("purpose_outcome") or data.get("status") or "OK"),
        )
