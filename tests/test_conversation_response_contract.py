"""Conversation output is a first-class turn, never an implicit business action."""
import asyncio
from dataclasses import replace

import pytest
from itertools import product
from jsonschema import Draft202012Validator, ValidationError
from langgraph.checkpoint.memory import InMemorySaver

from application.conversation_agent import ConversationAgent, planning_output_schema
from application.conversation_state import InMemoryConversationStateStore
from application.deterministic_resolution import DeterministicResolver, ResolutionKind, TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import CommandKind, ProposalDisposition, RouteMode, RoutePolicy, TurnPlanCompiler, TurnPlanningError, TurnProposal
from application.turn_runtime import TurnRuntime
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_conversation_agent import Provider, _invoke
from tests.test_knowledge_answer_boundary import Verifier
from tests.test_parameter_acceptance import accepted_work
from tests.test_target_chat_cutover import _Admission, _Publication


class NativePublicProvider(Provider):
    async def plan(self, payload):
        from tests.test_conversation_actions import provider
        self.calls.append(payload)
        native, _ = provider(text=self.value['response'])
        return await native.plan(payload)


@pytest.mark.parametrize("text", [prefix + body + suffix for prefix, body, suffix in product(
    ("", " ", "\n"), ("You're welcome.", "不客气。", "🙂", "A\nB"), ("", "\t"))])
def test_response_algebra_and_checkpoint_roundtrip(text):
    raw = {"status": "respond", "response": text}
    Draft202012Validator(planning_output_schema()).validate(raw)
    proposal, state, registry = _invoke(ConversationAgent(Provider(raw)), "A conversational message")
    assert proposal.disposition is ProposalDisposition.RESPOND
    invocation = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="reply")
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry), state, registry, invocation)
    assert plan.route.mode is RouteMode.RESPONSE
    assert plan.work is plan.transitions is None and not plan.control_mutations
    serde = target_checkpoint_serializer()
    restored = serde.loads_typed(serde.dumps_typed(plan))
    assert restored == plan and restored.plan_id == plan.plan_id
    changed = replace(plan, response_text=text + "x")
    assert changed.plan_id != plan.plan_id


@pytest.mark.parametrize("extra", [{"goals": []}, {"approval_decision": {"approval_id": "a", "decision": "approve"}},
                                  {"missing_fields": ["order_id"]},
                                  {"input_values": [{"target_work_item_id": "w", "field_name": "reply", "value": "yes"}]}])
def test_response_cannot_smuggle_control_fields(extra):
    raw = {"status": "respond", "response": "Hello", **extra}
    with pytest.raises(ValidationError):
        Draft202012Validator(planning_output_schema()).validate(raw)
    assert _invoke(ConversationAgent(Provider(raw)), "Hello")[0].disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


@pytest.mark.parametrize("raw", [{"status": "resolved"}, {"status": "respond"},
    {"status": "respond", "response": "  "}, {"status": "respond", "response": 1},
    {"status": "out_of_scope", "response": "Hello"}])
def test_incomplete_or_mixed_output_still_fails(raw):
    with pytest.raises(ValidationError):
        Draft202012Validator(planning_output_schema()).validate(raw)


@pytest.mark.parametrize("kind", [CommandKind.DIRECT_TOOL, CommandKind.DELEGATE_TASK])
@pytest.mark.parametrize("correlated", [False, True])
def test_free_prose_is_not_a_typed_slot_submission(kind, correlated):
    state, _, item, _ = accepted_work(kind)
    binding = {"interaction_id": "input", "interaction_version": 1} if correlated else {}
    result = DeterministicResolver().resolve(TurnObservations("That makes sense", **binding), state)
    assert result.kind in {ResolutionKind.UNRESOLVED, ResolutionKind.REPLY_PENDING_INPUT}
    assert not result.fields
    typed = DeterministicResolver().resolve(TurnObservations("", interaction_id="input", interaction_version=1,
        interaction_values=((item.work_item_id, "reply", "selected option"),)), state)
    assert typed.kind is ResolutionKind.FILL_PENDING_INPUT


@pytest.mark.parametrize("passed", [False, True])
def test_planner_text_reuses_verification_without_a_second_author(passed):
    verifier = Verifier(passed)
    result = asyncio.run(ResponseAssembler(knowledge_verifier=verifier).assemble(
        None, current_message="Thanks", response_candidate="You are welcome.",
        conversation_context={"turn_contract": "RESPONSE_ONLY_NO_STATE_CHANGE"}))
    assert len(verifier.calls) == 1
    assert result.verified is passed
    assert (result.text == "You are welcome.") is passed


