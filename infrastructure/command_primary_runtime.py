"""Compose the selective command-primary migration seam."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import knowledge_flow_registry
from application.legacy_intent_command_adapter import LegacyIntentKnowledgeAdapter
from application.route_policy_v2 import RoutePolicy
from application.turn_plan import TurnPlanCompiler
from application.turn_understanding import PendingSlotResolver


def build_command_primary_chat_planner(
    orchestrator: Any,
    config: Mapping[str, str],
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
        knowledge_flow_registry,
        knowledge_primary=mode == "knowledge_primary",
    )
