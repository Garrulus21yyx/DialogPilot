"""Main-agent observations preserve the original request and completed work."""
import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.conversation_context import conversation_context_payload
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.target_conversation_manager import TargetConversationManager
from application.target_understanding import CascadedTargetUnderstanding
from application.turn_planning import PlanningInvariantError, ProposalDisposition, TurnProposal
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_turn_runtime import _Executor, _OrderUnderstanding, _identity


def test_observation_cancel_retains_evidence_without_active_execution_authority(monkeypatch):
    """A current cancellation reply preserves facts without reauthorizing work."""
    from types import SimpleNamespace
    from application.turn_planning import CommandKind, CommandProposal
    from infrastructure.postgres_publication import PostgresPublicationService, PublicationConflictError

    async def scenario():
        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                if context.observed_execution is None:
                    base = await _OrderUnderstanding()()
                    return replace(base, commands=(replace(base.commands[0], observe_result=True),))
                control = state.active_work_controls[0]
                return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
                    "stop-read", CommandKind.CANCEL_WORK, control.owner_agent,
                    "Stop the completed lookup", revises_control_id=control.control_id,
                ),), "STOP_READ")

        store = InMemoryConversationStateStore()
        executor = _Executor()
        manager = TargetConversationManager(state_store=store,
            registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={}))
        identity = _identity()
        prepared = await manager.prepare(identity, TurnObservations("Check my order"))
        read = await manager.execute(prepared)
        await manager.commit_progress(read)
        read = await manager.resolve_followup(prepared, read)
        await manager.commit(read)
        following = await manager.prepare_observation(prepared, read)
        cancelled = await manager.execute(following)
        await manager.commit_progress(cancelled)
        cancelled = await manager.resolve_followup(following, cancelled)
        await manager.commit(cancelled)
        assert executor.calls == 1
        assert cancelled.board.facts == read.board.facts
        bindings = tuple(item.control for item, _ in cancelled.board.outcome_items)
        assert bindings and all(not cancelled.state_after.accepts(item) for item in bindings)

        # The existing publication consumer derives authorization from exactly
        # these retained outcomes. Its SQL reader is supplied the actual state.
        monkeypatch.setattr("infrastructure.postgres_target_runtime.load_conversation_state",
                            lambda *_args: cancelled.state_after)
        command = SimpleNamespace(expected_state_fingerprint=cancelled.state_after.fingerprint,
            tenant_id=identity.tenant_id, user_id=identity.user_id,
            conversation_id=identity.conversation_id)
        PostgresPublicationService._assert_reply_state(None, command)
        command.expected_state_fingerprint = read.state_after.fingerprint
        with pytest.raises(PublicationConflictError, match="conversation state is stale"):
            PostgresPublicationService._assert_reply_state(None, command)

    asyncio.run(scenario())


