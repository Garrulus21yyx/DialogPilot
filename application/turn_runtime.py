"""Checkpointed turn phases over the existing conversation and WorkPlan owners."""
from __future__ import annotations
from contextlib import nullcontext
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


@dataclass(frozen=True)
class TurnRuntimeResult:
    managed: ManagedTurnResult
    assembled: AssembledResponse | None


class TurnRuntime:
    """Coordinate durable turn phases without owning their domain semantics."""

    version = "turn-runtime-v8-response-or-work"

    def __init__(
        self,
        manager: TargetConversationManager,
        response_assembler: ResponseAssembler,
        callbacks=(),
        *,
        checkpointer=None,
        interaction_published=None,
    ) -> None:
        self._manager = manager
        self._assembler = response_assembler
        self._callbacks = callbacks
        self._checkpointer = checkpointer
        self._interaction_published = interaction_published
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
        ):
            builder.add_node(name, node)
        builder.add_edge(START, "prepare_turn")
        builder.add_edge("prepare_turn", "execute_work_plan")
        builder.add_edge("execute_work_plan", "commit_progress")
        builder.add_edge("commit_progress", "resolve_followup")
        builder.add_edge("resolve_followup", "assemble_response")
        builder.add_edge("assemble_response", "commit_turn_state")
        builder.add_edge("commit_turn_state", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def _prepare_turn(self, state: TurnGraphState):
        prepared = await self._manager.prepare(
            state["invocation"], state["observations"],
            execution_context=state.get("execution_context", {}),
        )
        return {"prepared": prepared}

    async def _execute_work_plan(self, state: TurnGraphState):
        return {"managed": await self._manager.execute(state["prepared"])}

    async def _commit_turn_state(self, state: TurnGraphState):
        await self._manager.commit(state["managed"])
        return {}

    async def _commit_progress(self, state: TurnGraphState):
        await self._manager.commit_progress(state["managed"])
        return {}

    async def _resolve_followup(self, state: TurnGraphState):
        return {"managed": await self._manager.resolve_followup(state["prepared"], state["managed"])}

    @property
    def supports_resume(self) -> bool:
        return self._checkpointer is not None

    async def _assemble_response(self, state: TurnGraphState):
        managed = state["managed"]
        board = managed.board
        if managed.plan.response_text is not None:
            # A conversational reply neither presents nor consumes an existing
            # wait. Verify the model's candidate through the same reply boundary.
            context = conversation_context_payload(state["prepared"].context) or {}
            context = {**context, "turn_contract": "RESPONSE_ONLY_NO_STATE_CHANGE",
                "pending_interaction": managed.state_after.pending_interaction is not None,
                "pending_approval": managed.state_after.pending_approval is not None}
            assembled = await self._assembler.assemble(
                None, current_message=state["observations"].raw_text,
                conversation_context=context, response_candidate=managed.plan.response_text)
            return {"assembled": assembled}
        pending_input = managed.state_after.pending_interaction
        questions = ()
        if (pending_input is not None and (
                managed.state_before.pending_interaction is None
                or (pending_input.interaction_id, pending_input.version) != (
                    managed.state_before.pending_interaction.interaction_id,
                    managed.state_before.pending_interaction.version)
                or self._interaction_published is not None and not self._interaction_published(
                    state["invocation"], signal_id=pending_input.interaction_id, signal_version=pending_input.version))):
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
        pending_approval = managed.state_after.pending_approval
        previous_approval = managed.state_before.pending_approval
        present_approval = pending_approval is not None and (
            previous_approval is None or
            (pending_approval.approval_id, pending_approval.version) !=
            (previous_approval.approval_id, previous_approval.version) or
            (self._interaction_published is not None and not self._interaction_published(
                state["invocation"], signal_id=pending_approval.approval_id,
                signal_version=pending_approval.version)))
        if board is None and not questions and not present_approval:
            return {"assembled": None}
        context = conversation_context_payload(state["prepared"].context)
        if managed.diagnostics:
            # Reply generation needs the outcome, not internal exception bodies.
            context = {**(context or {}), "recovery_outcomes": [
                {"stage": observation.stage, "status": observation.status.value,
                 "code": observation.detail["code"]}
                for observation in managed.diagnostics]}
        if pending_approval is not None and not present_approval:
            context = {**(context or {}), "retained_approval": {
                "action_ref": pending_approval.action_ref,
                "arguments": {arg.name: arg.value for arg in pending_approval.arguments},
                "status": "AWAITING_DECISION_NOT_EXECUTED",
                "presentation": "Already presented; answer this turn without soliciting approval again.",
            }}
        assembled = await self._assembler.assemble(
            board,
            current_message=state["observations"].raw_text,
            system_notice=notice,
            conversation_context=context,
            # An information request is not a second approval presentation.
            # The existing approval stays in ConversationState unchanged.
            pending_approval=pending_approval if present_approval else None,
            requested_inputs=questions,
        )
        assembled = replace(assembled, diagnostics=managed.diagnostics + assembled.diagnostics)
        return {"assembled": assembled}

    async def execute(
        self,
        invocation: InvocationIdentity,
        observations: TurnObservations,
        *, execution_context: dict | None = None,
    ) -> TurnRuntimeResult:
        key = str(invocation.invocation_key)
        config = {"callbacks": list(self._callbacks), "run_name": "customer_service_turn",
                  "metadata": {"invocation_key": key, "langfuse_session_id": str(invocation.conversation_id)}}
        if self._checkpointer is not None:
            config["configurable"] = {"thread_id": f"turn:{key}"}
        graph_input = {
            "runtime_version": self.version,
            "invocation": invocation,
            "invocation_key": key,
            "observations": observations,
            "execution_context": dict(execution_context or {}),
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
