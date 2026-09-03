"""Compose the selective command-primary migration seam."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import command_primary_flow_registry
from application.route_decision import RouteMode
from application.legacy_intent_command_adapter import LegacyIntentKnowledgeAdapter
from application.route_policy_v2 import RoutePolicy
from application.turn_plan import TurnPlanCompiler
from application.turn_understanding import PendingSlotResolver
from infrastructure.postgres_flow_state import PostgresFlowStateStore


def build_command_primary_chat_planner(
    orchestrator: Any,
    config: Mapping[str, str],
    *,
    postgres_pool: Any = None,
) -> CommandPrimaryChatPlanner | None:
    mode = config.get("COMMAND_PRIMARY_MODE", "off").strip().lower()
    if mode == "off":
        return None
    if mode not in {"shadow", "knowledge_primary"}:
        raise RuntimeError(
            "COMMAND_PRIMARY_MODE must be off, shadow, or knowledge_primary"
        )
    planner = CommandPrimaryPlanner(
        PendingSlotResolver(lambda _signal, _message: None),
        LegacyIntentKnowledgeAdapter(orchestrator.recognize_intent),
        RoutePolicy(),
        TurnPlanCompiler(),
    )
    return CommandPrimaryChatPlanner(
        planner,
        command_primary_flow_registry,
        primary_route_modes=(
            (RouteMode.KNOWLEDGE_QA,)
            if mode == "knowledge_primary" else ()
        ),
        flow_state_store=(
            PostgresFlowStateStore(postgres_pool)
            if postgres_pool is not None else None
        ),
    )