@pytest.mark.parametrize("recover", [False, True])
def test_main_observation_progress_feedback_stop_and_replay(recover):
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from tests.test_knowledge_answer_boundary import Verifier

    async def run():
        seen = []

        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                seen.append(context.observation_feedback)
                if context.observation_feedback and recover:
                    return TurnProposal(ProposalDisposition.RESPOND, (), "DONE",
                                        response_text="The order shipped.")
                base = await _OrderUnderstanding()()
                return replace(base, commands=(replace(base.commands[0], observe_result=True),))

        saver = InMemorySaver(serde=target_checkpoint_serializer())
        executor = _Executor()
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={}, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)),
                              checkpointer=saver, max_observation_steps=8)
        identity, message = _identity(), TurnObservations("Check my order and help me")
        result = await runtime.execute(identity, message)
        assert executor.calls == (3 if recover else 4)
        assert sum(bool(feedback) for feedback in seen) == 1
        assert result.managed.board.facts
        assert result.managed.request_completed is recover
        if not recover:
            assert result.managed.diagnostics[-1].detail["code"] == "OBSERVATION_NO_PROGRESS"
        calls = executor.calls
        replay = await runtime.execute(identity, message)
        assert executor.calls == calls
        assert replay.managed.request_completed is recover
        from application.execution_progress import direct_read_observations
        board = result.managed.board
        original = direct_read_observations(board)
        # Adapter-created subjects can contain call IDs; those are not novelty.
        changed = replace(board, results=tuple(replace(r, facts=tuple(
            replace(f, subject_ref="tool-observation:new-call-id", source_ref="new-call-id")
            for f in r.facts)) for r in board.results))
        assert direct_read_observations(changed) == original
        from application.agent_result import AgentResultStatus
        for status in (AgentResultStatus.BLOCKED, AgentResultStatus.CANCELLED,
                       AgentResultStatus.SUPERSEDED, AgentResultStatus.WAITING_APPROVAL):
            unexecuted = replace(board, results=tuple(replace(r, status=status, facts=(),
                reason_code="SCHEDULER:new-work-id") for r in board.results))
            assert direct_read_observations(unexecuted) == []
        import json
        from application.work_item import ArgumentValue
        from tests.test_knowledge_tool_contract import evidence_result
        knowledge_keys = None
        for index in range(3):
            data = evidence_result()
            data["evidence_pack"]["query"] = f"rephrased {index}"
            data["diagnostics"] = {"duration": index}
            data["evidence_pack"]["items"].reverse()
            knowledge = replace(board, work_items=tuple(replace(i,
                arguments=(ArgumentValue.create("query", f"rephrased {index}"),)) for i in board.work_items),
                results=tuple(replace(r, facts=tuple(replace(f,
                    requirement_id="knowledge.active_source", value_json=json.dumps(data, sort_keys=True,
                        ensure_ascii=False, separators=(",", ":")))
                    for f in r.facts)) for r in board.results))
            keys = direct_read_observations(knowledge)
            assert keys
            if knowledge_keys is not None:
                assert keys == knowledge_keys
            knowledge_keys = keys
        from core.identity import IdentityFactory
        fresh = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="fresh-request")
        start = len(seen)
        await runtime.execute(fresh, message)
        assert seen[start] == ""
        assert executor.calls == calls * 2
    asyncio.run(run())


@pytest.mark.parametrize("status", ["SUCCEEDED", "TERMINAL_FAILURE", "RETRYABLE_FAILURE", None])
@pytest.mark.parametrize("delegated", [False, True])
def test_observation_pairs_task_input_with_outcome_across_checkpoint(status, delegated):
    from application.agent_result import AgentResult, AgentResultStatus
    from application.work_item import ArgumentValue, ControlMode

    async def scenario():
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=_OrderUnderstanding(),
            orchestration=OrchestrationRuntime(direct_executor=_Executor(), domain_workers={}))
        prepared = await manager.prepare(_identity(), TurnObservations("Check my order"))
        executed = await manager.execute(prepared)
        item = replace(prepared.plan.work.items[0],
            control_mode=ControlMode.DELEGATED if delegated else ControlMode.DIRECT,
            arguments=(ArgumentValue.create("query", ""),
                       ArgumentValue.create("filter", {"ids": ["a", "b"], "active": False})))
        feedback = {"stage": "tool", "tool": "order_lookup", "error": "Not found"}
        result = (AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus(status),
            "TEST_OUTCOME", "v1", retryable=status == "RETRYABLE_FAILURE",
            execution_feedback=(feedback,)) if status else None)
        board = replace(executed.board, work_items=(), results=(), facts=(),
                        retained_outcomes=((item, result),))
        context = replace(prepared.context, observed_execution=board)
        serde = target_checkpoint_serializer()
        restored = serde.loads_typed(serde.dumps_typed(context))
        projection = conversation_context_payload(restored)
        assert projection == conversation_context_payload(context)
        from infrastructure.target_model_context import planning_context, planning_payload_from_request
        model_input = {"message": "Check my order", "conversation_context": projection}
        system, messages = planning_context(model_input)
        assert planning_payload_from_request({"system": system, "messages": [
            {"role": message.type, "content": message.content} for message in messages
        ]}) == model_input
        outcome, = projection["observed_execution"]["outcomes"]
        assert outcome["task_input"]["arguments"] == {
            "query": "", "filter": {"ids": ["a", "b"], "active": False}}
        assert outcome["status"] == (status or "NOT_EXECUTED")
        assert outcome["execution_feedback"] == ([feedback] if status else [])
        assert ("tool" in outcome["task_input"]) is not delegated
        if not delegated:
            assert outcome["task_input"]["tool"] == "order_lookup"
        # The model-facing projection must not expose execution authority.
        assert "approval_binding" not in outcome["task_input"]
        assert "allowed_tools" not in outcome["task_input"]
        outcome["task_input"]["arguments"]["filter"]["ids"].append("changed")
        assert item.arguments[1].value["ids"] == ["a", "b"]

    asyncio.run(scenario())


