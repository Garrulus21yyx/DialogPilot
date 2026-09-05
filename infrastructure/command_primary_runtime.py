"""Legacy structured test composition pending consumer migration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from application.always_defer_command_encoder import AlwaysDeferCommandEncoder
from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_argument_binding import ExplicitIdentifierArgumentBinder
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import command_primary_flow_registry
from application.route_decision import RouteMode
from application.route_policy_v2 import RoutePolicy
from application.selective_command_producer import SelectiveCommandProducer
from application.structured_command_producer import StructuredLLMCommandProducer
from application.turn_plan import TurnPlanCompiler
from application.turn_understanding import PendingSlotResolver
from core.model_policy import ModelProfile
from infrastructure.anthropic_command_completion import AnthropicCommandCompletion
from infrastructure.postgres_flow_state import PostgresFlowStateStore


def build_command_primary_chat_planner(
    config: Mapping[str, str],
    *,
    postgres_pool: Any = None,
    command_completion_client: Any = None,
    command_model_profile: ModelProfile | None = None,
) -> CommandPrimaryChatPlanner:
    mode = config.get("COMMAND_PRIMARY_MODE", "").strip().lower()
    if mode not in {"structured_knowledge_primary", "structured_read_only_primary"}:
        raise RuntimeError("legacy test composition requires an explicit structured mode")
    if command_completion_client is None or command_model_profile is None:
        raise RuntimeError("structured planning requires a completion client and model profile")
    semantic = SelectiveCommandProducer(
        AlwaysDeferCommandEncoder(),
        StructuredLLMCommandProducer(
            AnthropicCommandCompletion(command_completion_client, command_model_profile),
        ),
    )
    planner = CommandPrimaryPlanner(
        PendingSlotResolver(lambda _signal, _message: None),
        semantic,
        RoutePolicy(),
        TurnPlanCompiler(),
        ExplicitIdentifierArgumentBinder(),
    )
    primary_route_modes = {
        "structured_knowledge_primary": (
            RouteMode.KNOWLEDGE_QA,
            RouteMode.CLARIFY,
        ),
        "structured_read_only_primary": (
            RouteMode.KNOWLEDGE_QA,
            RouteMode.CLARIFY,
            RouteMode.AGENT_TASK,
            RouteMode.OUT_OF_SCOPE,
        ),
    }[mode]
    return CommandPrimaryChatPlanner(
        planner,
        command_primary_flow_registry,
        primary_route_modes=primary_route_modes,
        flow_state_store=(
            PostgresFlowStateStore(postgres_pool) if postgres_pool is not None else None
        ),
        authoritative=mode in {
            "structured_read_only_primary",
        },
    )
