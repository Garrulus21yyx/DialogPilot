"""Composition root for the Target conversation runtime.

This module owns wiring only. Conversation semantics, orchestration, persistence,
and delivery remain in their existing owners.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.framework_models import framework_model

from application.context_budget import ContextBudgetManager
from application.capability_registry import CapabilityRegistryBundle
from application.conversation_agent import ConversationAgent
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager, TurnUnderstanding
from infrastructure.target_domain_encoder import TargetDomainEncoder
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
from infrastructure.postgres_projection import PostgresConversationDeletionRepository
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
from infrastructure.conversation_tool_catalog import ConversationToolCatalog
from infrastructure.target_turn_context import TargetTurnContextLoader
from infrastructure.postgres_memory_projection import PostgresMemoryProjectionReader
from infrastructure.postgres_conversation_evidence import PostgresConversationEvidence
from infrastructure.conversation_observation_tool import install_observation_capability
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
    knowledge_verifier=None,
    knowledge_source_validator=None,
    knowledge_reuse_validator=None,
    registry: CapabilityRegistryBundle | None = None,
    enable_encoder: bool = True,
    response_locale: str | None = None,
    langfuse_sink=None,
) -> TargetRuntimeComponents:
    """Wire the one production Target runtime and enter its checkpoint owner."""
    registry = registry if registry is not None else build_default_capability_registry(
        os.getenv("DEFAULT_TENANT_ID", "default")
    )
    evidence_reader = PostgresConversationEvidence(postgres_pool, knowledge_reuse_validator)
    registry = install_observation_capability(registry, tool_manager, evidence_reader)
    checkpoint_owner = AsyncPostgresCheckpointOwner(database_url, setup=True,
        result_ttl_minutes=float(os.getenv("TARGET_RESULT_TTL_MINUTES", "43200")))
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
            tool_manager, registry=registry, control_guard=control_guard,
        )
        product_executor = TargetProductExecutor(
            tool_manager, control_guard=control_guard,
        )
        model = framework_model(worker_profile, provider_config)
        review_profile = model_policy.profile(ModelRole.VERIFIER)
        review_output_tokens = review_profile.request(max_tokens=4096)["max_tokens"]
        review_budget = ContextBudgetManager(
            context_window_tokens=min(review_profile.max_context_tokens,
                int(os.getenv("MODEL_CONTEXT_WINDOW_TOKENS", "16000"))),
            reserved_output_tokens=review_output_tokens,
            protocol_reserve_tokens=int(os.getenv("CONTEXT_PROTOCOL_RESERVE_TOKENS", "600")),
        )
        review_model = framework_model(review_profile, provider_config, max_tokens=review_output_tokens)
        domain_workers = {
            agent.agent_id: TargetFrameworkAgent(
                model,
                tool_manager,
                review_model=review_model,
                review_available_tokens=review_budget.available_tokens,
                result_store=checkpoint_owner.store,
                result_subject_fence=PostgresConversationDeletionRepository(postgres_pool).fence,
                registry=registry,
                system_prompt=agent.description,
                skill_executors={"product_identification": product_executor},
                context_budget=context_budget,
                control_guard=control_guard,
                callbacks=(langfuse_sink.callback(),) if langfuse_sink else (),
                trace_sink=langfuse_sink,
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
        encoder = _target_encoder(project_root, language=os.getenv(
            "TARGET_ENCODER_LANGUAGE", response_locale or os.getenv("TARGET_RESPONSE_LOCALE", "zh-CN")
        )) if enable_encoder else None
        conversation_agent = ConversationAgent(
            AnthropicConversationPlanningProvider(
                {role: framework_model(model_policy.profile(role), provider_config,
                                        max_tokens=conversation_output_tokens)
                 for role in (ModelRole.INTENT, ModelRole.SYNTHESIS)},
                model_profile=conversation_profile,
                synthesis_profile=model_policy.profile(ModelRole.SYNTHESIS),
                max_tokens=conversation_output_tokens,
                callbacks=(langfuse_sink.callback(),) if langfuse_sink else (),
            ),
            context_budget=conversation_context_budget,
            synthesis_context_budget=synthesis_context_budget,
            tool_catalog=ConversationToolCatalog(tool_manager),
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
                PostgresMemoryProjectionReader(postgres_pool, memory), tool_manager,
                evidence_reader=evidence_reader,
                historical_context_budget=ContextBudgetManager(
                    context_window_tokens=min(conversation_context_budget.available_tokens,
                                              context_budget.available_tokens,
                                              synthesis_context_budget.available_tokens,
                                              review_budget.available_tokens),
                    reserved_output_tokens=0, protocol_reserve_tokens=0)),
        )
        # Answer support is part of the assembled Target runtime, including
        # tool-only environments. Callers may inject a verifier, not omit it.
        if knowledge_verifier is None:
            knowledge_verifier = AnswerVerifier(
                model_client=review_model,
                model_profile=review_profile,
                callbacks=(langfuse_sink.callback(),) if langfuse_sink else (),
            )
        assembler = ResponseAssembler(conversation_agent,
                                      registry=registry,
                                      fallback_locale=(response_locale if response_locale is not None
                                                       else os.getenv("TARGET_RESPONSE_LOCALE", "zh-CN")),
                                      internal_tool_names=tool_manager.registered_tool_names,
                                      knowledge_verifier=knowledge_verifier,
                                      knowledge_source_validator=knowledge_source_validator,
                                      knowledge_reuse_validator=knowledge_reuse_validator,
                                      trace_sink=langfuse_sink)
        publication = PostgresTargetPublication(response_delivery)
        application = TargetChatApplication(
            manager=manager,
            admission=PostgresTargetAdmission(postgres_pool, durable=True),
            publication=publication,
            bundle_version=registry.bundle_version,
            knowledge_context_factory=knowledge_context_factory,
            response_assembler=assembler,
            turn_runtime=TurnRuntime(
                manager,
                assembler,
                callbacks=(langfuse_sink.callback(),) if langfuse_sink else (),
                checkpointer=checkpointer,
                interaction_published=publication.has_interaction,
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


def _target_encoder(project_root: Path, *, language: str | None = None) -> TargetEncoderUnderstanding | None:
    # Optional domain router. Old capability artifacts are not a fallback.
    enabled = os.getenv("TARGET_ENCODER_ENABLED", "false").strip().lower()
    if enabled not in {"true", "false"}:
        raise RuntimeError("TARGET_ENCODER_ENABLED must be true or false")
    if enabled == "false":
        return None
    language = (language or os.getenv("TARGET_ENCODER_LANGUAGE", "zh")).lower().split("-", 1)[0]
    if language not in {"zh", "en"}:
        raise RuntimeError("TARGET_ENCODER_LANGUAGE must select zh or en")
    artifact_dir = Path(os.getenv(
        "TARGET_ENCODER_ARTIFACT_DIR",
        str(project_root / "artifacts" / f"target-domain-encoder-{language}-v1"),
    ))
    artifact = TargetDomainEncoder(artifact_dir, device=os.getenv("TARGET_ENCODER_DEVICE", "cpu"))
    if artifact.manifest.language != language:
        raise RuntimeError("encoder artifact must be calibrated for the selected language")
    return TargetEncoderUnderstanding(artifact)