@pytest.mark.parametrize("observe", [False, True])
def test_observation_obligation_roundtrips_and_changes_work_identity(observe):
    from infrastructure.postgres_target_runtime import _work_item_from_payload, _work_item_to_payload
    from application.target_understanding import StateBoundTargetUnderstanding
    from application.work_item import WorkItemContractError

    async def scenario():
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=_OrderUnderstanding(),
            orchestration=OrchestrationRuntime(direct_executor=_Executor(), domain_workers={}))
        prepared = await manager.prepare(_identity(), TurnObservations("Check order DP1234"))
        base = prepared.plan.work.items[0]
        item = replace(base, observe_result=observe)
        assert item.fingerprint != replace(item, observe_result=not observe).fingerprint
        payload = _work_item_to_payload(item)
        restored = _work_item_from_payload(payload)
        codec = target_checkpoint_serializer()
        checkpointed = codec.loads_typed(codec.dumps_typed(restored))
        assert checkpointed == item
        assert StateBoundTargetUnderstanding._resume_command(1, checkpointed).observe_result is observe
        for invalid in (None, "true", 1):
            with pytest.raises(WorkItemContractError, match="observation"):
                _work_item_from_payload({**payload, "observe_result": invalid})
        del payload["observe_result"]
        with pytest.raises(ValueError, match="migration"):
            _work_item_from_payload(payload)
    asyncio.run(scenario())


def test_read_observe_read_respond_preserves_request_results_and_step_scope():
    async def scenario():
        seen = []

        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                seen.append((observations, context))
                if len(seen) == 3:
                    return TurnProposal(ProposalDisposition.RESPOND, (), "ANSWER", response_text="Both orders shipped.")
                base = await _OrderUnderstanding()()
                return replace(base, commands=(replace(base.commands[0], observe_result=True),))

        executor = _Executor()
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={},
                checkpointer=InMemorySaver(serde=target_checkpoint_serializer())))
        message = TurnObservations("Check the shipment, then help with my original request")
        prepared = await manager.prepare(_identity(), message)
        steps = []
        for _ in range(3):
            result = await manager.execute(prepared)
            await manager.commit_progress(result)
            result = await manager.resolve_followup(prepared, result)
            await manager.commit(result)
            steps.append((prepared, result))
            if result.plan.response_text is not None:
                break
            prepared = await manager.prepare_observation(prepared, result)
        assert [entry.planning_step for entry, _ in steps] == [0, 1, 2]
        assert all(observation == message for observation, _ in seen)
        assert len({entry.invocation.invocation_key for entry, _ in steps}) == 1
        assert len({result.checkpoint_thread_id for _, result in steps}) == 3
        assert executor.calls == 2
        assert len(steps[-1][1].board.outcome_items) == 2
        assert seen[0][1].observed_execution is None
        assert len(seen[1][1].observed_execution.outcome_items) == 1
        projection = conversation_context_payload(seen[-1][1])["observed_execution"]
        assert len(projection["outcomes"]) == 2
        assert projection["facts"][0]["value"]["order_id"] == "DP1234"
        assert projection["facts"][0]["source_ref"] == "receipt:order-read"
        assert seen[-1][1].recent_messages == seen[0][1].recent_messages
        # Context and step identity use the actual checkpoint serializer.
        serde = target_checkpoint_serializer()
        restored = serde.loads_typed(serde.dumps_typed(prepared))
        assert restored.fingerprint == prepared.fingerprint
        assert restored.planning_step == 2
        assert conversation_context_payload(restored.context) == conversation_context_payload(prepared.context)

        class Planner:
            async def plan(self, *args):
                assert args[-1].observed_execution is not None
                return "observed"

        async def forbidden(*args):
            pytest.fail("An execution observation must not reconsume state or run the text encoder")

        cascade = CascadedTargetUnderstanding(forbidden, Planner(), encoder=forbidden)
        assert await cascade(message, result.state_after, prepared.deterministic,
                             manager._registry, seen[-1][1]) == "observed"
        with pytest.raises(PlanningInvariantError):
            await manager.prepare_observation(prepared, result)
    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["respond", "delegate", "clarify", "invalid_dag", "provider_failure", "budget"])
