"""Checkpointed turn phases over the existing conversation and WorkPlan owners."""
from __future__ import annotations
from contextlib import nullcontext
import logging
from langfuse import propagate_attributes

from dataclasses import dataclass, replace
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from application.conversation_context import conversation_context_payload
from application.agent_result import MissingInputSpec
from application.deterministic_resolution import TurnObservations
from application.response_assembly import AssembledResponse, ResponseAssembler
from application.target_conversation_manager import (
    ManagedTurnResult,
    PreparedTurn,
    TargetConversationManager,
)
from core.identity import InvocationIdentity
from application.chat_contracts import StageObservation, StageStatus
from application.conversation_state import ConversationState
from application.turn_planning import PlanningUnavailable, TurnPlanningError
from core.framework_models import ModelInvocationError
from application.execution_progress import advance_progress, planning_observations, requires_observation, PROGRESS_FEEDBACK


logger = logging.getLogger(__name__)


class TurnRuntimeError(ValueError):
    pass


class TurnCheckpointVersionError(TurnRuntimeError):
    """Old in-flight decisions need explicit reconciliation, not new execution."""


class TurnGraphState(TypedDict, total=False):
    runtime_version: str
    invocation: InvocationIdentity
    invocation_key: str
    observations: TurnObservations
    execution_context: dict
    prepared: PreparedTurn
    managed: ManagedTurnResult
    assembled: AssembledResponse | None
    observation_failed: bool
    observation_progress: dict
    presentation_state: ConversationState
    preparation_options: dict


@dataclass(frozen=True)
class TurnRuntimeResult:
    managed: ManagedTurnResult
    assembled: AssembledResponse | None


