"""Single lifecycle owner for Target Architecture v1 chat turns."""
from __future__ import annotations

from dataclasses import dataclass, replace, field
import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from application.capability_registry import CapabilityRegistryBundle
from application.conversation_state import (
    ConversationState,
    ConversationStateConflict,
    ConversationStateStore,
    PendingApprovalState,
    PendingInteractionState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.deterministic_resolution import (
    DeterministicResolution,
    DeterministicResolver,
    ResolutionKind,
    TurnObservations,
)
from application.entity_binding import EntityBindingResolver, EntityBindingSet
from application.orchestration_runtime import OrchestrationRuntime
from application.result_board import ResultBoardSnapshot
from application.agent_result import AgentResultStatus, RequestedField, MissingInputSpec
from application.turn_planning import (
    MutationApplyStage,
    RoutePolicy,
    TurnPlan,
    TurnPlanCompiler,
    TurnProposal,
)
from application.work_item import ArgumentValue, WorkControlBinding
from core.identity import InvocationIdentity


class TurnUnderstanding(Protocol):
    async def __call__(
        self,
        observations: TurnObservations,
        state: ConversationState,
        deterministic: DeterministicResolution,
        registry: CapabilityRegistryBundle,
        turn_context: "TargetTurnContext",
    ) -> TurnProposal: ...


class TargetContextProjectionStatus(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class TargetContextMessage:
    role: str
    content: str
    source_ref: str
    seq: int = 0
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant", "system"}:
            raise ValueError("context message role is invalid")
        if not self.content.strip() or not self.source_ref.strip():
            raise ValueError("context message content and source are required")
        if self.seq < 0:
            raise ValueError("context message sequence cannot be negative")


@dataclass(frozen=True)
class TargetContextSummary:
    content: str
    source_ref: str
    covered_until_seq: int = 0
    producer_version: str = "conversation-memory-v1"

    def __post_init__(self) -> None:
        if not self.content.strip() or not self.source_ref.strip():
            raise ValueError("context summary content and source are required")
        if self.covered_until_seq < 0 or not self.producer_version.strip():
            raise ValueError("context summary metadata is invalid")


@dataclass(frozen=True)
class TargetTurnContext:
    recent_messages: tuple[TargetContextMessage, ...] = ()
    summary: TargetContextSummary | None = None
    evidence_refs: tuple[str, ...] = ()
    understanding_evidence: tuple[tuple[str, object], ...] = ()
    projection_status: TargetContextProjectionStatus = (
        TargetContextProjectionStatus.UNAVAILABLE
    )
    source_watermark: int = 0
    projection_reason_codes: tuple[str, ...] = ("CONTEXT_PROVIDER_NOT_CONFIGURED",)
    memory_attempted: bool = False
    memory_status: str = "NOT_REQUIRED"
    entity_bindings: EntityBindingSet = EntityBindingSet()
    knowledge_filter_contract: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_watermark < 0:
            raise ValueError("context source watermark cannot be negative")
        refs = tuple(item.source_ref for item in self.recent_messages)
        if len(refs) != len(set(refs)):
            raise ValueError("context message sources must be unique")
        if self.projection_status is TargetContextProjectionStatus.READY and (
            self.projection_reason_codes
        ):
            raise ValueError("ready context cannot carry projection failures")
        if self.projection_status is not TargetContextProjectionStatus.READY and (
            not self.projection_reason_codes
        ):
            raise ValueError("non-ready context requires a reason code")

    @property
    def recent_relevant_turns(self) -> tuple[str, ...]:
        """Compatibility rendering for the existing task-scoped Agent context."""
        return (
            *((f"summary: {self.summary.content}",) if self.summary else ()),
            *(f"{item.role}: {item.content}" for item in self.recent_messages),
        )


class TargetTurnContextProvider(Protocol):
    async def load(
        self,
        invocation: InvocationIdentity,
        observations: TurnObservations,
        state: ConversationState,
        deterministic: DeterministicResolution,
    ) -> TargetTurnContext: ...


@dataclass(frozen=True)
class ManagedTurnResult:
    state_before: ConversationState
    state_after: ConversationState
    deterministic: DeterministicResolution
    plan: TurnPlan
    board: ResultBoardSnapshot | None
    checkpoint_thread_id: str
    interaction_questions: tuple[MissingInputSpec, ...] = ()
    state_transitions: tuple[ConversationState, ...] = ()
    close_checkpoint: bool = False


@dataclass(frozen=True)
class PreparedTurn:
    invocation: InvocationIdentity
    observations: TurnObservations
    state_before: ConversationState
    state: ConversationState
    deterministic: DeterministicResolution
    plan: TurnPlan
    context: TargetTurnContext
    resume_thread_id: str | None
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    artifact_version: str = "prepared-turn-v2"
    execution_context: dict = field(default_factory=dict)
    state_transitions: tuple[ConversationState, ...] = ()

    @property
    def fingerprint(self) -> str:
        raw = json.dumps({
            "artifact_version": self.artifact_version,
            "invocation": str(self.invocation.invocation_key),
            "plan": self.plan.plan_id,
            "state": self.state.fingerprint,
            "context_watermark": self.context.source_watermark,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "prepared-turn:v1:" + hashlib.sha256(raw).hexdigest()


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
        context_provider: TargetTurnContextProvider | None = None,
        binding_resolver: EntityBindingResolver | None = None,
        conversation_agent=None,
    ) -> None:
        self._state_store = state_store
        self._registry = registry
        self._understanding = understanding
        self._orchestration = orchestration
        self._resolver = resolver or DeterministicResolver()
        self._route_policy = route_policy or RoutePolicy()
        self._compiler = compiler or TurnPlanCompiler()
        self._context_provider = context_provider
        self._binding_resolver = binding_resolver or EntityBindingResolver()
        self._conversation_agent = conversation_agent

    async def handle(
        self,
        invocation: InvocationIdentity,
        observations: TurnObservations,
        *,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
    ) -> ManagedTurnResult:
        prepared = await self.prepare(
            invocation,
            observations,
            recent_relevant_turns=recent_relevant_turns,
            evidence_refs=evidence_refs,
            token_budget=token_budget,
        )
        result = await self.execute(prepared)
        await self.commit(result)
        return result

    async def prepare(
        self,
        invocation: InvocationIdentity,
        observations: TurnObservations,
        *,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
        execution_context: dict | None = None,
    ) -> PreparedTurn:
        state_before = self._state_store.load(
            invocation.tenant_id,
            invocation.user_id,
            invocation.conversation_id,
        )
        if state_before.owner.value != "AUTOMATION":
            raise ConversationStateConflict("human-owned conversation rejects automation")
        deterministic = self._resolver.resolve(observations, state_before)
        resume_thread_id = self._resume_thread_id(state_before, deterministic)
        state = self._apply_deterministic(state_before, deterministic)
        transitions = [state] if state is not state_before else []

        turn_context = (
            await self._context_provider.load(
                invocation, observations, state, deterministic,
            )
            if self._context_provider is not None
            else TargetTurnContext()
        )
        from application.sales_channels import filter_contract
        turn_context = replace(
            turn_context,
            knowledge_filter_contract=filter_contract((execution_context or {}).get("knowledge_filter_contract")),
            entity_bindings=self._binding_resolver.resolve(
                observations, state, turn_context,
            ),
        )
        proposal = await self._understanding(
            observations, state, deterministic, self._registry, turn_context,
        )
        validated = self._route_policy.accept(proposal, state, self._registry)
        plan = self._compiler.compile(validated, state, self._registry, invocation)
        planned_state = self._apply_plan_accepted(state, plan, invocation)
        if planned_state is not state:
            transitions.append(planned_state)
            state = planned_state

        return PreparedTurn(
            invocation,
            observations,
            state_before,
            state,
            deterministic,
            plan,
            turn_context,
            resume_thread_id,
            recent_relevant_turns,
            evidence_refs,
            token_budget,
            execution_context={**dict(execution_context or {}), "knowledge_filter_contract": turn_context.knowledge_filter_contract},
            state_transitions=tuple(transitions),
        )

    async def execute(self, prepared: PreparedTurn) -> ManagedTurnResult:
        # PreparedTurn is checkpointed before consuming input or accepting work.
        # A replay recognizes only an exact committed prefix of this plan.
        self._commit_states(prepared.state_before, prepared.state_transitions)
        invocation = prepared.invocation
        observations = prepared.observations
        state_before = prepared.state_before
        state = prepared.state
        deterministic = prepared.deterministic
        plan = prepared.plan
        turn_context = prepared.context
        resume_thread_id = prepared.resume_thread_id
        thread_id = resume_thread_id or str(invocation.invocation_key)
        if plan.work is None:
            return ManagedTurnResult(
                state_before,
                state,
                deterministic,
                plan,
                None,
                thread_id,
                state_transitions=prepared.state_transitions,
                close_checkpoint=resume_thread_id is not None,
            )
        execution_context = {
            "current_message": observations.raw_text,
            "pending_approval": state.pending_approval,
            "recent_relevant_turns": tuple(dict.fromkeys((
                *prepared.recent_relevant_turns,
                *turn_context.recent_relevant_turns,
            ))),
            "evidence_refs": tuple(dict.fromkeys((
                *prepared.evidence_refs,
                *turn_context.evidence_refs,
            ))),
            "token_budget": prepared.token_budget,
            "trusted_context": {
                **prepared.execution_context,
                **invocation.metadata(),
                "conv_id": str(invocation.conversation_id),
                "resolved_input_signal": (str(deterministic.signal_id or "")
                    if deterministic.kind in {ResolutionKind.FILL_PENDING_INPUT, ResolutionKind.REPLY_PENDING_INPUT}
                    else ""),
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
        }
        # Retain this execution checkpoint until the conversation owner decides
        # whether stopped domain work needs a user continuation.
        await_decision = self._awaits_workflow_approval(plan) or (
            self._conversation_agent is not None
            and any(item.control_mode.value == "DELEGATED" for item in plan.work.items))
        board = (
            await self._orchestration.resume(
                plan.work,
                thread_id=thread_id,
                interrupt_after_completion=await_decision,
                **execution_context,
            )
            if resume_thread_id is not None else
            await self._orchestration.execute(
                plan.work,
                thread_id=thread_id,
                interrupt_after_completion=await_decision,
                **execution_context,
            )
        )
        recovery_inputs = ()
        if self._conversation_agent is not None and self._orchestration.supports_resume:
            from application.conversation_context import conversation_context_payload
            recovery_inputs = await self._conversation_agent.recover(
                plan.work, board, current_message=observations.raw_text,
                conversation_context=conversation_context_payload(turn_context))
        transitions = list(prepared.state_transitions)
        state = self._apply_successful_workflows(
            state, plan, board, invocation, deterministic,
            checkpoint_thread_id=(
                thread_id if self._orchestration.supports_resume else None
            ),
            transitions=transitions,
        )
        from application.action_approval import bind_action_approval
        next_state = bind_action_approval(
            state, plan, board, self._registry,
            thread_id if self._orchestration.supports_resume else None,
        )
        if next_state is not state:
            transitions.append(next_state)
            state = next_state
        next_state = self._apply_missing_inputs(
            state, plan, board,
            recovery_inputs=recovery_inputs,
            checkpoint_thread_id=(
                thread_id if self._orchestration.supports_resume else None
            ),
        )
        if next_state is not state:
            transitions.append(next_state)
            state = next_state
        close_checkpoint = (
            self._orchestration.supports_resume
            and await_decision
            and not any(pending is not None and pending.checkpoint_thread_id == thread_id
                        for pending in (state.pending_approval, state.pending_interaction))
        )
        return ManagedTurnResult(
            state_before,
            state,
            deterministic,
            plan,
            board,
            thread_id,
            tuple(spec for result in board.results
                  if result.status is AgentResultStatus.NEEDS_USER_INPUT
                  for spec in result.missing_inputs) + recovery_inputs,
            tuple(transitions),
            close_checkpoint,
        )

    async def commit(self, result: ManagedTurnResult) -> None:
        """Commit the checkpointed decision, then release its execution wait."""
        self._commit_states(result.state_before, result.state_transitions)
        if result.close_checkpoint:
            await self._orchestration.cancel_interrupt(
                thread_id=result.checkpoint_thread_id)

    def _apply_missing_inputs(
        self,
        state: ConversationState,
        plan: TurnPlan,
        board: ResultBoardSnapshot,
        *,
        checkpoint_thread_id: str | None,
        recovery_inputs: tuple[MissingInputSpec, ...] = (),
    ) -> ConversationState:
        missing_results = tuple(
            result for result in board.results
            if result.status is AgentResultStatus.NEEDS_USER_INPUT
        )
        # Work already suspended in this approval checkpoint resumes after the
        # decision. A question from another execution has its own continuation.
        if (state.pending_approval is not None
                and state.pending_approval.checkpoint_thread_id == checkpoint_thread_id):
            return state
        if not missing_results and not recovery_inputs:
            return state
        if state.pending_interaction is not None:
            raise ConversationStateConflict(
                "work result cannot create a second pending interaction"
            )
        if plan.work is None:
            raise ConversationStateConflict("missing input has no work plan")
        transition_items = {
            mutation.bound_work_item_id
            for mutation in (plan.transitions.mutations if plan.transitions else ())
        }
        item_by_id = {item.work_item_id: item for item in plan.work.items}
        suspended = []
        fields = []
        all_specs = tuple(spec for result in missing_results for spec in result.missing_inputs) + recovery_inputs
        target_ids = tuple(dict.fromkeys(spec.target_work_item_id for spec in all_specs if spec.required))
        for work_item_id in target_ids:
            if work_item_id in transition_items:
                raise ConversationStateConflict(
                    "workflow input must be resolved before its state transition"
                )
            item = item_by_id[work_item_id]
            suspended.append(replace(item, dependencies=()))
            fields.extend(
                RequestedField(
                    spec.field_name,
                    spec.target_work_item_id,
                    spec.value_schema,
                )
                for spec in all_specs
                if spec.required and spec.target_work_item_id == work_item_id
            )
        if not fields:
            raise ConversationStateConflict(
                "NEEDS_USER_INPUT produced no required fields"
            )
        identity_payload = json.dumps(
            {
                "plan": plan.plan_id,
                "state_version": state.version,
                "fields": sorted(
                    (item.target_work_item_id, item.field_name, item.value_schema)
                    for item in fields
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        interaction_id = "interaction:v1:" + hashlib.sha256(
            identity_payload.encode("utf-8")
        ).hexdigest()
        next_state = state.wait_for_interaction(PendingInteractionState(
            interaction_id,
            1,
            tuple(fields),
            (),
            tuple(suspended),
            checkpoint_thread_id,
        ))
        return next_state

    def _apply_successful_workflows(
        self,
        state: ConversationState,
        plan: TurnPlan,
        board: ResultBoardSnapshot,
        invocation: InvocationIdentity,
        deterministic: DeterministicResolution,
        *,
        checkpoint_thread_id: str | None,
        transitions: list[ConversationState],
    ) -> ConversationState:
        if plan.work is None and not plan.control_mutations:
            return state
        plan_items = {item.work_item_id: item for item in plan.work.items}
        if (
            deterministic.kind is ResolutionKind.APPROVAL_DECISION
            or deterministic.kind is ResolutionKind.RECONCILE_WORKFLOW
        ) and deterministic.approved:
            result = next(iter(board.results), None)
            stream = next(
                item for item in state.workstreams
                if item.workstream_id == deterministic.workstream_id
            )
            if result is not None and result.status is AgentResultStatus.SUCCEEDED:
                next_state = state.complete_workstream(
                    stream.workstream_id,
                    expected_version=stream.state_version,
                )
                transitions.append(next_state)
                return next_state
            if result is not None and result.status is AgentResultStatus.RECONCILING:
                next_state = state.mark_workstream_reconciling(
                    stream.workstream_id,
                    expected_version=stream.state_version,
                )
                if next_state is not state:
                    transitions.append(next_state)
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
            if mutation.preparation_requirement_id:
                bindings = (
                    mutation.readiness_field,
                    mutation.readiness_value_json,
                    mutation.target_version_field,
                    mutation.target_version_argument,
                )
                if any(not str(item or "").strip() for item in bindings):
                    raise ConversationStateConflict(
                        "workflow preparation binding is incomplete"
                    )
                fact = next(
                    (
                        item for item in result.facts
                        if item.requirement_id == mutation.preparation_requirement_id
                    ),
                    None,
                )
                if fact is None:
                    raise ConversationStateConflict(
                        "workflow preparation lacks its authoritative requirement"
                    )
                value = json.loads(fact.value_json)
                if not isinstance(value, dict):
                    raise ConversationStateConflict(
                        "workflow preparation fact must be an object"
                    )
                readiness_value = json.loads(str(mutation.readiness_value_json))
                if value.get(str(mutation.readiness_field)) != readiness_value:
                    next_state = state.cancel_workstream(
                        stream.workstream_id,
                        expected_version=stream.state_version,
                    )
                    transitions.append(next_state)
                    state = next_state
                    continue
                target_version_value = value.get(str(mutation.target_version_field))
                if target_version_value is None or target_version_value == "":
                    raise ConversationStateConflict(
                        "workflow preparation lacks authoritative target version"
                    )
                target_version = str(target_version_value)
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
                    if item.name != mutation.target_version_argument
                ) + (ArgumentValue.create(
                    str(mutation.target_version_argument), target_version_value,
                ),)
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
                    checkpoint_thread_id,
                    plan_items[mutation.bound_work_item_id].argument_bindings,
                ))
                transitions.append(next_state)
                state = next_state
                continue
            next_state = state.complete_workstream(
                stream.workstream_id,
                expected_version=stream.state_version,
            )
            transitions.append(next_state)
            state = next_state
            handoff_receipt = next((
                receipt for receipt in result.action_receipts
                if receipt.requirement_id == "support.handoff_action"
                and receipt.effect_status == "COMMITTED"
            ), None)
            if handoff_receipt is not None:
                next_state = state.transfer_to_human(handoff_receipt)
                transitions.append(next_state)
                state = next_state
        return state

    @staticmethod
    def _resume_thread_id(
        state: ConversationState,
        resolution: DeterministicResolution,
    ) -> str | None:
        if resolution.kind in {ResolutionKind.FILL_PENDING_INPUT, ResolutionKind.REPLY_PENDING_INPUT}:
            return (
                state.pending_interaction.checkpoint_thread_id
                if state.pending_interaction is not None else None
            )
        if resolution.kind in {
            ResolutionKind.APPROVAL_DECISION,
            ResolutionKind.APPROVAL_EXPIRED,
        }:
            return (
                state.pending_approval.checkpoint_thread_id
                if state.pending_approval is not None else None
            )
        return None

    @staticmethod
    def _awaits_workflow_approval(plan: TurnPlan) -> bool:
        return bool(
            plan.transitions is not None
            and any(
                mutation.preparation_requirement_id is not None
                for mutation in plan.transitions.mutations
            )
        )

    def _apply_deterministic(
        self,
        state: ConversationState,
        resolution: DeterministicResolution,
    ) -> ConversationState:
        if resolution.state_fingerprint != state.fingerprint:
            raise ConversationStateConflict("resolution is bound to stale state")
        if resolution.kind is ResolutionKind.REPLY_PENDING_INPUT:
            return state.consume_interaction_reply(
                interaction_id=str(resolution.signal_id),
                interaction_version=int(resolution.signal_version),
            )
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
        invocation: InvocationIdentity,
    ) -> ConversationState:
        if plan.work is None:
            return state
        if (
            plan.transitions is not None
            and plan.transitions.expected_conversation_version != state.version
        ):
            raise ConversationStateConflict("flow plan is bound to stale conversation state")
        items = {
            item.work_item_id: item for item in (plan.work.items if plan.work else ())
        }
        starts = []
        for mutation in (plan.transitions.mutations if plan.transitions else ()):
            if mutation.apply_stage is not MutationApplyStage.PLAN_ACCEPTED:
                continue
            if mutation.kind != "START":
                raise ConversationStateConflict("unsupported plan-accepted mutation")
            item = items[mutation.bound_work_item_id]
            flow = self._registry.flow(mutation.flow_ref) if mutation.flow_ref else None
            starts.append(WorkstreamState(
                mutation.workstream_id,
                item.owner_agent,
                mutation.flow_ref or str(mutation.action_ref),
                flow.initial_stage if flow is not None else "PREPARE_ACTION",
                WorkstreamStatus.ACTIVE,
                1,
                item.arguments,
                mutation.flow_ref,
                item.argument_bindings,
            ))
        return state.accept_work_items(
            plan.work.items if plan.work else (),
            invocation_key=str(invocation.invocation_key),
            started_workstreams=tuple(starts),
            cancelled_controls=tuple(
                WorkControlBinding(item.control_id, item.expected_revision)
                for item in plan.control_mutations
            ),
        )

    def _commit_states(
        self,
        current: ConversationState,
        transitions: tuple[ConversationState, ...],
    ) -> None:
        states = (current, *transitions)
        stored = self._state_store.load(
            current.tenant_id, current.user_id, current.conversation_id)
        matched = next((index for index, state in enumerate(states)
                        if state.fingerprint == stored.fingerprint), None)
        if matched is None:
            raise ConversationStateConflict("conversation state changed outside this turn")
        for before, after in zip(states[matched:], states[matched + 1:]):
            if not self._state_store.compare_and_set(before, after):
                raise ConversationStateConflict("conversation state changed concurrently")