def test_turn_graph_observes_and_delivers_retained_results(ending):
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from tests.test_knowledge_answer_boundary import Verifier

    async def scenario():
        planner_calls = []

        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                planner_calls.append(observations.raw_text)
                if len(planner_calls) == 2 and ending == "delegate":
                    from application.turn_planning import CommandKind
                    base = await _OrderUnderstanding()()
                    return replace(base, commands=(replace(base.commands[0], kind=CommandKind.DELEGATE_TASK,
                        tool_id=None, objective=observations.raw_text),))
                if len(planner_calls) == 3 and ending != "budget":
                    if ending == "clarify":
                        return TurnProposal(ProposalDisposition.CLARIFY, (), "NEEDS_CONTEXT", ("delivery address",))
                    if ending == "invalid_dag":
                        base = (await _OrderUnderstanding()()).commands[0]
                        return TurnProposal(ProposalDisposition.RESOLVED, (
                            replace(base, command_id="a", dependencies=("b",)),
                            replace(base, command_id="b", dependencies=("a",))), "CYCLE")
                    return (TurnProposal(ProposalDisposition.RESPOND, (), "ANSWER", response_text="The order has shipped.")
                        if ending == "respond" else TurnProposal(ProposalDisposition.PROVIDER_FAILURE, (), "TEST_PROVIDER_DOWN"))
                base = await _OrderUnderstanding()()
                return replace(base, commands=(replace(base.commands[0], observe_result=True),))

        class Assembler(ResponseAssembler):
            def __init__(self):
                super().__init__(knowledge_verifier=Verifier(True))
                self.seen = []

            async def assemble(self, board, **kwargs):
                self.seen.append((board, kwargs))
                return await super().assemble(board, **kwargs)

        saver = InMemorySaver(serde=target_checkpoint_serializer())
        executor, assembler = _Executor(), Assembler()
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=executor,
                domain_workers={"order_logistics": executor}, checkpointer=saver))
        runtime = TurnRuntime(manager, assembler, checkpointer=saver,
                              max_observation_steps=1 if ending == "budget" else 4)
        identity, message = _identity(), TurnObservations("Check the shipment and finish my request")
        result = await runtime.execute(identity, message)
        assert executor.calls == 2
        assert len(assembler.seen) == 1
        board, inputs = assembler.seen[0]
        assert len(board.outcome_items) == 2
        completed = ending in {"respond", "delegate"}
        assert result.managed.request_completed is completed
        assert inputs["conversation_context"]["request_completed"] is completed
        assert all(text == message.raw_text for text in planner_calls)
        if ending == "clarify":
            assert inputs["conversation_context"]["clarification_fields"] == ["delivery address"]
            assert result.managed.plan.route.missing_inputs == ("delivery address",)
        elif not completed:
            assert result.managed.plan.observation_work_item_ids
            diagnostic, = result.managed.diagnostics
            assert diagnostic.detail["code"] == ("OBSERVATION_BUDGET_EXHAUSTED"
                if ending == "budget" else "OBSERVATION_CANDIDATE_REJECTED"
                if ending == "invalid_dag" else "TEST_PROVIDER_DOWN")
            assert inputs["system_notice"]
            assert result.assembled.diagnostics
        elif ending == "respond":
            assert inputs["response_candidate"] == "The order has shipped."
        else:
            assert len(planner_calls) == 2
            assert result.managed.plan.work.items[0].objective == message.raw_text
        # A completed turn is replayed from the saved result, not replanned.
        replay = await TurnRuntime(manager, assembler, checkpointer=saver,
            max_observation_steps=1 if ending == "budget" else 4).execute(identity, message)
        assert replay.managed.request_completed == result.managed.request_completed
        assert len(assembler.seen) == 1
        assert executor.calls == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("extra_read", [False, True])
