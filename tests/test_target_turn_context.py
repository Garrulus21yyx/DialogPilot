import asyncio
from types import SimpleNamespace

from application.conversation_state import (
    ConversationState,
    InMemoryConversationStateStore,
)
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.target_conversation_manager import (
    TargetConversationManager,
    TargetTurnContext,
)
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    RoutePolicy,
    TurnPlanCompiler,
    TurnProposal,
)
from core.identity import IdentityFactory
from infrastructure.target_turn_context import TargetTurnContextLoader
from mcp.tool_manager import ToolCallStatus, ToolResult


def _identity():
    return IdentityFactory(lambda: "fixed").create_invocation(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conversation-a",
        request_id="request-a",
    )


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


class Memory:
    def __init__(self):
        self.calls = []

    async def get_current_context(self, user_id, conversation_id):
        self.calls.append((user_id, conversation_id))
        return SimpleNamespace(
            summary="用户正在处理售后问题",
            recent_messages=[SimpleNamespace(
                role=SimpleNamespace(value="user"), content="上轮消息",
            )],
        )


class Tools:
    def __init__(self):
        self.calls = []

    async def execute_for_agent(self, name, params, **kwargs):
        self.calls.append((name, params, kwargs))
        return ToolResult(
            True,
            {
                "status": "OK",
                "purpose_outcome": "UNIQUE_BINDING",
                "hits": [{
                    "episode_id": "episode-1",
                    "episode_revision": "3",
                    "provenance_sha256": "a" * 64,
                }],
            },
            name,
            call_id=kwargs["call_id"],
            status=ToolCallStatus.SUCCESS.value,
            authority="memory.service_episode",
        )


def test_current_thread_context_is_loaded_without_prefetching_cross_session_memory():
    memory = Memory()
    tools = Tools()
    observations = TurnObservations("查询订单 DP1234")
    state = _state()
    deterministic = DeterministicResolver().resolve(observations, state)

    context = asyncio.run(TargetTurnContextLoader(memory, tools).load(
        _identity(), observations, state, deterministic,
    ))

    assert memory.calls == [("user-a", "conversation-a")]
    assert context.recent_relevant_turns == (
        "summary: 用户正在处理售后问题", "user: 上轮消息",
    )
    assert context.memory_attempted is False
    assert tools.calls == []


def test_historical_reference_triggers_exactly_one_scoped_memory_lookup():
    memory = Memory()
    tools = Tools()
    observations = TurnObservations("继续处理上次那个问题")
    state = _state()
    deterministic = DeterministicResolver().resolve(observations, state)

    context = asyncio.run(TargetTurnContextLoader(memory, tools).load(
        _identity(), observations, state, deterministic,
    ))

    assert len(tools.calls) == 1
    name, params, kwargs = tools.calls[0]
    assert name == "service_episode_search"
    assert params["purpose"] == "REFERENCE_RESOLUTION"
    assert kwargs["allowed_tool_ids"] == ("service_episode_search",)
    assert kwargs["context"]["tenant_id"] == "tenant-a"
    assert kwargs["context"]["user_id"] == "user-a"
    assert context.memory_attempted is True
    assert context.memory_status == "UNIQUE_BINDING"
    assert context.evidence_refs == (
        f"service-episode:episode-1:3:{'a' * 64}",
    )
    assert context.understanding_evidence[0][0] == "memory.service_episode"


def test_conversation_manager_loads_context_before_understanding_once():
    calls = []

    class ContextProvider:
        async def load(self, invocation, observations, state, deterministic):
            calls.append(("context", deterministic.kind.value))
            return TargetTurnContext(
                ("user: earlier turn",),
                ("episode:e1",),
                (("memory.service_episode", {"episode_id": "e1"}),),
                True,
                "UNIQUE_BINDING",
            )

    class Understanding:
        async def __call__(self, observations, *_args):
            calls.append(("understanding", observations.understanding_evidence))
            return TurnProposal(
                ProposalDisposition.CLARIFY, (), "NEEDS_GOAL", ("customer_service_goal",),
            )

    async def unused(_context):
        raise AssertionError("terminal clarification must not execute work")

    manager = TargetConversationManager(
        state_store=InMemoryConversationStateStore(),
        registry=build_default_capability_registry("tenant-a"),
        understanding=Understanding(),
        orchestration=OrchestrationRuntime(
            direct_executor=unused, domain_workers={},
        ),
        context_provider=ContextProvider(),
    )

    asyncio.run(manager.handle(_identity(), TurnObservations("之前那个")))

    assert calls == [
        ("context", "UNRESOLVED"),
        ("understanding", (("memory.service_episode", {"episode_id": "e1"}),)),
    ]


def test_target_registry_exposes_existing_memory_tool_without_a_memory_skill():
    registry = build_default_capability_registry("tenant-a")

    assert registry.tool("service_episode_search").authority == "memory.service_episode"
    assert "service_episode_search" in registry.agent("billing_refund").allowed_tool_ids
    assert all("memory" not in skill.skill_id for skill in registry.skills)


def test_delegated_tool_envelope_is_derived_from_requirements_not_whole_domain():
    registry = build_default_capability_registry("tenant-a")
    state = _state()
    proposal = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "history-1",
            CommandKind.DELEGATE_TASK,
            "general",
            "Interpret one verified historical service episode",
            requirement_ids=("memory.service_episode",),
        ),),
        "HISTORICAL_EVIDENCE_REQUIRED",
    )

    plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(proposal, state, registry),
        state,
        registry,
        _identity(),
    )

    assert plan.work.items[0].allowed_tools == ("service_episode_search",)
