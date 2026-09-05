"""Composition root for the Target conversation runtime.

This module owns wiring only. Conversation semantics, orchestration, persistence,
and delivery remain in their existing owners.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from langchain_anthropic import ChatAnthropic

from agents.orchestration_contracts import AgentType
from application.context_budget import ContextBudgetManager
from application.conversation_agent import ConversationAgent
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_encoder_artifact import load_target_text_encoder_artifact
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.target_run import TargetRunCoordinator
from application.target_understanding import (
    CascadedTargetUnderstanding,
    StateBoundTargetUnderstanding,
)
from application.turn_runtime import TurnRuntime
from application.work_control import WorkControlGuard
from core.model_policy import ModelRole
from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
from infrastructure.postgres_admission import PostgresStartOutbox, StartOutboxDispatcher
from infrastructure.postgres_conversation import PostgresInvocationRepository
from infrastructure.postgres_target_run import (
    PostgresTargetRunBinder,
    PostgresTargetRunStore,
)
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from infrastructure.target_agent_execution import TargetAgentExecutor
from infrastructure.target_chat_adapters import (
    PostgresTargetAdmission,
    PostgresTargetPublication,
)
from infrastructure.target_conversation_provider import (
    AnthropicConversationPlanningProvider,
)
from infrastructure.target_evidence_resolution import TargetEvidenceResolver
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.target_product_execution import TargetProductExecutor
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.target_turn_context import TargetTurnContextLoader
from infrastructure.target_workflow_execution import TargetWorkflowExecutor


@dataclass(frozen=True)
class TargetRuntimeComponents:
    application: TargetChatApplication
    coordinator: TargetRunCoordinator
    run_store: PostgresTargetRunStore
    checkpoint_owner: AsyncPostgresCheckpointOwner


async def build_target_runtime(
    *,
    database_url: str,
    postgres_pool: Any,
    tool_manager: Any,
    legacy_orchestrator: Any,
    memory: Any,
    response_delivery: Any,
    model_policy: Any,
    provider_config: Mapping[str, Any],
    project_root: Path,
) -> TargetRuntimeComponents:
    """Wire the one production Target runtime and enter its checkpoint owner."""
    registry = build_default_capability_registry(
        os.getenv("DEFAULT_TENANT_ID", "default")
    )
    checkpoint_owner = AsyncPostgresCheckpointOwner(database_url, setup=True)
    checkpointer = await checkpoint_owner.__aenter__()
    try:
        context_budget = ContextBudgetManager(
            context_window_tokens=int(os.getenv(
                "MODEL_CONTEXT_WINDOW_TOKENS", "16000",
            )),
            reserved_output_tokens=int(os.getenv(
                "CONVERSATION_OUTPUT_RESERVE_TOKENS", "1200",
            )),
            protocol_reserve_tokens=int(os.getenv(
                "CONTEXT_PROTOCOL_RESERVE_TOKENS", "600",
            )),
        )
        state_store = PostgresConversationStateStore(postgres_pool)
        control_guard = WorkControlGuard(state_store)
        tool_executor = TargetToolExecutor(
            tool_manager, control_guard=control_guard,
        )
        product_executor = TargetProductExecutor(tool_manager)
        domain_executor = TargetAgentExecutor(
            {
                agent_type: legacy_orchestrator.worker_for(agent_type)
                for agent_type in (
                    AgentType.GENERAL,
                    AgentType.BILLING,
                    AgentType.ACCOUNT_SECURITY,
                    AgentType.ESCALATION,
                )
            },
            registry=registry,
            context_budget=context_budget,
            control_guard=control_guard,
        )
        product_agent = TargetFrameworkAgent(
            ChatAnthropic(
                model_name=model_policy.profile(ModelRole.WORKER).model,
                api_key=provider_config["api_key"],
                base_url=provider_config.get("base_url"),
                max_tokens=1024,
                temperature=0,
            ),
            tool_manager,
            registry=registry,
            system_prompt=(
                "You are the product specialist for a general ecommerce service. "
                "Use catalog, media and knowledge evidence to resolve the supplied "
                "product objective; do not assume a product category."
            ),
            skill_executors={"product_identification": product_executor},
            context_budget=context_budget,
            checkpointer=checkpointer,
            control_guard=control_guard,
        )
        orchestration = OrchestrationRuntime(
            direct_executor=tool_executor,
            domain_workers={
                "general": domain_executor,
                "product_technical": product_agent,
                "order_logistics": domain_executor,
                "billing_refund": domain_executor,
                "account_security": domain_executor,
                "human_service": domain_executor,
            },
            workflow_executor=TargetWorkflowExecutor(
                postgres_pool, tool_manager, control_guard=control_guard,
            ),
            evidence_resolver=TargetEvidenceResolver(registry, tool_executor),
            checkpointer=checkpointer,
            control_guard=control_guard,
        )
        encoder = _target_encoder(project_root)
        conversation_agent = ConversationAgent(
            AnthropicConversationPlanningProvider(
                tool_manager.llm_client,
                model=model_policy.profile(ModelRole.INTENT).model,
            ),
            context_budget=context_budget,
        )
        manager = TargetConversationManager(
            state_store=state_store,
            registry=registry,
            understanding=CascadedTargetUnderstanding(
                StateBoundTargetUnderstanding(),
                conversation_agent,
                encoder=encoder,
            ),
            orchestration=orchestration,
            context_provider=TargetTurnContextLoader(memory, tool_manager),
        )
        assembler = ResponseAssembler(conversation_agent)
        application = TargetChatApplication(
            manager=manager,
            admission=PostgresTargetAdmission(postgres_pool, durable=True),
            publication=PostgresTargetPublication(response_delivery),
            bundle_version=registry.bundle_version,
            response_assembler=assembler,
            turn_runtime=TurnRuntime(
                manager,
                assembler,
                checkpointer=checkpointer,
            ),
        )
        run_store = PostgresTargetRunStore(postgres_pool)
        coordinator = TargetRunCoordinator(
            application,
            dispatcher=StartOutboxDispatcher(
                PostgresStartOutbox(postgres_pool),
                PostgresInvocationRepository(postgres_pool),
                PostgresTargetRunBinder(postgres_pool),
            ),
            store=run_store,
            worker_id=os.getenv("DIALOGPILOT_TARGET_RUN_WORKER_ID", "api-target"),
            start_lease_seconds=int(os.getenv(
                "DIALOGPILOT_START_LEASE_SECONDS", "30",
            )),
            execution_lease_seconds=int(os.getenv(
                "DIALOGPILOT_EXECUTION_LEASE_SECONDS", "300",
            )),
            heartbeat_seconds=float(os.getenv(
                "DIALOGPILOT_EXECUTION_HEARTBEAT_SECONDS", "30",
            )),
        )
        return TargetRuntimeComponents(
            application,
            coordinator,
            run_store,
            checkpoint_owner,
        )
    except BaseException:
        await checkpoint_owner.__aexit__(None, None, None)
        raise


def _target_encoder(project_root: Path) -> TargetEncoderUnderstanding | None:
    enabled = os.getenv("TARGET_ENCODER_ENABLED", "true").strip().lower()
    if enabled not in {"true", "false"}:
        raise RuntimeError("TARGET_ENCODER_ENABLED must be true or false")
    if enabled == "false":
        return None
    artifact_dir = Path(os.getenv(
        "TARGET_ENCODER_ARTIFACT_DIR",
        str(project_root / "artifacts" / "target-encoder-zh-v2"),
    ))
    return TargetEncoderUnderstanding(
        load_target_text_encoder_artifact(artifact_dir)
    )
