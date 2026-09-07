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

from application.context_budget import ContextBudgetManager
from application.capability_registry import CapabilityRegistryBundle
from application.conversation_agent import ConversationAgent
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager, TurnUnderstanding
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
from infrastructure.postgres_memory_projection import PostgresMemoryProjectionReader
from infrastructure.target_workflow_execution import TargetWorkflowExecutor
from services.answer_verifier import AnswerVerifier


@dataclass(frozen=True)
class TargetRuntimeComponents:
    application: TargetChatApplication
    coordinator: TargetRunCoordinator
    run_store: PostgresTargetRunStore
    checkpoint_owner: AsyncPostgresCheckpointOwner
    orchestration: OrchestrationRuntime
    registry: CapabilityRegistryBundle
    understanding: TurnUnderstanding


def _framework_model(profile, provider_config):
    request = profile.request(max_tokens=1024, temperature=0)
    return ChatAnthropic(
        model_name=request["model"], api_key=provider_config["api_key"],
        base_url=provider_config.get("base_url"), max_tokens=request["max_tokens"],
        callbacks=provider_config.get("callbacks"),
        model_kwargs={key: value for key, value in request.items()
                      if key not in {"model", "max_tokens"}},
    )


async def build_target_runtime(
    *,
    database_url: str,
    postgres_pool: Any,
    tool_manager: Any,
    memory: Any,
    response_delivery: Any,
    model_policy: Any,
    provider_config: Mapping[str, Any],
    project_root: Path,
    knowledge_context_factory=None,
    knowledge_generator=None,
    knowledge_verifier=None,
    knowledge_source_validator=None,
    registry: CapabilityRegistryBundle | None = None,
    enable_encoder: bool = True,
) -> TargetRuntimeComponents:
    """Wire the one production Target runtime and enter its checkpoint owner."""
    registry = registry if registry is not None else build_default_capability_registry(
        os.getenv("DEFAULT_TENANT_ID", "default")
    )
    checkpoint_owner = AsyncPostgresCheckpointOwner(database_url, setup=True)
    checkpointer = await checkpoint_owner.__aenter__()
    try:
        worker_profile = model_policy.profile(ModelRole.WORKER)
        worker_request = worker_profile.request(max_tokens=1024, temperature=0)
        context_budget = ContextBudgetManager(
            context_window_tokens=min(worker_profile.max_context_tokens, int(os.getenv("MODEL_CONTEXT_WINDOW_TOKENS", "16000"))),
            reserved_output_tokens=max(worker_request["max_tokens"], int(os.getenv("CONVERSATION_OUTPUT_RESERVE_TOKENS", "1200"))),
            protocol_reserve_tokens=int(os.getenv("CONTEXT_PROTOCOL_RESERVE_TOKENS", "600")),
        )
        conversation_profile = model_policy.profile(ModelRole.INTENT)
        conversation_output_tokens = 800
        conversation_context_budget = ContextBudgetManager(
            context_window_tokens=min(conversation_profile.max_context_tokens, int(os.getenv(
                "MODEL_CONTEXT_WINDOW_TOKENS", "16000",
            ))),
            reserved_output_tokens=max(
                conversation_profile.request(max_tokens=conversation_output_tokens)["max_tokens"],
                int(os.getenv("CONVERSATION_OUTPUT_RESERVE_TOKENS", "1200")),
            ),
            protocol_reserve_tokens=int(os.getenv(
                "CONTEXT_PROTOCOL_RESERVE_TOKENS", "600",
            )),
        )
        synthesis_profile = model_policy.profile(ModelRole.SYNTHESIS)
        synthesis_context_budget = ContextBudgetManager(
            context_window_tokens=min(synthesis_profile.max_context_tokens, int(os.getenv(
                "MODEL_CONTEXT_WINDOW_TOKENS", "16000",
            ))),
            reserved_output_tokens=max(
                synthesis_profile.request(max_tokens=conversation_output_tokens)["max_tokens"],
                int(os.getenv("CONVERSATION_OUTPUT_RESERVE_TOKENS", "1200")),
            ),
            protocol_reserve_tokens=int(os.getenv(
                "CONTEXT_PROTOCOL_RESERVE_TOKENS", "600",
            )),
        )
        state_store = PostgresConversationStateStore(postgres_pool)
        control_guard = WorkControlGuard(state_store)
        tool_executor = TargetToolExecutor(
            tool_manager, control_guard=control_guard,
        )
        product_executor = TargetProductExecutor(
            tool_manager, control_guard=control_guard,
        )
        model = _framework_model(worker_profile, provider_config)
        domain_workers = {
            agent.agent_id: TargetFrameworkAgent(
                model,
                tool_manager,
                registry=registry,
                system_prompt=agent.description,
                callbacks=tuple(provider_config.get("callbacks") or ()),
                skill_executors={"product_identification": product_executor},
                context_budget=context_budget,
                control_guard=control_guard,
            )
            for agent in registry.agents
        }
        orchestration = OrchestrationRuntime(
            direct_executor=tool_executor,
            domain_workers=domain_workers,
            workflow_executor=TargetWorkflowExecutor(
                postgres_pool, tool_manager, registry=registry, control_guard=control_guard,
            ),
            evidence_resolver=TargetEvidenceResolver(registry, tool_executor),
            checkpointer=checkpointer,
            control_guard=control_guard,
        )
        encoder = _target_encoder(project_root) if enable_encoder else None
        conversation_agent = ConversationAgent(
            AnthropicConversationPlanningProvider(
                tool_manager.llm_client,
                model_profile=conversation_profile,
                synthesis_profile=model_policy.profile(ModelRole.SYNTHESIS),
                max_tokens=conversation_output_tokens,
            ),
            context_budget=conversation_context_budget,
            synthesis_context_budget=synthesis_context_budget,
        )
        understanding = CascadedTargetUnderstanding(
            StateBoundTargetUnderstanding(), conversation_agent, encoder=encoder,
        )
        manager = TargetConversationManager(
            state_store=state_store,
            registry=registry,
            understanding=understanding,
            orchestration=orchestration,
            context_provider=TargetTurnContextLoader(
                PostgresMemoryProjectionReader(postgres_pool, memory), tool_manager),
        )
        # Answer support is part of the assembled Target runtime, including
        # tool-only environments. Callers may inject a verifier, not omit it.
        if knowledge_verifier is None:
            knowledge_verifier = AnswerVerifier(
                client=tool_manager.llm_client,
                model_profile=model_policy.profile(ModelRole.VERIFIER),
            )
        assembler = ResponseAssembler(conversation_agent, knowledge_generator=knowledge_generator,
                                      knowledge_verifier=knowledge_verifier,
                                      knowledge_source_validator=knowledge_source_validator)
        application = TargetChatApplication(
            manager=manager,
            admission=PostgresTargetAdmission(postgres_pool, durable=True),
            publication=PostgresTargetPublication(response_delivery),
            bundle_version=registry.bundle_version,
            knowledge_context_factory=knowledge_context_factory,
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
            orchestration,
            registry,
            understanding,
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
