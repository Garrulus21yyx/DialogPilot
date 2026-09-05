"""An isolated planning session using the production Target preparation path."""
from __future__ import annotations

from typing import Mapping

from application.capability_registry import CapabilityRegistryBundle
from application.chat_contracts import ChatCommand
from application.conversation_state import InMemoryConversationStateStore
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.target_conversation_manager import (
    TargetContextMessage, TargetContextProjectionStatus, TargetConversationManager,
    TargetTurnContext, TurnUnderstanding,
)
from application.turn_planning import TurnPlan
from core.identity import IdentityFactory


class TargetPlanningRunner:
    """One sequential dialog case; no tool execution or persistent business writes."""

    def __init__(
        self, *, registry: CapabilityRegistryBundle,
        understanding: TurnUnderstanding, orchestration: OrchestrationRuntime,
    ):
        self.registry = registry
        self._context = TargetTurnContext()
        self._manager = TargetConversationManager(
            state_store=InMemoryConversationStateStore(), registry=registry,
            understanding=understanding, orchestration=orchestration, context_provider=self,
        )

    async def load(self, invocation, observations, state, deterministic) -> TargetTurnContext:
        return self._context

    async def plan(
        self, command: ChatCommand, *, history: list[dict[str, str]],
        entities: Mapping[str, object],
    ) -> TurnPlan:
        self._context = TargetTurnContext(
            recent_messages=tuple(
                TargetContextMessage(
                    message["role"], message["content"], f"eval-history:{index}", index,
                ) for index, message in enumerate(history) if message["content"].strip()
            ),
            projection_status=TargetContextProjectionStatus.READY,
            projection_reason_codes=(),
        )
        identity = IdentityFactory().create_invocation(
            tenant_id=self.registry.tenant_id, user_id=command.user_id,
            conversation_id=command.conv_id, request_id=command.request_id,
        )
        prepared = await self._manager.prepare(
            identity, TurnObservations(command.message, tuple(entities.items())),
        )
        return prepared.plan
