"""Minimal real ChatApplication composition for locked L0 clarification eval."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from application.active_case import (
    ActiveCaseContextView,
    ActiveCaseProjection,
    ActiveCaseSelection,
    ActiveCaseState,
)
from application.always_defer_command_encoder import AlwaysDeferCommandEncoder
from application.chat_application import ChatApplication, ChatOperations, ChatServices
from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import command_primary_flow_registry
from application.route_decision import RouteMode
from application.route_policy_v2 import RoutePolicy
from application.selective_command_producer import SelectiveCommandProducer
from application.structured_command_producer import (
    CommandCompletionPort,
    StructuredLLMCommandProducer,
)
from application.turn_plan import TurnPlanCompiler
from application.turn_understanding import PendingSlotResolver
from core.llm_metrics import capture_llm_usage
from evaluation.command_primary_eval.locked_conversation import (
    LockedConversationTransportAdapter,
)
from evaluation.command_primary_eval.locked_l0_artifacts import write_locked_l0_run
from evaluation.command_primary_eval.locked_l0_clarification import (
    LiveProviderIdentity,
    load_locked_l0_clarification_cases,
    score_locked_l0_turn,
)
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.response_delivery import DeliveryStatus


class ObservedCommandCompletion:
    """Record fingerprints around the real completion without retaining prompts."""

    def __init__(self, delegate: CommandCompletionPort) -> None:
        self._delegate = delegate
        self.provider_version = delegate.provider_version
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, input_json: str) -> str:
        call = {
            "system_prompt_sha256": _sha(system),
            "input_sha256": _sha(input_json),
            "output_sha256": None,
            "error_type": None,
        }
        self.calls.append(call)
        try:
            output = await self._delegate.complete(
                system=system,
                input_json=input_json,
            )
        except BaseException as exc:
            call["error_type"] = type(exc).__name__
            raise
        call["output_sha256"] = _sha(output)
        return output


class ObservedCommandPlanner:
    def __init__(self, delegate: CommandPrimaryChatPlanner) -> None:
        self._delegate = delegate
        self.results: list[Any] = []

    async def prepare(self, **kwargs: Any) -> Any:
        result = await self._delegate.prepare(**kwargs)
        self.results.append(result)
        return result

    async def commit_flow_transition(self, plan: Any) -> bool:
        return await self._delegate.commit_flow_transition(plan)


@dataclass(frozen=True)
class LockedL0ChatRuntime:
    application: ChatApplication
    completion: ObservedCommandCompletion
    planner: ObservedCommandPlanner


def build_locked_l0_chat_runtime(
    completion: CommandCompletionPort,
) -> LockedL0ChatRuntime:
    observed_completion = ObservedCommandCompletion(completion)
    semantic = SelectiveCommandProducer(
        AlwaysDeferCommandEncoder(),
        StructuredLLMCommandProducer(observed_completion),
    )
    observed_planner = ObservedCommandPlanner(
        CommandPrimaryChatPlanner(
            CommandPrimaryPlanner(
                PendingSlotResolver(lambda _signal, _message: None),
                semantic,
                RoutePolicy(),
                TurnPlanCompiler(),
            ),
            command_primary_flow_registry,
            primary_route_modes=(RouteMode.CLARIFY,),
        )
    )

    class Orchestrator:
        async def recognize_intent(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("locked command-primary run reached legacy Intent")

        async def run(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("locked clarification run reached Agent execution")

    class Memory:
        async def get_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                recent_messages=[],
                retrieval_hits=[],
                to_sections=lambda: [],
            )

        async def add_messages(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class Delivery:
        @staticmethod
        def select_response(**_kwargs: Any) -> Any:
            return SimpleNamespace(
                response_id="locked-l0-response",
                seq=1,
                status=DeliveryStatus.SELECTED,
            )

    async def active_case(*_args: Any, **_kwargs: Any) -> ActiveCaseContextView:
        return ActiveCaseContextView(
            ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
            ActiveCaseSelection((), ()),
        )

    verification = VerificationResult(
        VerificationStatus.PASS,
        True,
        False,
        "policy terminal",
        VerificationReasonCode.POLICY_TERMINAL,
    )
    services = ChatServices(
        orchestrator=Orchestrator(),
        memory=Memory(),
        answer_verifier=object(),
        ticket_service=object(),
        response_delivery=Delivery(),
        context_assembler=SimpleNamespace(
            assemble=lambda **_kwargs: SimpleNamespace(system_context=""),
        ),
        bundle_registry=object(),
        bundle_resolver=SimpleNamespace(
            resolve=lambda _user_id: SimpleNamespace(
                primary=SimpleNamespace(version="locked-l0-eval-bundle-v1"),
                pinned_refs=None,
            )
        ),
        command_primary_chat_planner=observed_planner,
    )
    operations = ChatOperations(
        active_ticket_context=active_case,
        build_knowledge_context=lambda *_args, **_kwargs: _unexpected("Knowledge RAG"),
        capture_badcases=lambda **_kwargs: _done(),
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: verification,
        publish_candidate=lambda candidate, _verification: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=lambda **_kwargs: _unexpected("legacy Intent"),
        select_publication_candidate=lambda candidate, _evidence, **_kwargs: (
            candidate,
            False,
        ),
        trace_id=lambda: "locked-l0-eval-trace",
        verify_for_publication=lambda *_args, **_kwargs: _unexpected("Verifier"),
    )
    return LockedL0ChatRuntime(
        ChatApplication(services, operations),
        observed_completion,
        observed_planner,
    )


async def run_locked_l0_chat_eval(
    *,
    dataset_root: Any,
    output_dir: Any,
    run_id: str,
    completion: CommandCompletionPort,
    provider: LiveProviderIdentity,
    case_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    cases = load_locked_l0_clarification_cases(dataset_root, case_ids=case_ids)
    runtime = build_locked_l0_chat_runtime(completion)
    transport = LockedConversationTransportAdapter(runtime.application, dataset_root)
    predictions = []
    for case in cases:
        call_offset = len(runtime.completion.calls)
        plan_offset = len(runtime.planner.results)
        started = time.perf_counter()
        with capture_llm_usage() as usage:
            turns = await transport.run(str(case["case_id"]))
        calls = runtime.completion.calls[call_offset:]
        plan = (
            runtime.planner.results[plan_offset]
            if len(runtime.planner.results) == plan_offset + 1
            else None
        )
        predictions.append(
            score_locked_l0_turn(
                case=case,
                outcome=turns[0].outcome,
                chat_plan=plan,
                completion_call=calls[0] if len(calls) == 1 else None,
                usage=usage.summary(),
                provider=provider,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                run_id=run_id,
            )
        )
    registry = command_primary_flow_registry(
        str(cases[0]["initial_state"]["tenant_id"])
    )
    return dict(
        write_locked_l0_run(
            dataset_root=dataset_root,
            output_dir=output_dir,
            run_id=run_id,
            cases=cases,
            predictions=predictions,
            provider=provider,
            completion_version=completion.provider_version,
            prompt_fingerprints=[
                call["system_prompt_sha256"] for call in runtime.completion.calls
            ],
            registry=registry,
        )
    )


async def _done() -> None:
    return None


async def _unexpected(component: str) -> None:
    raise RuntimeError(f"locked L0 clarification invoked {component}")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