@pytest.mark.parametrize("queued_count", [0, 1, 3])
@pytest.mark.parametrize("reverse_declarations", [False, True])
@pytest.mark.parametrize("native_tail", [False, True])
def test_successful_read_is_observed_while_independent_domain_wait_is_preserved(
    extra_read, queued_count, reverse_declarations, native_tail,
):
    from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from application.turn_planning import CommandKind
    from application.target_understanding import StateBoundTargetUnderstanding
    from tests.test_knowledge_answer_boundary import Verifier

    async def scenario():
        planned = []
        class Planner:
            async def plan(self, observations, state, deterministic, registry, context):
                planned.append(context)
                if len(planned) > 1:
                    assert context.observed_execution is not None
                    if state.pending_interaction is None:
                        return TurnProposal(ProposalDisposition.RESPOND, (), "OBSERVED_AFTER_RESUME",
                            response_text="The arrangement is complete.")
                    assert state.pending_interaction is not None
                    if extra_read and len(planned) == 2:
                        base = (await _OrderUnderstanding()()).commands[0]
                        return TurnProposal(ProposalDisposition.RESOLVED,
                            (replace(base, observe_result=True),), "ANOTHER_READ")
                    return TurnProposal(ProposalDisposition.RESPOND, (), "OBSERVED",
                        response_text="The order has shipped. Where should it be delivered?")
                base = (await _OrderUnderstanding()()).commands[0]
                commands = (
                    replace(base, observe_result=True),
                    replace(base, command_id="delivery", kind=CommandKind.DELEGATE_TASK,
                            tool_id=None, objective="Confirm the delivery preference"),
                    *(replace(base, command_id=f"after-delivery-{index}",
                        dependencies=("delivery" if index == 0 else f"after-delivery-{index - 1}",),
                        kind=CommandKind.DIRECT_TOOL if native_tail and index == queued_count - 1 else CommandKind.DELEGATE_TASK,
                        tool_id=base.tool_id if native_tail and index == queued_count - 1 else None,
                        observe_result=native_tail and index == queued_count - 1,
                        objective=f"Finish delivery arrangement step {index}") for index in range(queued_count)))
                return TurnProposal(ProposalDisposition.RESOLVED,
                    tuple(reversed(commands)) if reverse_declarations else commands, "MIXED")

        worker_calls = []
        async def worker(context):
            worker_calls.append(context)
            if len(worker_calls) == 1:
                return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
                    AgentResultStatus.NEEDS_USER_INPUT, "DELIVERY_PREFERENCE_REQUIRED", "test-v1",
                    missing_inputs=(MissingInputSpec("delivery_preference", context.work_item.work_item_id,
                        "DELIVERY_PREFERENCE_REQUIRED", {"type": "string"}, "Where should it be delivered?"),))
            return await _Executor()(context)

        saver = InMemorySaver(serde=target_checkpoint_serializer())
        executor = _Executor()
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"),
            understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), Planner()),
            orchestration=OrchestrationRuntime(direct_executor=executor,
                domain_workers={"order_logistics": worker}, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)),
            checkpointer=saver)
        first = await runtime.execute(_identity(), TurnObservations("Check shipment and arrange delivery"))
        pending = first.managed.state_after.pending_interaction
        assert pending is not None
        assert len(planned) == 2 + extra_read
        assert not first.managed.plan.observation_work_item_ids
        assert not first.managed.request_completed
        assert executor.calls == 1 + extra_read
        assert len(worker_calls) == 1
        identity = _identity()
        from core.identity import IdentityFactory
        second_id = IdentityFactory().create_invocation(tenant_id=str(identity.tenant_id),
            user_id=str(identity.user_id), conversation_id=str(identity.conversation_id), request_id="delivery-answer")
        resumed = await runtime.execute(second_id, TurnObservations("Home",
            interaction_id=pending.interaction_id, interaction_version=pending.version,
            interaction_values=((pending.requested_fields[0].target_work_item_id, "delivery_preference", "Home"),)))
        assert resumed.managed.state_after.pending_interaction is None
        resumed_read = int(native_tail and queued_count > 0)
        assert executor.calls == 1 + extra_read + resumed_read
        assert len(worker_calls) == 2 + queued_count - resumed_read
        assert resumed.managed.request_completed
        assert [call.work_item.objective for call in worker_calls[2:]] == [
            f"Finish delivery arrangement step {index}" for index in range(queued_count - resumed_read)]
        assert len(planned) == 2 + extra_read + resumed_read
    asyncio.run(scenario())