class TurnRuntime:
    """Coordinate durable turn phases without owning their domain semantics."""

    version = "turn-runtime-v24-scoped-input-revision"

    def __init__(
        self,
        manager: TargetConversationManager,
        response_assembler: ResponseAssembler,
        callbacks=(),
        *,
        checkpointer=None,
        interaction_published=None,
        trace_sink=None,
        max_observation_steps: int = 4,
    ) -> None:
        self._manager = manager
        self._assembler = response_assembler
        self._callbacks = callbacks
        self._checkpointer = checkpointer
        self._interaction_published = interaction_published
        self._trace_sink = trace_sink
        if type(max_observation_steps) is not int or max_observation_steps < 1:
            raise TurnRuntimeError("observation budget must be a positive integer")
        self._max_observation_steps = max_observation_steps
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(TurnGraphState)
        for name, node in (
            ("prepare_turn", self._prepare_turn),
            ("execute_work_plan", self._execute_work_plan),
            ("commit_turn_state", self._commit_turn_state),
            ("commit_progress", self._commit_progress),
            ("resolve_followup", self._resolve_followup),
            ("assemble_response", self._assemble_response),
            ("commit_observation", self._commit_turn_state),
            ("plan_observation", self._plan_observation),
        ):
            builder.add_node(name, node)
        builder.add_edge(START, "prepare_turn")
        builder.add_edge("prepare_turn", "execute_work_plan")
        builder.add_edge("execute_work_plan", "commit_progress")
        builder.add_edge("commit_progress", "resolve_followup")
        builder.add_conditional_edges("resolve_followup", self._after_followup,
                                      ["commit_observation", "assemble_response"])
        builder.add_edge("commit_observation", "plan_observation")
        builder.add_conditional_edges("plan_observation",
            lambda state: "assemble_response" if state.get("observation_failed") else "execute_work_plan",
            ["assemble_response", "execute_work_plan"])
        builder.add_edge("assemble_response", "commit_turn_state")
        builder.add_edge("commit_turn_state", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def _prepare_turn(self, state: TurnGraphState):
        try:
            prepared = await self._manager.prepare(
                state["invocation"], state["observations"],
                execution_context=state.get("execution_context", {}),
                **state.get("preparation_options", {}),
            )
        except PlanningUnavailable as exc:
            self._record_failure(StageObservation(
                "planning", StageStatus.FAILED,
                {**exc.detail, "code": exc.reason_code},
            ))
            raise
        return {"prepared": prepared, "presentation_state": prepared.state_before}

    async def _execute_work_plan(self, state: TurnGraphState):
        return {"managed": await self._manager.execute(state["prepared"])}

    async def _commit_turn_state(self, state: TurnGraphState):
        await self._manager.commit(state["managed"])
        return {}

    async def _commit_progress(self, state: TurnGraphState):
        return {"managed": await self._manager.commit_progress(state["managed"], prepared=state["prepared"])}

    async def _resolve_followup(self, state: TurnGraphState):
        return {"managed": await self._manager.resolve_followup(state["prepared"], state["managed"])}

    @staticmethod
    def _after_followup(state):
        result = state["managed"]
        return "commit_observation" if requires_observation(result.plan, result.board) else "assemble_response"

    async def _plan_observation(self, state: TurnGraphState):
        prepared, managed = state["prepared"], state["managed"]
        progress = advance_progress(state.get("observation_progress", {}),
                                    planning_observations(managed.board))
        failure = None
        if progress.get("progress_blocked"):
            failure = StageObservation("planning_observation", StageStatus.FAILED,
                                       {"code": "OBSERVATION_NO_PROGRESS"})
        elif prepared.planning_step >= self._max_observation_steps:
            failure = StageObservation("planning_observation", StageStatus.FAILED,
                                       {"code": "OBSERVATION_BUDGET_EXHAUSTED"})
        else:
            try:
                next_plan = await self._manager.prepare_observation(prepared, managed,
                    progress_feedback=PROGRESS_FEEDBACK if progress.get("progress_warning") else "")
                return {"prepared": next_plan, "observation_failed": False,
                        "observation_progress": progress}
            except (PlanningUnavailable, TurnPlanningError, ModelInvocationError) as exc:
                from core.tracing import exception_chain
                code = (exc.reason_code if isinstance(exc, PlanningUnavailable)
                        else "OBSERVATION_CANDIDATE_REJECTED" if isinstance(exc, TurnPlanningError)
                        else "OBSERVATION_PROVIDER_FAILURE")
                failure = StageObservation("planning_observation", StageStatus.FAILED,
                    {**(exc.detail if isinstance(exc, PlanningUnavailable) else {}), "code": code,
                     "exception_chain": exception_chain(exc)})
        self._record_failure(failure)
        return {"managed": replace(managed, diagnostics=(*managed.diagnostics, failure)),
                "observation_failed": True, "observation_progress": progress}

    def _record_failure(self, failure: StageObservation) -> None:
        if self._trace_sink is None:
            return
        try:
            self._trace_sink.record_failure(failure.to_dict())
        except Exception:
            # Trace export is observational and cannot change turn semantics.
            logger.exception("Failure trace export failed; preserving the planning failure")

    @property
    def supports_resume(self) -> bool:
        return self._checkpointer is not None

    async def _assemble_response(self, state: TurnGraphState):
        managed = state["managed"]
        presentation_state = state["presentation_state"]
        board = managed.board
        # This node leads only to commit and END, not back to execution. An
        # unfinished objective or durable wait is not a scheduled background job.
        context = conversation_context_payload(state["prepared"].context) or {}
        context = {**context, "turn_execution": {
            "phase": "REPLY", "continues_after_reply": False,
            "waiting_for_input": managed.state_after.pending_interaction is not None,
            "waiting_for_approval": managed.state_after.pending_approval is not None,
        }}
        response_only = managed.plan.response_text is not None and board is None
        pending_approval = managed.state_after.pending_approval
        previous_approval = presentation_state.pending_approval
        from application.action_approval import approval_presentation_due
        present_approval = approval_presentation_due(pending_approval, previous_approval,
            published=(self._interaction_published(state["invocation"],
                signal_id=pending_approval.approval_id, signal_version=pending_approval.version)
                if pending_approval is not None and self._interaction_published is not None else None))
        pending_input = managed.state_after.pending_interaction
        present_input = pending_input is not None and (
            presentation_state.pending_interaction is None
            or (pending_input.interaction_id, pending_input.version) != (
                presentation_state.pending_interaction.interaction_id, presentation_state.pending_interaction.version)
            or self._interaction_published is not None and not self._interaction_published(
                state["invocation"], signal_id=pending_input.interaction_id, signal_version=pending_input.version))
        if pending_approval is not None and not present_approval:
            context = {**context, "retained_approval": {
                "operations": [op.view() for op in pending_approval.operations],
                "status": "AWAITING_DECISION_NOT_EXECUTED",
                "presentation": "Answer this turn without soliciting approval again.",
            }}
        if response_only and not present_approval and not present_input:
            # A conversational reply neither presents nor consumes an existing
            # wait. Verify the model's candidate through the same reply boundary.
            context = {**context, "turn_contract": "RESPONSE_ONLY_NO_STATE_CHANGE",
                "pending_interaction": managed.state_after.pending_interaction is not None,
                "pending_approval": managed.state_after.pending_approval is not None}
            assembled = await self._assembler.assemble(
                None, current_message=state["observations"].raw_text,
                conversation_context=context, response_candidate=managed.plan.response_text)
            return {"assembled": assembled}
        questions = ()
        if present_input:
            candidates = tuple(spec for item, result in (board.outcome_items if board else ())
                if result is not None and any(item == original for original in pending_input.suspended_work_items)
                for spec in result.missing_inputs if spec.required) or managed.interaction_questions
            if all(field.question_hint for field in pending_input.requested_fields):
                candidates = tuple(MissingInputSpec(field.field_name, field.target_work_item_id,
                    "PENDING_INPUT", field.value_schema, field.question_hint)
                    for field in pending_input.requested_fields)
            bindings = {(field.target_work_item_id, field.field_name)
                        for field in pending_input.requested_fields}
            questions = tuple(spec for spec in candidates
                              if (spec.target_work_item_id, spec.field_name) in bindings)
            if {(spec.target_work_item_id, spec.field_name) for spec in questions} != bindings:
                raise TurnRuntimeError("Pending input lacks its bound question specification")
        notice = (
            ("An account security risk was detected. Security handling took priority; other high-risk operations were not started this turn.\n"
             if self._assembler.fallback_locale == "en" else
             "检测到账户安全风险，已优先处理安全任务；本轮未启动其他高风险业务操作。\n")
            if managed.plan.route.reason_code
            == "SECURITY_PREEMPTED_NONESSENTIAL_WRITES"
            else ""
        )
        if board is None and not questions and not present_approval:
            return {"assembled": None}
        context = {**(context or {}), "request_completed": managed.request_completed}
        if managed.plan.route.missing_inputs:
            context["clarification_fields"] = list(managed.plan.route.missing_inputs)
        if state.get("observation_failed"):
            notice += ("I could not finish the remaining steps. Completed results are preserved below.\n"
                       if self._assembler.fallback_locale == "en" else
                       "本轮未能完成剩余步骤；已经完成的结果仍保留如下。\n")
        if managed.diagnostics:
            # Reply generation needs the outcome, not internal exception bodies.
            context = {**(context or {}), "recovery_outcomes": [
                {"stage": observation.stage, "status": observation.status.value,
                 "code": observation.detail["code"]}
                for observation in managed.diagnostics]}
        assembled = await self._assembler.assemble(
            board,
            current_message=state["observations"].raw_text,
            system_notice=notice,
            conversation_context=context,
            # An information request is not a second approval presentation.
            # The existing approval stays in ConversationState unchanged.
            pending_approval=pending_approval if present_approval else None,
            requested_inputs=questions,
            # An undelivered interaction must be authored from its persisted
            # scope, not from a planner reply that did not present that scope.
            response_candidate=None if present_approval or present_input else managed.plan.response_text,
        )
        assembled = replace(assembled, diagnostics=managed.diagnostics + assembled.diagnostics)
        return {"assembled": assembled}

    async def execute(
        self,
        invocation: InvocationIdentity,
        observations: TurnObservations,
        *, execution_context: dict | None = None,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
    ) -> TurnRuntimeResult:
        key = str(invocation.invocation_key)
        config = {"callbacks": list(self._callbacks), "run_name": "customer_service_turn",
                  "recursion_limit": 10 + 6 * (self._max_observation_steps + 1),
                  "metadata": {"invocation_key": key, "langfuse_session_id": str(invocation.conversation_id)}}
        if self._checkpointer is not None:
            config["configurable"] = {"thread_id": f"turn:{key}"}
        graph_input = {
            "runtime_version": self.version,
            "invocation": invocation,
            "invocation_key": key,
            "observations": observations,
            "execution_context": dict(execution_context or {}),
            "preparation_options": {"recent_relevant_turns": recent_relevant_turns,
                                    "evidence_refs": evidence_refs, "token_budget": token_budget},
        }
        if self._checkpointer is not None:
            snapshot = await self.graph.aget_state(config)
            if snapshot.values:
                saved_context = snapshot.values.get("execution_context", {})
                if saved_context.get("authorization_fingerprint", "") != (execution_context or {}).get("authorization_fingerprint", ""):
                    raise TurnRuntimeError("turn authorization changed")
                if snapshot.values.get("invocation_key") != key:
                    raise TurnRuntimeError("turn checkpoint belongs to another invocation")
                prepared = snapshot.values.get("prepared")
                completed = snapshot.values.get("managed") is not None and not snapshot.next
                if (
                    snapshot.values.get("runtime_version") != self.version
                    or (prepared is not None and prepared.artifact_version != "prepared-turn-v2")
                ):
                    raise TurnCheckpointVersionError("unpublished checkpoint requires explicit lifecycle migration")
                if completed:
                    return TurnRuntimeResult(
                        snapshot.values["managed"],
                        snapshot.values.get("assembled"),
                    )
                graph_input = None
        with (propagate_attributes(session_id=str(invocation.conversation_id))
              if self._callbacks else nullcontext()):
            result = await self.graph.ainvoke(graph_input, config=config)
        return TurnRuntimeResult(result["managed"], result.get("assembled"))
