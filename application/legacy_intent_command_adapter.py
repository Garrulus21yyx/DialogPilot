"""Temporary selective adapter from the legacy classifier to command proposals.

The adapter is intentionally narrow: it proposes the first read-only command
path and defers every other label to the unchanged legacy baseline.  Route,
requirements, risk, and tools are still owned by command-primary policies.
"""
from __future__ import annotations

from typing import Any, Mapping

from application.route_policy_v2 import FlowActionRegistry
from application.turn_state import TurnStateSnapshot
from application.turn_understanding import (
    CommandKind,
    CommandProposal,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
)
from core.intent_recognizer import IntentCategory


class LegacyIntentKnowledgeAdapter:
    def __init__(self, recognizer: Any) -> None:
        self._recognizer = recognizer

    async def understand(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        *,
        history: tuple[Mapping[str, str], ...] = (),
        bundle: Any = None,
    ) -> UnderstandingResult:
        del state, registry
        result = await self._recognizer(
            message,
            history=list(history) or None,
            bundle=bundle,
        )
        if result.intent is not IntentCategory.QUERY:
            return UnderstandingResult(
                UnderstandingStatus.DEFER,
                reason_code="LEGACY_BASELINE_REQUIRED",
            )
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            commands=(CommandProposal(
                kind=CommandKind.ANSWER_KNOWLEDGE,
                source=UnderstandingSource.LLM,
                message_fingerprint=message_fingerprint,
                producer_version="legacy-intent-knowledge-adapter-v1",
                evidence_refs=(f"message:{message_fingerprint}",),
            ),),
        )
