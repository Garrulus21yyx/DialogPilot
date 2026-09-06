"""LangGraph parent runtime for direct, single-agent, and multi-agent work."""
from __future__ import annotations

import hashlib
import json
import operator
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Annotated, Awaitable, Callable, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Overwrite, Send, interrupt

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    EvidenceRequest,
    FactRecord,
)
from application.result_board import ResultBoard, ResultBoardSnapshot
from application.work_item import ControlMode, WorkItem, WorkPlan
from application.work_control import WorkControlGuard, WorkSuperseded


class OrchestrationRuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class AgentContextView:
    work_item: WorkItem
    current_message: str
    verified_facts: tuple[FactRecord, ...]
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    trusted_context: Mapping[str, str] = field(default_factory=dict)
    dependency_results: tuple[AgentResult, ...] = ()


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
    agent_results: Annotated[list[AgentResult], operator.add]
    facts: tuple[FactRecord, ...]
    board: ResultBoardSnapshot
    trusted_context: Mapping[str, str]
    interrupt_after_completion: bool


class WorkerState(TypedDict):
    work_item: WorkItem
    current_message: str
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    facts: tuple[FactRecord, ...]
    trusted_context: Mapping[str, str]
    dependency_results: tuple[AgentResult, ...]


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
            "await_resume", self._dispatch, ["execute_work_item", "finish"],
        )
        builder.add_edge("finish", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def _initialize(self, state: ParentGraphState):
        plan = state["work_plan"]
        board = self._result_board.evaluate(plan, ())
        return {
            "ready_items": board.ready_items,
            "agent_results": [],
            "facts": (),
            "board": board,
        }

    def _dispatch(self, state: ParentGraphState):
        ready = state.get("ready_items", ())
        if not ready:
            return "finish"
        results = {
            result.work_item_id: result
            for result in state.get("agent_results", ())
        }
        return [
            Send("execute_work_item", {
                "work_item": item,
                "current_message": state.get("current_message", ""),
                "recent_relevant_turns": state.get("recent_relevant_turns", ()),
                "evidence_refs": state.get("evidence_refs", ()),
                "token_budget": state.get("token_budget", 6000),
                "facts": state.get("facts", ()),
                "trusted_context": state.get("trusted_context", {}),
                "dependency_results": tuple(
                    results[dependency]
                    for dependency in item.dependencies
                ),
            })
            for item in ready
        ]

    def _next_step(self, state: ParentGraphState):
        if state.get("ready_items", ()):
            return self._dispatch(state)
        if self._checkpointer is not None and (
            any(
                item.status is AgentResultStatus.NEEDS_USER_INPUT
                for item in state.get("agent_results", ())
            )
            or bool(state.get("interrupt_after_completion"))
        ):
            return "await_resume"
        return "finish"

    async def _await_resume(self, state: ParentGraphState):
        missing = tuple(
            field
            for result in state.get("agent_results", ())
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
        if resumed.get("cancel") is True:
            return {
                "interrupt_after_completion": False,
                "ready_items": (),
            }
        plan = resumed.get("work_plan")
        if not isinstance(plan, WorkPlan):
            raise OrchestrationRuntimeError("resume payload requires a validated WorkPlan")
        board = self._result_board.evaluate(plan, ())
        return {
            "work_plan": plan,
            "work_plan_fingerprint": _work_plan_fingerprint(plan),
            "current_message": str(resumed.get("current_message") or ""),
            "recent_relevant_turns": tuple(resumed.get("recent_relevant_turns") or ()),
            "evidence_refs": tuple(resumed.get("evidence_refs") or ()),
            "token_budget": int(resumed.get("token_budget") or 6000),
            "trusted_context": dict(resumed.get("trusted_context") or {}),
            "interrupt_after_completion": False,
            "agent_results": Overwrite(value=[]),
            "facts": (),
            "ready_items": board.ready_items,
            "board": board,
        }

    async def _execute_work_item(self, state: WorkerState):
        started = time.perf_counter()
        update = await self._run_work_item(state)
        result = update["agent_results"][0]
        owner = state["work_item"].owner_agent
        self._outcome_counts.setdefault(owner, Counter())[result.status.value] += 1
        self._elapsed_ms[owner] = (
            self._elapsed_ms.get(owner, 0.0) + (time.perf_counter() - started) * 1000
        )
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
        )
        if self._control_guard is not None and not self._control_guard.is_current(
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
            result = await self._execute_with_evidence(executor, context)
        except WorkSuperseded:
            result = self._control_guard.superseded_result(item)
        if self._control_guard is not None and not self._control_guard.is_current(
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
            if self._control_guard is not None:
                self._control_guard.ensure_current(
                    context.work_item, context.trusted_context,
                )
            current = replace(
                context,
                verified_facts=_merge_facts(
                    context.verified_facts,
                    resolved_facts,
                ),
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
            available = _merge_facts(current.verified_facts, result.facts)
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
        board = self._result_board.evaluate(
            state["work_plan"], tuple(state.get("agent_results", ())),
        )
        return {
            "agent_results": list(board.blocked_results),
            "facts": board.facts,
            "ready_items": board.ready_items,
            "board": board,
        }

    async def _finish(self, state: ParentGraphState):
        board = self._result_board.evaluate(
            state["work_plan"], tuple(state.get("agent_results", ())),
        )
        if not board.complete:
            raise OrchestrationRuntimeError("work graph stopped before reaching terminal outcomes")
        return {"board": board, "facts": board.facts, "ready_items": ()}

    async def execute(
        self,
        work_plan: WorkPlan,
        *,
        current_message: str,
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
        thread_id: str | None = None,
        trusted_context: Mapping[str, str] | None = None,
        interrupt_after_completion: bool = False,
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
        }
        if self._checkpointer is not None:
            snapshot = await self.graph.aget_state(config)
            if snapshot.values:
                if snapshot.values.get("work_plan_fingerprint") != _work_plan_fingerprint(
                    work_plan
                ):
                    raise OrchestrationRuntimeError(
                        "checkpoint thread is bound to another work plan"
                    )
                board = snapshot.values.get("board")
                if board is not None and board.complete:
                    return _normalize_board(board)
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
        recent_relevant_turns: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        token_budget: int = 6000,
        trusted_context: Mapping[str, str] | None = None,
    ) -> ResultBoardSnapshot:
        if self._checkpointer is None:
            raise OrchestrationRuntimeError("resume requires a checkpointer")
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.graph.aget_state(config)
        if not snapshot.tasks or not any(task.interrupts for task in snapshot.tasks):
            raise OrchestrationRuntimeError("checkpoint thread is not interrupted")
        result = await self.graph.ainvoke(Command(resume={
            "work_plan": work_plan,
            "current_message": current_message,
            "recent_relevant_turns": recent_relevant_turns,
            "evidence_refs": evidence_refs,
            "token_budget": token_budget,
            "trusted_context": dict(trusted_context or {}),
        }), config=config)
        return result["board"]

    async def cancel_interrupt(self, *, thread_id: str) -> None:
        if self._checkpointer is None:
            return
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.graph.aget_state(config)
        if snapshot.tasks and any(task.interrupts for task in snapshot.tasks):
            await self.graph.ainvoke(
                Command(resume={"cancel": True}), config=config,
            )


def _work_plan_fingerprint(plan: WorkPlan) -> str:
    raw = json.dumps(
        {
            "primary": plan.primary_work_item_id,
            "items": [item.fingerprint for item in plan.items],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "work-plan:v1:" + hashlib.sha256(raw).hexdigest()


def _merge_facts(*groups: tuple[FactRecord, ...]) -> tuple[FactRecord, ...]:
    merged: dict[
        tuple[str, str, str, str, str],
        FactRecord,
    ] = {}
    for fact in (item for group in groups for item in group):
        key = (
            fact.subject_ref,
            fact.requirement_id,
            fact.source_ref,
            fact.producer_id,
            fact.producer_version,
        )
        prior = merged.setdefault(key, fact)
        if prior.value_json != fact.value_json:
            raise OrchestrationRuntimeError(
                "one evidence identity produced conflicting values"
            )
    return tuple(merged.values())


def _normalize_board(board: ResultBoardSnapshot) -> ResultBoardSnapshot:
    """Restore tuple-based public contracts at the checkpoint boundary."""
    def normalize_result(result: AgentResult) -> AgentResult:
        return replace(
            result,
            facts=tuple(result.facts),
            evidence_refs=tuple(result.evidence_refs),
            action_receipts=tuple(result.action_receipts),
            missing_inputs=tuple(result.missing_inputs),
            requested_evidence=tuple(result.requested_evidence),
            state_mutation_proposals=tuple(result.state_mutation_proposals),
        )

    return ResultBoardSnapshot(
        tuple(normalize_result(item) for item in board.results),
        tuple(board.facts),
        tuple(board.ready_items),
        tuple(normalize_result(item) for item in board.blocked_results),
        tuple(board.missing_requirement_ids),
        tuple(board.conflict_keys),
        bool(board.complete),
        bool(board.partial_delivery_allowed),
    )
