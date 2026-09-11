"""LangGraph parent runtime for direct, single-agent, and multi-agent work."""
from __future__ import annotations

import time
import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Annotated, Awaitable, Callable, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.errors import NodeCancelledError
from langgraph.types import Command, Overwrite, Send, interrupt

from application.agent_result import (
    merge_facts as _merge_facts,
    AgentResult,
    AgentResultStatus,
    EvidenceRequest,
    FactRecord,
)
from application.result_board import ResultBoard, ResultBoardSnapshot, current_facts
from application.work_item import ActionSerialization, ControlMode, WorkItem, WorkPlan
from application.work_control import WorkControlGuard, WorkSuperseded
from application.conversation_state import PendingApprovalState
from application.capability_registry import CapabilityEffect


class OrchestrationRuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class PlanScopedAgentResult:
    """Graph-state event identity for one result inside one accepted WorkPlan."""

    execution_id: str
    result: AgentResult

    def __post_init__(self) -> None:
        if not str(self.execution_id or "").strip():
            raise OrchestrationRuntimeError("result execution identity is required")

def _scope_result(plan: WorkPlan, result: AgentResult) -> PlanScopedAgentResult:
    if not isinstance(result, AgentResult):
        raise OrchestrationRuntimeError("worker must return an AgentResult")
    item = next((item for item in plan.items if item.work_item_id == result.work_item_id), None)
    if item is None or item.owner_agent != result.owner_agent:
        raise OrchestrationRuntimeError("result is outside the scoped work plan")
    return PlanScopedAgentResult(_execution_result_id(plan, item), result)


def _scope_results(plan: WorkPlan, results) -> list[PlanScopedAgentResult]:
    return [_scope_result(plan, result) for result in results]


def _agent_results(plan: WorkPlan, results) -> tuple[AgentResult, ...]:
    validated = []
    for record in results:
        if not isinstance(record, PlanScopedAgentResult):
            raise OrchestrationRuntimeError("graph results require plan-scoped identities")
        if record != _scope_result(plan, record.result):
            raise OrchestrationRuntimeError("result execution identity differs from work plan")
        validated.append(record.result)
    return tuple(validated)


def _execution_result_id(plan: WorkPlan, item: WorkItem) -> str:
    return f"{plan.fingerprint}:{item.work_item_id}:{item.fingerprint}"


def _checkpoint_outcomes(state):
    """Keep local result IDs paired with the WorkItems from their own graph."""
    _validate_checkpoint(state)
    results = {result.work_item_id: result for result in _agent_results(state["work_plan"], state.get("agent_results", ()))}
    return _merge_checkpoint_outcomes(tuple(state.get("retained_outcomes", ())), tuple(
        (item, results.get(item.work_item_id)) for item in state["work_plan"].items))


def _merge_checkpoint_outcomes(left, right):
    outcomes = list(left)
    for item, result in right:
        same = next(((original, previous) for original, previous in outcomes
                     if original == item or (item.control is not None and original.control == item.control)), None)
        if same is None:
            outcomes.append((item, result))
        elif same != (item, result):
            raise OrchestrationRuntimeError("checkpoint progress conflicts for the same goal revision")
    return tuple(outcomes)


def _unreplaced_outcomes(previous, plan):
    """One revision rule for fresh observation graphs and resumed graphs."""
    return plan.unreplaced_outcomes(previous)


def _merge_agent_results(left, right):
    """Reducer for one plan-bound execution stream.

    LangGraph may replay a completed update after checkpoint recovery. The same
    result for the same runnable work is idempotent; a different result for that
    identity is a checkpoint/executor conflict and must not be hidden by order.
    """
    merged: dict[str, object] = {}
    order: list[str] = []
    for result in (*tuple(left or ()), *tuple(right or ())):
        if not isinstance(result, PlanScopedAgentResult):
            raise OrchestrationRuntimeError("graph results require plan-scoped identities")
        key = result.execution_id
        prior = merged.get(key)
        if prior is None:
            merged[key] = result
            order.append(key)
        elif prior != result:
            raise OrchestrationRuntimeError(
                "conflicting results for the same plan-bound work item"
            )
    return [merged[key] for key in order]