@pytest.mark.parametrize("waiting", ["none", "input", "approval"])
@pytest.mark.parametrize("kind", [CommandKind.DIRECT_TOOL, CommandKind.DELEGATE_TASK])
@pytest.mark.parametrize("provider_type", [Provider, NativePublicProvider])
def test_full_reply_preserves_wait_and_replays_without_model_or_work(waiting, kind, provider_type):
    from application.chat_contracts import ChatCommand, Completed
    async def run():
        state, registry, _, _ = accepted_work(kind)
        if waiting == "none":
            state = replace(state, pending_interaction=None)
        elif waiting == "approval":
            from tests.test_approval_revision_lifecycle import pending_state
            approval_state, _, _ = pending_state(False)
            state = replace(approval_state, tenant_id=state.tenant_id,
                            user_id=state.user_id, conversation_id=state.conversation_id)
        store = InMemoryConversationStateStore()
        store._states[("tenant-a", "user-a", "conversation-a")] = state
        provider = provider_type({"status": "respond", "response": "You are welcome."})
        async def forbidden(context):
            pytest.fail("response-only turn executed work")
        manager = TargetConversationManager(state_store=store, registry=registry,
            understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider)),
            orchestration=OrchestrationRuntime(direct_executor=forbidden, domain_workers={}))
        publication = _Publication()
        verifier = Verifier(True)
        assembler = ResponseAssembler(knowledge_verifier=verifier)
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        runtime = TurnRuntime(manager, assembler, checkpointer=saver, interaction_published=publication.has_interaction)
        app = TargetChatApplication(manager=manager, admission=_Admission(), publication=publication,
            bundle_version=registry.bundle_version, response_assembler=assembler, turn_runtime=runtime)
        command = ChatCommand("Thanks", "user-a", "tenant-a", "conversation-a", "reply",
            **({"interaction_id": "input", "interaction_version": 1} if waiting == "input" else {}))
        outcome = await app.handle(command)
        assert isinstance(outcome, Completed)
        assert outcome.response["response"] == "You are welcome."
        assert not outcome.response["task_completed"]
        assert outcome.response["evaluation_trace"]["cost"]["conversation_planner_invoked"]
        if waiting != "none":
            assert outcome.response["execution"] == "WAITING"
        assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state
        assert not getattr(publication, "interaction_payloads", ())
        assert await app.handle(command) == outcome
        assert len(provider.calls) == len(verifier.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("kind", [CommandKind.DIRECT_TOOL, CommandKind.DELEGATE_TASK])
@pytest.mark.parametrize("value", ["blue", "蓝色", 42, False])
def test_semantic_field_values_use_existing_binding_and_accepted_envelope(kind, value):
    state, registry, original, _ = accepted_work(kind)
    raw = {"status": "resolved", "input_values": [{"target_work_item_id": original.work_item_id,
        "field_name": "reply", "value": value}]}
    Draft202012Validator(planning_output_schema()).validate(raw)
    store = InMemoryConversationStateStore()
    store._states[("tenant-a", "user-a", "conversation-a")] = state
    manager = TargetConversationManager(state_store=store, registry=registry,
        understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(Provider(raw))),
        orchestration=OrchestrationRuntime(direct_executor=None, domain_workers={}))
    identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="field-reply")
    prepared = asyncio.run(manager.prepare(identity, TurnObservations(str(value),
        interaction_id="input", interaction_version=1)))
    assert prepared.state.pending_interaction is None
    assert prepared.resume_thread_id == "thread"
    item, = prepared.plan.work.items
    assert item.continuation_of == original.work_item_id
    assert item.control.control_id == original.control.control_id
    assert item.control.revision == original.control.revision + 1
    assert item.allowed_tools == original.allowed_tools
    assert {a.name: a.value for a in item.arguments}["reply"] == value
    assert all(binding in item.argument_bindings for binding in original.argument_bindings)
    bound = next(binding for binding in item.argument_bindings if binding.field_name == "reply")
    assert bound.value == value and bound.source.value == "PENDING_INTERACTION"
    assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state  # preparation is not commit


@pytest.mark.parametrize("provider_type", [Provider, NativePublicProvider])
def test_response_checkpoint_survives_postgres_reopen_before_publication(postgres_database_url, provider_type):
    from uuid import uuid4
    from application.chat_contracts import ChatCommand, Completed
    from application.default_capability_registry import build_default_capability_registry
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
    from infrastructure.postgres_target_runtime import PostgresConversationStateStore
    from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
    from infrastructure.target_chat_adapters import PostgresTargetAdmission, PostgresTargetPublication

    async def run():
        PostgresMigrationRunner(postgres_database_url).upgrade()
        provider = provider_type({"status": "respond", "response": "You are welcome."})
        verifier = Verifier(True)
        registry = build_default_capability_registry("tenant-a")
        conv = "response-" + uuid4().hex
        command = ChatCommand("Thanks", "user-a", "tenant-a", conv, "reply")
        identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
            conversation_id=conv, request_id="reply")
        async def forbidden(context):
            pytest.fail("response-only turn executed work")
        for phase in ("before_publication", "reopened", "replay"):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=True) as saver:
                    manager = TargetConversationManager(state_store=PostgresConversationStateStore(pool), registry=registry,
                        understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider)),
                        orchestration=OrchestrationRuntime(direct_executor=forbidden, domain_workers={}, checkpointer=saver))
                    publication = PostgresTargetPublication(PostgresResponseDeliveryService(pool, resume_binding_secret="test-secret"))
                    admission = PostgresTargetAdmission(pool)
                    assembler = ResponseAssembler(knowledge_verifier=verifier)
                    runtime = TurnRuntime(manager, assembler, checkpointer=saver, interaction_published=publication.has_interaction)
                    if phase == "before_publication":
                        admission.admit(command, identity, bundle_version=registry.bundle_version)
                        result = await runtime.execute(identity, TurnObservations(command.message))
                        assert result.assembled.verified
                    else:
                        app = TargetChatApplication(manager=manager, admission=admission, publication=publication,
                            bundle_version=registry.bundle_version, response_assembler=assembler, turn_runtime=runtime)
                        outcome = await app.handle(command)
                        assert isinstance(outcome, Completed), outcome
                        assert outcome.response["response"] == "You are welcome."
                        assert not outcome.response["task_completed"]
                    assert len(provider.calls) == len(verifier.calls) == 1
            finally:
                pool.close()
    asyncio.run(run())


