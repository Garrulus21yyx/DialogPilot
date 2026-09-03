"""Single lifecycle owner for Target Architecture v1 chat turns."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Protocol

from application.capability_registry import CapabilityRegistryBundle
from application.conversation_state import (
    ConversationState,
    ConversationStateConflict,
    ConversationStateStore,
    PendingApprovalState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.deterministic_resolution import (
    DeterministicResolution,
    DeterministicResolver,
    ResolutionKind,
    TurnObservations,
)
from application.orchestration_runtime import OrchestrationRuntime
from application.result_board import ResultBoardSnapshot
from application.agent_result import AgentResultStatus
from application.turn_planning import (
    MutationApplyStage,
    RoutePolicy,
    TurnPlan,
    TurnPlanCompiler,
    TurnProposal,
)
from application.work_item import ArgumentValue
from core.identity import InvocationIdentity


class TurnUnderstanding(Protocol):
    async def __call__(
        self,
        observations: TurnObservations,
        state: ConversationState,
        deterministic: DeterministicResolution,
        registry: CapabilityRegistryBundle,
    ) -> TurnProposal: ...


@dataclass(frozen=True)
class ManagedTurnResult:
    state_before: ConversationState
    state_after: ConversationState
    deterministic: DeterministicResolution
    plan: TurnPlan
    board: ResultBoardSnapshot | None
    checkpoint_thread_id: str


class TargetConversationManager:
    """Load, resolve, validate, persist plan state, and execute exactly once."""

    version = "target-conversation-manager-v1"

    def __init__(
        self,
        *,
        state_store: ConversationStateStore,
        registry: CapabilityRegistryBundle,
        understanding: TurnUnderstanding,
        orchestration: OrchestrationRuntime,
        resolver: DeterministicResolver | None = None,
        route_policy: RoutePolicy | None = None,
        compiler: TurnPlanCompiler | None = None,
    ) -> None:
        self._state_store = state_store
        self._registry = registry
        self._understanding = understanding
        self._orchestration = orchestration
        self._resolver = resolver or DeterministicResolver()
        self._route_policy = route_policy or RoutePolicy()
        self._compiler = compiler or TurnPlanCompiler()

    async def handle(
        self,
        invocation: InvocationIdentity,
        observations: TurnObservations,
        *,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
    ) -> ManagedTurnResult:
        state_before = self._state_store.load(
            invocation.tenant_id,
            invocation.user_id,
            invocation.conversation_id,
        )
        if state_before.owner.value != "AUTOMATION":
            raise ConversationStateConflict("human-owned conversation rejects automation")
        deterministic = self._resolver.resolve(observations, state_before)
        state = self._apply_deterministic(state_before, deterministic)
        if state is not state_before:
            self._persist(state_before, state)

        proposal = await self._understanding(
            observations, state, deterministic, self._registry,
        )
        validated = self._route_policy.accept(proposal, state, self._registry)
        plan = self._compiler.compile(validated, state, self._registry, invocation)
        planned_state = self._apply_plan_accepted(state, plan)
        if planned_state is not state:
            self._persist(state, planned_state)
            state = planned_state

        thread_id = str(invocation.invocation_key)
        if plan.work is None:
            return ManagedTurnResult(
                state_before,
                state,
                deterministic,
                plan,
                None,
                thread_id,
            )
        board = await self._orchestration.execute(
            plan.work,
            current_message=observations.raw_text,
            recent_relevant_turns=recent_relevant_turns,
            evidence_refs=evidence_refs,
            token_budget=token_budget,
            thread_id=thread_id,
            trusted_context={
                **invocation.metadata(),
                "conv_id": str(invocation.conversation_id),
                **(
                    {
                        "approved_operation_key": str(deterministic.operation_key),
                        "approval_binding": str(deterministic.signal_id),
                        "approval_target_version": str(
                            deterministic.target_entity_version
                        ),
                        "approval_actor": str(invocation.user_id),
                    }
                    if deterministic.kind is ResolutionKind.APPROVAL_DECISION
                    and deterministic.approved
                    else {}
                ),
            },
        )
        state = self._apply_successful_workflows(
            state, plan, board, invocation, deterministic,
        )
        return ManagedTurnResult(
            state_before,
            state,
            deterministic,
            plan,
            board,
            thread_id,
        )

    def _apply_successful_workflows(
        self,
        state: ConversationState,
        plan: TurnPlan,
        board: ResultBoardSnapshot,
        invocation: InvocationIdentity,
        deterministic: DeterministicResolution,
    ) -> ConversationState:
        if plan.work is None:
            return state
        if (
            deterministic.kind is ResolutionKind.APPROVAL_DECISION
            and deterministic.approved
        ):
            result = next(iter(board.results), None)
            if result is not None and result.status is AgentResultStatus.SUCCEEDED:
                stream = next(
                    item for item in state.workstreams
                    if item.workstream_id == deterministic.workstream_id
                )
                next_state = state.complete_workstream(
                    stream.workstream_id,
                    expected_version=stream.state_version,
                )
                self._persist(state, next_state)
                return next_state
        if plan.transitions is None:
            return state
        results = {item.work_item_id: item for item in board.results}
        for mutation in plan.transitions.mutations:
            result = results.get(mutation.bound_work_item_id)
            if result is None or result.status is not AgentResultStatus.SUCCEEDED:
                continue
            stream = next(
                item for item in state.workstreams
                if item.workstream_id == mutation.workstream_id
            )
            if mutation.target_version_field:
                fact = next(
                    (
                        item for item in result.facts
                        if item.requirement_id in plan.route.requirement_ids
                    ),
                    None,
                )
                value = json.loads(fact.value_json) if fact is not None else {}
                if value.get("eligible") is not True:
                    next_state = state.cancel_workstream(
                        stream.workstream_id,
                        expected_version=stream.state_version,
                    )
                    self._persist(state, next_state)
                    state = next_state
                    continue
                target_version = str(value.get(mutation.target_version_field) or "")
                if not target_version:
                    raise ConversationStateConflict(
                        "workflow preparation lacks authoritative target version"
                    )
                action = next(
                    item for item in self._registry.actions
                    if item.ref == mutation.action_ref
                )
                operation_key = str(invocation.operation_key(
                    stream.owner_agent,
                    action.action_id,
                    str(mutation.target_entity_ref),
                ))
                approval_id = "approval:v1:" + hashlib.sha256(
                    operation_key.encode("utf-8")
                ).hexdigest()
                arguments = tuple(
                    item for item in stream.slots
                    if item.name != "expected_order_version"
                ) + (ArgumentValue.create("expected_order_version", int(target_version)),)
                next_state = state.wait_for_approval(PendingApprovalState(
                    approval_id,
                    1,
                    stream.workstream_id,
                    mutation.bound_work_item_id,
                    str(mutation.action_ref),
                    operation_key,
                    str(mutation.target_entity_ref),
                    target_version,
                    (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                    arguments,
                ))
                self._persist(state, next_state)
                state = next_state
                continue
            next_state = state.complete_workstream(
                stream.workstream_id,
                expected_version=stream.state_version,
            )
            self._persist(state, next_state)
            state = next_state
            handoff_receipt = next((
                receipt for receipt in result.action_receipts
                if receipt.requirement_id == "support.handoff_action"
                and receipt.effect_status == "COMMITTED"
            ), None)
            if handoff_receipt is not None:
                next_state = state.transfer_to_human(handoff_receipt)
                self._persist(state, next_state)
                state = next_state
        return state

    def _apply_deterministic(
        self,
        state: ConversationState,
        resolution: DeterministicResolution,
    ) -> ConversationState:
        if resolution.state_fingerprint != state.fingerprint:
            raise ConversationStateConflict("resolution is bound to stale state")
        if resolution.kind is ResolutionKind.FILL_PENDING_INPUT:
            return state.consume_interaction(
                interaction_id=str(resolution.signal_id),
                interaction_version=int(resolution.signal_version),
                values=tuple(
                    (item.workstream_id, item.field_name, item.value)
                    for item in resolution.fields
                ),
            )
        if resolution.kind in {
            ResolutionKind.APPROVAL_DECISION,
            ResolutionKind.APPROVAL_EXPIRED,
        }:
            return state.consume_approval(
                approval_id=str(resolution.signal_id),
                approval_version=int(resolution.signal_version),
                approved=bool(resolution.approved),
            )
        if resolution.kind is ResolutionKind.CANCEL_WORKSTREAM:
            return state.cancel_workstream(
                str(resolution.workstream_id),
                expected_version=int(resolution.expected_workstream_version),
            )
        return state

    def _apply_plan_accepted(
        self,
        state: ConversationState,
        plan: TurnPlan,
    ) -> ConversationState:
        if plan.transitions is None:
            return state
        if plan.transitions.expected_conversation_version != state.version:
            raise ConversationStateConflict("flow plan is bound to stale conversation state")
        if plan.work is None:
            raise ConversationStateConflict("flow transition has no work plan")
        items = {item.work_item_id: item for item in plan.work.items}
        starts = []
        for mutation in plan.transitions.mutations:
            if mutation.apply_stage is not MutationApplyStage.PLAN_ACCEPTED:
                continue
            if mutation.kind != "START":
                raise ConversationStateConflict("unsupported plan-accepted mutation")
            item = items[mutation.bound_work_item_id]
            flow = self._registry.flow(mutation.flow_ref)
            starts.append(WorkstreamState(
                mutation.workstream_id,
                item.owner_agent,
                mutation.flow_ref,
                flow.initial_stage,
                WorkstreamStatus.ACTIVE,
                1,
                item.arguments,
                mutation.flow_ref,
            ))
        return state.start_workstreams(tuple(starts)) if starts else state

    def _persist(
        self,
        current: ConversationState,
        next_state: ConversationState,
    ) -> None:
        if not self._state_store.compare_and_set(current, next_state):
            raise ConversationStateConflict("conversation state changed concurrently")