@dataclass(frozen=True)
class AgentContextView:
    work_item: WorkItem
    current_message: str
    verified_facts: tuple[FactRecord, ...]
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    trusted_context: Mapping[str, object] = field(default_factory=dict)
    dependency_results: tuple[AgentResult, ...] = ()
    working_messages: tuple[dict, ...] = ()
    pending_approval: PendingApprovalState | None = None
    working_state: dict = field(default_factory=dict)

    def __post_init__(self):
        # Origin is supplied by the runtime, never by a model tool argument.
        object.__setattr__(self, "trusted_context", {**self.trusted_context,
            "original_user_message": self.current_message})
        for name in ("verified_facts", "recent_relevant_turns", "evidence_refs", "dependency_results", "working_messages"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


class WorkExecutor(Protocol):
    async def __call__(self, context: AgentContextView) -> AgentResult: ...


class EvidenceResolver(Protocol):
    async def __call__(
        self,
        request: EvidenceRequest,
        context: AgentContextView,
    ) -> tuple[FactRecord, ...]: ...


class ParentGraphState(TypedDict, total=False):
    work_plan: WorkPlan
    work_plan_fingerprint: str
    current_message: str
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    ready_items: tuple[WorkItem, ...]
    agent_results: Annotated[list[PlanScopedAgentResult], _merge_agent_results]
    facts: tuple[FactRecord, ...]
    board: ResultBoardSnapshot
    trusted_context: Mapping[str, object]
    interrupt_after_completion: bool
    continuation_facts: dict[str, tuple[FactRecord, ...]]
    continuation_messages: dict[str, tuple[dict, ...]]
    continuation_states: dict[str, dict]
    pending_approval: PendingApprovalState | None
    retained_outcomes: tuple[tuple[WorkItem, AgentResult | None], ...]
    accepted_observed_outcomes: tuple[tuple[WorkItem, AgentResult | None], ...]
    imported_source_threads: tuple[str, ...]
    retain_wait: bool


class WorkerState(TypedDict):
    work_item: WorkItem
    work_plan: WorkPlan
    current_message: str
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    facts: tuple[FactRecord, ...]
    trusted_context: Mapping[str, object]
    dependency_results: tuple[AgentResult, ...]
    working_messages: tuple[dict, ...]
    pending_approval: PendingApprovalState | None
    working_state: dict


class OrchestrationRuntime:
    """Build one static graph; runtime WorkPlans determine fan-out and waves."""

    def __init__(
        self,
        *,
        direct_executor: WorkExecutor,
        domain_workers: Mapping[str, WorkExecutor],
        workflow_executor: WorkExecutor | None = None,
        result_board: ResultBoard | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        checkpointer=None,
        control_guard: WorkControlGuard | None = None,
    ) -> None:
        self._direct_executor = direct_executor
        self._domain_workers = dict(domain_workers)
        self._workflow_executor = workflow_executor
        self._result_board = result_board or ResultBoard()
        self._evidence_resolver = evidence_resolver
        self._checkpointer = checkpointer
        self._control_guard = control_guard
        # Process-local execution observations, never routing or business authority.
        self._outcome_counts: dict[str, Counter[str]] = {}
        self._elapsed_ms: dict[str, float] = {}
        self.graph = self._build_graph()

    def get_stats(self) -> dict[str, dict[str, object]]:
        """Count completed worker invocations, not unique goals or answer quality."""
        stats = {}
        for owner, counts in self._outcome_counts.items():
            total = sum(counts.values())
            samples = sum(counts[status] for status in (
                "SUCCEEDED", "PARTIAL", "RETRYABLE_FAILURE", "TERMINAL_FAILURE",
            ))
            stats[owner] = {
                "total": total,
                "outcome_counts": dict(counts),
                "outcome_samples": samples,
                "success_rate": counts["SUCCEEDED"] / samples if samples else None,
                "avg_ms": self._elapsed_ms[owner] / total,
                "scope": "process_worker_invocations",
            }
        return stats

    def _build_graph(self):
        builder = StateGraph(ParentGraphState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("execute_work_item", self._execute_work_item)
        builder.add_node("merge_results", self._merge_results)
        builder.add_node("finish", self._finish)
        builder.add_edge(START, "initialize")
        builder.add_conditional_edges(
            "initialize", self._dispatch, ["execute_work_item", "finish"],
        )
        builder.add_edge("execute_work_item", "merge_results")
        builder.add_conditional_edges(
            "merge_results", self._next_step,
            ["execute_work_item", "await_resume", "finish"],
        )
        builder.add_node("await_resume", self._await_resume)
        builder.add_conditional_edges(
            "await_resume", lambda state: "await_resume" if state.get("retain_wait") else self._dispatch(state),
            ["execute_work_item", "finish", "await_resume"],
        )
        builder.add_edge("finish", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def _initialize(self, state: ParentGraphState):
        plan = state["work_plan"]
        board = self._result_board.evaluate(
            plan, (), retained_outcomes=tuple(state.get("retained_outcomes", ())))
        return {
            "ready_items": board.ready_items,
            "agent_results": [],
            "facts": board.facts,
            "board": board,
        }

    @staticmethod
    def _ready_wave(state: ParentGraphState):
        # One conversation approval slot: serialize action-capable workers,
        # while independent read-only work remains parallel.
        plan = state["work_plan"]
        results = _agent_results(plan, state.get("agent_results", ()))
        if plan.policy.action_serialization is ActionSerialization.NONE:
            return tuple(state.get("ready_items", ()))
        action_busy = any(result.status is AgentResultStatus.WAITING_APPROVAL
                          for result in results)
        ready = []
        for item in state.get("ready_items", ()):
            action_capable = bool(item.allowed_actions) or item.control_mode in {
                ControlMode.ACTION, ControlMode.WORKFLOW}
            if action_capable:
                if action_busy:
                    continue
                action_busy = True
            ready.append(item)
        return tuple(ready)

    def _dispatch(self, state: ParentGraphState):
        from application.agent_working_state import recovery_context
        ready = self._ready_wave(state)
        if not ready:
            return "finish"
        results = {
            result.work_item_id: result
            for result in _agent_results(state["work_plan"], state.get("agent_results", ()))
        }
        now = datetime.now(timezone.utc)
        prior = state.get("accepted_observed_outcomes", ())
        recovered = {item.work_item_id: recovery_context(state["work_plan"], item, prior) for item in ready}
        return [
            Send("execute_work_item", {
                "work_item": item,
                "work_plan": state["work_plan"],
                "current_message": state.get("current_message", ""),
                "recent_relevant_turns": state.get("recent_relevant_turns", ()),
                "evidence_refs": state.get("evidence_refs", ()),
                "token_budget": state.get("token_budget", 6000),
                "facts": current_facts(_merge_facts(
                    tuple(fact for fact in recovered[item.work_item_id].get("facts", ())
                          if fact.valid_until is None or fact.valid_until > now),
                    tuple(fact for dependency in item.dependencies for fact in results[dependency].facts),
                    tuple(fact for source in state["work_plan"].reassignment_sources(
                        item, state.get("accepted_observed_outcomes", ())) for fact in source.facts
                        if fact.valid_until is None or fact.valid_until > now),
                    # Direct observations are explicit conversation-level reads
                    # (e.g. identifying the account before delegation). Domain
                    # investigations stay local unless a DAG dependency or a
                    # continuation explicitly carries them into this worker.
                    tuple(fact for work, result in state.get("retained_outcomes", ())
                          if result and work.control_mode is ControlMode.DIRECT for fact in result.facts),
                    state.get("continuation_facts", {}).get(item.work_item_id, ()))),
                "trusted_context": {**state.get("trusted_context", {}),
                    "dependency_work_ids": state["work_plan"].dependency_closure(item), "assignment_view": {
                    "assignments": [{"work_item_id": work.work_item_id, "owner_agent": work.owner_agent,
                        "objective": work.objective, "allows_action_preparation": bool(work.allowed_actions),
                        "dependencies": list(work.dependencies),
                        "status": results[work.work_item_id].status.value if work.work_item_id in results else "PENDING"}
                        for work in state["work_plan"].items],
                    "retained_outcomes": [{"work_item_id": work.work_item_id, "owner_agent": work.owner_agent,
                        "objective": work.objective, "status": result.status.value if result else "PENDING"}
                        for work, result in state.get("retained_outcomes", ())],
                }},
                "pending_approval": state.get("pending_approval"),
                "dependency_results": tuple(
                    results[dependency]
                    for dependency in item.dependencies
                ),
                "working_messages": state.get("continuation_messages", {}).get(item.work_item_id,
                    recovered[item.work_item_id].get("working_messages", ())),
                "working_state": state.get("continuation_states", {}).get(item.work_item_id,
                    recovered[item.work_item_id].get("working_state", {})),
            })
            for item in ready
        ]

    def _next_step(self, state: ParentGraphState):
        if self._ready_wave(state):
            return self._dispatch(state)
        if self._checkpointer is not None and (
            any(
                item.status in {AgentResultStatus.NEEDS_USER_INPUT, AgentResultStatus.WAITING_APPROVAL}
                for item in _agent_results(state["work_plan"], state.get("agent_results", ()))
            )
            or bool(state.get("interrupt_after_completion"))
        ):
            return "await_resume"
        return "finish"

    async def _await_resume(self, state: ParentGraphState):
        missing = tuple(
            field
            for result in _agent_results(state["work_plan"], state.get("agent_results", ()))
            if result.status is AgentResultStatus.NEEDS_USER_INPUT
            for field in result.missing_inputs
            if field.required
        )
        resumed = interrupt({
            "kind": "FIELDS" if missing else "APPROVAL",
            "requested_fields": [
                {
                    "target_work_item_id": item.target_work_item_id,
                    "field_name": item.field_name,
                    "value_schema": item.value_schema,
                }
                for item in missing
            ],
            "work_plan_fingerprint": state["work_plan_fingerprint"],
        })
        if not isinstance(resumed, Mapping):
            raise OrchestrationRuntimeError("resume payload must be an object")
        closed = tuple(resumed.get("closed_work_items", ()))
        previous = _checkpoint_outcomes(state)
        imported = tuple(resumed.get("imported_outcomes", ()))
        checkpoint_items = tuple(item for item, _ in (*previous, *imported))
        if any(item not in checkpoint_items for item in closed):
            raise OrchestrationRuntimeError("closed objective does not match checkpoint")
        if resumed.get("cancel") is True:
            # Closing an execution wait does not undo completed work or cancel
            # a remote action. Give only unstarted work a terminal outcome so
            # queued items cannot leave the graph in an unfinished state.
            returned = {result.work_item_id: result for result in _agent_results(state["work_plan"], state.get("agent_results", ()))}
            retain = bool(resumed.get("retain_wait"))
            results = [_closed_outcome(item, returned.get(item.work_item_id))
                if item in closed or not retain and item.work_item_id not in returned else returned.get(item.work_item_id)
                for item in state["work_plan"].items]
            results = [result for result in results if result is not None]
            retained = tuple((item, _closed_outcome(item, result) if item in closed else result)
                             for item, result in state.get("retained_outcomes", ()))
            return {
                "agent_results": Overwrite(_scope_results(state["work_plan"], results)),
                "retained_outcomes": retained,
                "board": self._evaluate({**state, "agent_results": _scope_results(state["work_plan"], results), "retained_outcomes": retained}),
                "interrupt_after_completion": retain,
                "retain_wait": retain,
                "ready_items": (),
            }
        plan = resumed.get("work_plan")
        if not isinstance(plan, WorkPlan):
            raise OrchestrationRuntimeError("resume payload requires a validated WorkPlan")
        previous = _merge_checkpoint_outcomes(previous, imported)
        # A resume executes a subset, but cannot erase other outcomes from the
        # original request. These are checkpoint projections, never runnable work.
        retained = tuple((item, _closed_outcome(item, result) if item in closed else result)
            for item, result in _unreplaced_outcomes(previous, plan))
        preserved = tuple(result for item, result in previous if item in plan.items and result is not None)
        board = self._result_board.evaluate(plan, preserved, retained_outcomes=retained)
        progress = {}
        messages = {}
        working_states = {}
        now = datetime.now(timezone.utc)
        for item in plan.items:
            if any(original == item for original, _ in previous):
                continue
            if item.continuation_of is None:
                continue
            candidates = tuple((original, result) for original, result in previous
                if original.work_item_id == item.continuation_of
                and original.control is not None and item.control is not None
                and original.control.control_id == item.control.control_id
                and original.control.revision + 1 == item.control.revision)
            if len(candidates) != 1:
                raise OrchestrationRuntimeError("continuation does not match checkpoint progress")
            original, result = candidates[0]
            if (original.owner_agent != item.owner_agent
                    or original.registry_fingerprint != item.registry_fingerprint):
                raise OrchestrationRuntimeError("continuation capability scope differs from checkpoint")
            if result is None:
                # The prior checkpoint retained this queued, not-yet-run item.
                continue
            progress[item.work_item_id] = tuple(fact for fact in result.facts
                if fact.valid_until is None or fact.valid_until > now)
            messages[item.work_item_id] = tuple(result.working_messages)
            from application.agent_working_state import working_state
            working_states[item.work_item_id] = working_state(result.working_state)
        return {
            "work_plan": plan,
            "continuation_facts": progress,
            "continuation_messages": messages,
            "continuation_states": working_states,
            "work_plan_fingerprint": _work_plan_fingerprint(plan),
            "current_message": str(resumed.get("current_message") or ""),
            "recent_relevant_turns": tuple(resumed.get("recent_relevant_turns") or ()),
            "evidence_refs": tuple(resumed.get("evidence_refs") or ()),
            "token_budget": int(resumed.get("token_budget") or 6000),
            "trusted_context": dict(resumed.get("trusted_context") or {}),
            "pending_approval": resumed.get("pending_approval"),
            "interrupt_after_completion": bool(resumed.get("interrupt_after_completion", False)),
            "agent_results": Overwrite(value=_scope_results(plan, preserved)),
            "facts": board.facts,
            "ready_items": board.ready_items,
            "board": board,
            "retained_outcomes": retained,
            "imported_source_threads": tuple(dict.fromkeys(
                (*state.get("imported_source_threads", ()), *resumed.get("source_thread_ids", ())))),
            "retain_wait": False,
            "accepted_observed_outcomes": tuple(resumed.get("observed_outcomes", ())),
        }

    async def _execute_work_item(self, state: WorkerState):
        if not isinstance(state.get("work_plan"), WorkPlan) or state["work_item"] not in state["work_plan"].items:
            raise OrchestrationRuntimeError("worker requires its accepted work plan")
        started = time.perf_counter()
        try:
            update = await self._run_work_item(state)
        except (OrchestrationRuntimeError, NodeCancelledError):
            raise  # Broken execution contracts are not ordinary worker outages.
        except Exception as exc:
            if state["work_item"].effect is CapabilityEffect.WRITE:
                # Ledger/checkpoint recovery owns unknown write effects.
                raise
            update = {"agent_results": [_worker_failure(state["work_item"], exc)]}
        result = update["agent_results"][0]
        owner = state["work_item"].owner_agent
        self._outcome_counts.setdefault(owner, Counter())[result.status.value] += 1
        self._elapsed_ms[owner] = (
            self._elapsed_ms.get(owner, 0.0) + (time.perf_counter() - started) * 1000
        )
        update = {**update, "agent_results": _scope_results(state["work_plan"], (result,))}
        return update

    async def _run_work_item(self, state: WorkerState):
        item = state["work_item"]
        context = AgentContextView(
            item,
            state["current_message"],
            state["facts"],
            state["recent_relevant_turns"],
            state["evidence_refs"],
            state["token_budget"],
            state.get("trusted_context", {}),
            state.get("dependency_results", ()),
            state.get("working_messages", ()),
            state.get("pending_approval"),
            state.get("working_state", {}),
        )
        if item.effect is CapabilityEffect.READ and self._control_guard is not None and not self._control_guard.is_current(
            item, context.trusted_context,
        ):
            return {"agent_results": [self._control_guard.superseded_result(item)]}
        if item.control_mode is ControlMode.DIRECT:
            executor = self._direct_executor
        elif (
            item.control_mode in {ControlMode.WORKFLOW, ControlMode.ACTION}
            and self._workflow_executor is not None
        ):
            executor = self._workflow_executor
        else:
            try:
                executor = self._domain_workers[item.owner_agent]
            except KeyError as exc:
                raise OrchestrationRuntimeError(
                    f"no domain worker registered for {item.owner_agent}"
                ) from exc
        try:
            if item.control_mode is ControlMode.DIRECT:
                async with asyncio.timeout(item.timeout_seconds):
                    result = await self._execute_with_evidence(executor, context)
            else:
                result = await self._execute_with_evidence(executor, context)
        except WorkSuperseded:
            result = WorkControlGuard.superseded_result(item)
        if item.effect is CapabilityEffect.READ and self._control_guard is not None and not self._control_guard.is_current(
            item, context.trusted_context,
        ):
            result = self._control_guard.superseded_result(item)
        return {"agent_results": [result]}

    async def _execute_with_evidence(
        self,
        executor: WorkExecutor,
        context: AgentContextView,
    ) -> AgentResult:
        resolved_facts: tuple[FactRecord, ...] = ()
        seen_requests: set[tuple[str, tuple[str, ...]]] = set()
        for _attempt in range(context.work_item.max_steps):
            if context.work_item.effect is CapabilityEffect.READ and self._control_guard is not None:
                self._control_guard.ensure_current(
                    context.work_item, context.trusted_context,
                )
            current = replace(
                context,
                verified_facts=current_facts(_merge_facts(
                    context.verified_facts,
                    resolved_facts,
                )),
            )
            result = await executor(current)
            if result.status is not AgentResultStatus.NEEDS_EVIDENCE:
                return replace(
                    result,
                    facts=_merge_facts(resolved_facts, result.facts),
                )
            if context.work_item.control_mode is not ControlMode.DELEGATED:
                return result
            requested = tuple(
                request for request in result.requested_evidence
                if (request.requirement_id, request.preferred_providers)
                not in seen_requests
            )
            if not requested:
                return replace(
                    result,
                    facts=_merge_facts(resolved_facts, result.facts),
                )
            found = []
            available = current_facts(_merge_facts(current.verified_facts, result.facts))
            for request in requested:
                seen_requests.add((
                    request.requirement_id,
                    request.preferred_providers,
                ))
                existing = tuple(
                    fact for fact in available
                    if fact.requirement_id == request.requirement_id
                )
                if existing:
                    found.extend(existing)
                    continue
                if self._evidence_resolver is None:
                    continue
                produced = tuple(await self._evidence_resolver(request, current))
                if any(
                    fact.requirement_id != request.requirement_id
                    for fact in produced
                ):
                    raise OrchestrationRuntimeError(
                        "evidence resolver returned another requirement"
                    )
                found.extend(produced)
            next_facts = _merge_facts(resolved_facts, tuple(found))
            if next_facts == resolved_facts:
                return replace(
                    result,
                    facts=_merge_facts(resolved_facts, result.facts),
                )
            resolved_facts = next_facts
        return replace(
            result,
            facts=_merge_facts(resolved_facts, result.facts),
        )

    async def _merge_results(self, state: ParentGraphState):
        board = self._evaluate(state)
        return {
            "agent_results": _scope_results(state["work_plan"], board.blocked_results),
            "facts": board.facts,
            "ready_items": board.ready_items,
            "board": board,
        }

    async def _finish(self, state: ParentGraphState):
        board = self._evaluate(state)
        if not board.complete:
            raise OrchestrationRuntimeError("work graph stopped before reaching terminal outcomes")
        return {"board": board, "facts": board.facts, "ready_items": ()}

    def _evaluate(self, state):
        return self._result_board.evaluate(
            state["work_plan"], _agent_results(state["work_plan"], state.get("agent_results", ())),
            retained_outcomes=tuple(state.get("retained_outcomes", ())))

    async def execute(
        self,
        work_plan: WorkPlan,
        *,
        current_message: str,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
        thread_id: str | None = None,
        trusted_context: Mapping[str, object] | None = None,
        interrupt_after_completion: bool = False,
        pending_approval: PendingApprovalState | None = None,
        retained_outcomes: tuple[tuple[WorkItem, AgentResult | None], ...] = (),
    ) -> ResultBoardSnapshot:
        if self._checkpointer is not None and not str(thread_id or "").strip():
            raise OrchestrationRuntimeError("checkpointed execution requires thread_id")
        config = (
            {"configurable": {"thread_id": thread_id}}
            if thread_id is not None else None
        )
        graph_input = {
            "work_plan": work_plan,
            "work_plan_fingerprint": _work_plan_fingerprint(work_plan),
            "current_message": current_message,
            "recent_relevant_turns": recent_relevant_turns,
            "evidence_refs": evidence_refs,
            "token_budget": token_budget,
            "agent_results": [],
            "facts": (),
            "trusted_context": dict(trusted_context or {}),
            "interrupt_after_completion": bool(interrupt_after_completion),
            "pending_approval": pending_approval,
            "retained_outcomes": _unreplaced_outcomes(retained_outcomes, work_plan),
            "accepted_observed_outcomes": tuple(retained_outcomes),
        }
        if self._checkpointer is not None:
            snapshot = await self.graph.aget_state(config)
            if snapshot.values:
                _validate_checkpoint(snapshot.values)
                if not _checkpoint_matches_work_plan(
                    snapshot.values.get("work_plan_fingerprint"), work_plan,
                ):
                    raise OrchestrationRuntimeError(
                        "checkpoint thread is bound to another work plan"
                    )
                saved_outcomes = tuple(tuple(pair) for pair in snapshot.values.get("accepted_observed_outcomes", ()))
                if saved_outcomes != tuple(retained_outcomes):
                    raise OrchestrationRuntimeError("checkpoint thread is bound to different observed outcomes")
                board = snapshot.values.get("board")
                if board is not None and board.complete:
                    return board
                graph_input = None
        result = await self.graph.ainvoke(graph_input, config=config)
        return result["board"]

    @property
    def supports_resume(self) -> bool:
        return self._checkpointer is not None

    async def resume(
        self,
        work_plan: WorkPlan,
        *,
        current_message: str,
        thread_id: str,
        interrupt_after_completion: bool = False,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
        trusted_context: Mapping[str, object] | None = None,
        pending_approval: PendingApprovalState | None = None,
        closed_work_items: tuple[WorkItem, ...] = (),
        source_thread_ids: tuple[str, ...] = (),
        retained_outcomes: tuple[tuple[WorkItem, AgentResult | None], ...] = (),
    ) -> ResultBoardSnapshot:
        if self._checkpointer is None:
            raise OrchestrationRuntimeError("resume requires a checkpointer")
        if len(source_thread_ids) > 1 or thread_id in source_thread_ids:
            raise OrchestrationRuntimeError("resume supports only the conversation's two distinct waits")
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.graph.aget_state(config)
        _validate_checkpoint(snapshot.values)
        if _checkpoint_matches_work_plan(snapshot.values.get("work_plan_fingerprint"), work_plan):
            if set(source_thread_ids).difference(snapshot.values.get("imported_source_threads", ())):
                raise OrchestrationRuntimeError("checkpoint replay cannot add an unaccepted source")
            # This continuation was already accepted before its caller stopped.
            # Reuse the normal checkpoint replay, not a second resume signal.
            return await self.execute(
                work_plan, current_message=current_message, thread_id=thread_id,
                interrupt_after_completion=interrupt_after_completion,
                recent_relevant_turns=recent_relevant_turns, evidence_refs=evidence_refs,
                token_budget=token_budget, trusted_context=trusted_context,
                pending_approval=pending_approval, retained_outcomes=retained_outcomes)
        if not snapshot.tasks or not any(task.interrupts for task in snapshot.tasks):
            raise OrchestrationRuntimeError("checkpoint thread is not interrupted")
        self._result_board.evaluate(work_plan, (), retained_outcomes=retained_outcomes)
        if any(item.registry_fingerprint != work_plan.items[0].registry_fingerprint
               for item, _ in retained_outcomes):
            raise OrchestrationRuntimeError("observed outcome registry differs from plan")
        imported = tuple(retained_outcomes)
        from application.action_approval import merge_action_decisions
        decisions = tuple(snapshot.values.get("trusted_context", {}).get("action_decisions", ()))
        for source in source_thread_ids:
            if source in snapshot.values.get("imported_source_threads", ()):
                continue
            other = await self.graph.aget_state({"configurable": {"thread_id": source}})
            if not other.tasks or not any(task.interrupts for task in other.tasks):
                raise OrchestrationRuntimeError("source checkpoint is not interrupted")
            scope = snapshot.values.get("trusted_context", {})
            other_scope = other.values.get("trusted_context", {})
            if (any(not scope.get(key) for key in ("tenant_id", "user_id", "conversation_id"))
                    or any(scope.get(key) != other_scope.get(key)
                   for key in ("tenant_id", "user_id", "conversation_id", "conv_id"))):
                raise OrchestrationRuntimeError("source checkpoint belongs to another conversation")
            outcomes = _checkpoint_outcomes(other.values)
            if any(item.registry_fingerprint != work_plan.items[0].registry_fingerprint for item, _ in outcomes):
                raise OrchestrationRuntimeError("source checkpoint registry differs from plan")
            if not any(original in closed_work_items or any(
                original.control and item.control
                and original.control.control_id == item.control.control_id
                and original.control.revision + 1 == item.control.revision
                for item in work_plan.items) for original, _ in outcomes):
                raise OrchestrationRuntimeError("source checkpoint has no goal in the accepted plan")
            imported = _merge_checkpoint_outcomes(imported, outcomes)
            decisions = merge_action_decisions(decisions, other_scope.get("action_decisions", ()))
        resumed_context = dict(trusted_context or {})
        resumed_context["action_decisions"] = merge_action_decisions(
            decisions, resumed_context.get("action_decisions", ()))
        # Explicitly replaced goals start a new decision scope. A write item
        # executing the old proposal is not a replacement of its parent goal.
        revised = {item.control.control_id for item in work_plan.items
                   if item.control and item.continuation_of is None and not item.operation_key}
        resumed_context["action_decisions"] = tuple(
            decision for decision in resumed_context["action_decisions"]
            if decision["control_id"] not in revised)
        result = await self.graph.ainvoke(Command(resume={
            "work_plan": work_plan,
            "interrupt_after_completion": interrupt_after_completion,
            "current_message": current_message,
            "recent_relevant_turns": recent_relevant_turns,
            "evidence_refs": evidence_refs,
            "token_budget": token_budget,
            "trusted_context": resumed_context,
            "pending_approval": pending_approval,
            "closed_work_items": closed_work_items,
            "imported_outcomes": imported,
            "observed_outcomes": tuple(retained_outcomes),
            "source_thread_ids": source_thread_ids,
        }), config=config, durability="sync")
        return result["board"]

    async def cancel_interrupt(self, *, thread_id: str, closed_work_items: tuple[WorkItem, ...] = (), retain_wait: bool = False):
        if self._checkpointer is None:
            return
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            _validate_checkpoint(snapshot.values)
        if snapshot.tasks and any(task.interrupts for task in snapshot.tasks):
            result = await self.graph.ainvoke(
                Command(resume={"cancel": True, "closed_work_items": closed_work_items,
                                "retain_wait": retain_wait}), config=config, durability="sync",
            )
            return result["board"]
        return snapshot.values.get("board")


def _work_plan_fingerprint(plan: WorkPlan) -> str:
    return plan.fingerprint


def _checkpoint_matches_work_plan(stored: object, plan: WorkPlan) -> bool:
    return stored == plan.fingerprint


def _validate_checkpoint(state) -> None:
    from application.action_approval import merge_action_decisions
    plan = state.get("work_plan")
    if not isinstance(plan, WorkPlan) or state.get("work_plan_fingerprint") != plan.fingerprint:
        raise OrchestrationRuntimeError("checkpoint requires the current work plan contract")
    _agent_results(plan, state.get("agent_results", ()))
    merge_action_decisions(state.get("trusted_context", {}).get("action_decisions", ()))


def _worker_failure(item: WorkItem, error: Exception) -> AgentResult:
    from core.framework_models import ModelInvocationError
    from core.tracing import exception_chain
    retryable = isinstance(error, (TimeoutError, ConnectionError)) or (
        isinstance(error, ModelInvocationError) and error.retryable)
    logging.getLogger(__name__).exception("Worker failed: %s", item.work_item_id)
    return AgentResult(item.work_item_id, item.owner_agent,
        AgentResultStatus.RETRYABLE_FAILURE if retryable else AgentResultStatus.TERMINAL_FAILURE,
        f"WORKER_EXECUTION_FAILED:{type(error).__name__}", "orchestration-runtime-v1",
        retryable=retryable, execution_feedback=({"stage": "worker_execution", "status": "failed",
            "detail": {"error_type": type(error).__name__, "retryable": retryable,
                       "exception_chain": exception_chain(error)}},))


def _closed_outcome(item: WorkItem, result: AgentResult | None) -> AgentResult:
    """Apply an explicit control closure; retain facts and committed receipts."""
    if result is None:
        return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.CANCELLED,
                           "EXECUTION_WAIT_CLOSED", "orchestration-runtime-v1")
    return replace(result, status=AgentResultStatus.CANCELLED, reason_code="APPROVAL_NOT_GRANTED",
                   pending_action=None, additional_actions=(), missing_inputs=(), requested_evidence=(),
                   candidate_response=None, retryable=False)