def test_unpublished_implicit_response_cannot_bypass_new_public_channel():
    from application.chat_contracts import ChatCommand, Failed
    from application.default_capability_registry import build_default_capability_registry

    async def run():
        provider = Provider({'status': 'respond', 'response': 'I should ask the user. Hello.'})
        async def forbidden(_):
            pytest.fail('version rejection must not execute or retry business work')
        registry = build_default_capability_registry('tenant-a')
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(), registry=registry,
            understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider)),
            orchestration=OrchestrationRuntime(direct_executor=forbidden, domain_workers={}))
        publication = _Publication()
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)),
            checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
        identity = IdentityFactory().create_invocation(tenant_id='tenant-a', user_id='user-a',
            conversation_id='old-unpublished', request_id='one')
        await runtime.execute(identity, TurnObservations('Hi'))
        await runtime.graph.aupdate_state({'configurable': {'thread_id': 'turn:' + str(identity.invocation_key)}},
            {'runtime_version': 'turn-runtime-v18-implicit-current-action'})
        app = TargetChatApplication(manager=manager, admission=_Admission(), publication=publication,
            bundle_version=registry.bundle_version, turn_runtime=runtime)
        result = await app.handle(ChatCommand('Hi', 'user-a', 'tenant-a', 'old-unpublished', 'one'))
        assert isinstance(result, Failed)
        assert result.code == 'turn_checkpoint_version_unsupported'
        assert len(provider.calls) == 1
        assert publication.responses == {}
    asyncio.run(run())


@pytest.mark.parametrize("approval", [None, "approve", "decline"])
@pytest.mark.parametrize("additional_goal", [False, True])
def test_semantic_input_composes_with_independent_goal_and_approval(approval, additional_goal):
    from application.conversation_state import PendingApprovalState, WorkstreamState, WorkstreamStatus
    from application.entity_binding import BindingSource, EntityBinding
    from application.work_item import ArgumentValue
    state, registry, original, _ = accepted_work(CommandKind.DIRECT_TOOL)
    if approval:
        stream = WorkstreamState("refund", "billing_refund", "execute_refund:v1", "PREPARED",
            WorkstreamStatus.ACTIVE, 1, flow_ref="execute_refund:v1",
            slots=(ArgumentValue.create("order_id", "DP2222"),))
        state = state.start_workstream(stream)
        binding = EntityBinding.create("order_id", "DP2222", source=BindingSource.WORKSTREAM_SLOT,
            source_ref="workstream:refund:slot:order_id", source_version=1, workstream_id="refund",
            tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a", priority=400)
        state = state.wait_for_approval(PendingApprovalState("approval", 1, "refund", "prepare-refund",
            "refund.request.create:v1", "operation-refund", "order:DP2222", "7", "2099-01-01T00:00:00+00:00",
            (ArgumentValue.create("order_id", "DP2222"), ArgumentValue.create("reason", "requested"),
             ArgumentValue.create("expected_order_version", 7)), checkpoint_thread_id="refund-thread",
            argument_bindings=(binding,)))
    raw = {"status": "resolved", "input_values": [{"target_work_item_id": original.work_item_id,
        "field_name": "reply", "value": "blue"}]}
    if approval:
        raw["approval_decision"] = {"approval_id": "approval", "decision": approval}
    if additional_goal:
        raw["goals"] = [{"kind": "delegate_task", "target_agent": "product_technical",
                         "objective": "Explain the product options", "allow_action_proposals": False}]
    store = InMemoryConversationStateStore()
    store._states[("tenant-a", "user-a", "conversation-a")] = state
    manager = TargetConversationManager(state_store=store, registry=registry,
        understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(Provider(raw))),
        orchestration=OrchestrationRuntime(direct_executor=None, domain_workers={}))
    identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="combined")
    prepared = asyncio.run(manager.prepare(identity, TurnObservations("Use blue; handle the other requests too")))
    assert prepared.state.pending_interaction is None
    assert prepared.state.pending_approval is None
    resumed = next(item for item in prepared.plan.work.items if item.continuation_of == original.work_item_id)
    assert {a.name: a.value for a in resumed.arguments}["reply"] == "blue"
    assert len(prepared.plan.work.items) == 1 + additional_goal + (approval == "approve")
    assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state