@pytest.mark.parametrize("point", ["before_observation", "after_execution", "before_assembly"])
def test_interrupted_turn_reuses_completed_read_steps(point):
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from tests.test_knowledge_answer_boundary import Verifier

    async def scenario():
        decisions = []
        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                decisions.append(observations.raw_text)
                if len(decisions) == 3:
                    return TurnProposal(ProposalDisposition.RESPOND, (), "DONE", response_text="The order has shipped.")
                base = await _OrderUnderstanding()()
                return replace(base, commands=(replace(base.commands[0], observe_result=True),))

        faults = []
        class InterruptOnce(TurnRuntime):
            def inject(self, stage):
                if point == stage and not faults:
                    faults.append(stage)
                    raise RuntimeError("injected phase interruption")

            async def _plan_observation(self, state):
                self.inject("before_observation")
                return await super()._plan_observation(state)

            async def _execute_work_plan(self, state):
                result = await super()._execute_work_plan(state)
                if state["prepared"].planning_step == 1:
                    self.inject("after_execution")
                return result

            async def _assemble_response(self, state):
                self.inject("before_assembly")
                return await super()._assemble_response(state)

        saver = InMemorySaver(serde=target_checkpoint_serializer())
        executor = _Executor()
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={}, checkpointer=saver))
        assembler = ResponseAssembler(knowledge_verifier=Verifier(True))
        identity, observations = _identity(), TurnObservations("Check shipment and finish the request")
        with pytest.raises(RuntimeError, match="injected phase interruption"):
            await InterruptOnce(manager, assembler, checkpointer=saver).execute(identity, observations)
        result = await TurnRuntime(manager, assembler, checkpointer=saver).execute(identity, observations)
        assert result.managed.request_completed
        assert len(result.managed.board.outcome_items) == 2
        assert executor.calls == 2
        assert len(decisions) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("clarify", [False, True])
def test_publication_preserves_observed_work_when_final_plan_has_no_tools(clarify):
    from application.chat_contracts import ChatCommand, Completed
    from application.target_chat_application import TargetChatApplication
    from application.response_assembly import ResponseAssembler
    from tests.test_target_chat_cutover import _Admission, _Publication
    from tests.test_knowledge_answer_boundary import Verifier

    async def scenario():
        calls = []
        class Understanding:
            async def __call__(self, *args):
                calls.append(args)
                if len(calls) == 2:
                    return (TurnProposal(ProposalDisposition.CLARIFY, (), "NEEDS_CONTEXT", ("delivery address",))
                        if clarify else TurnProposal(ProposalDisposition.RESPOND, (), "ANSWER",
                                                    response_text="The order has shipped."))
                base = await _OrderUnderstanding()()
                return replace(base, commands=(replace(base.commands[0], observe_result=True),))

        class Composer:
            async def compose(self, payload):
                assert payload["evidence"]["user_context"]["clarification_fields"] == ["delivery address"]
                assert not payload["evidence"]["coverage"]["task_completed"]
                return "The order has shipped. What is the delivery address?"

        executor = _Executor()
        registry = build_default_capability_registry("tenant-a")
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(), registry=registry,
            understanding=Understanding(), orchestration=OrchestrationRuntime(
                direct_executor=executor, domain_workers={}))
        app = TargetChatApplication(manager=manager, admission=_Admission(), publication=_Publication(),
            bundle_version=registry.bundle_version,
            response_assembler=ResponseAssembler(composer=Composer(), knowledge_verifier=Verifier(True)))
        result = await app.handle(ChatCommand(message="Check the shipment", user_id="user-a",
            conv_id="conversation-a", request_id="request-public", tenant_id="tenant-a"))
        assert isinstance(result, Completed), result
        body = result.response
        assert body["task_completed"] is (not clarify)
        assert len(body["agent_outcomes"]) == 1
        assert len(body["task_plan"]["work_item_ids"]) == 1
        assert body["evaluation_trace"]["cost"]["work_item_count"] == 1
        if clarify:
            assert "delivery address" in body["coverage"]["missing_requirement_ids"]
            assert "What is the delivery address?" in body["response"]
        assert executor.calls == 1
    asyncio.run(scenario())
