"""LangGraph parent runtime for direct, single-agent, and multi-agent work."""
from __future__ import annotations

import hashlib
import json
import operator
from dataclasses import dataclass, field, replace
from typing import Annotated, Awaitable, Callable, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from application.agent_result import AgentResult, FactRecord
from application.result_board import ResultBoard, ResultBoardSnapshot
from application.work_item import ControlMode, WorkItem, WorkPlan


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


class WorkExecutor(Protocol):
    async def __call__(self, context: AgentContextView) -> AgentResult: ...


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


class WorkerState(TypedDict):
    work_item: WorkItem
    current_message: str
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    facts: tuple[FactRecord, ...]
    trusted_context: Mapping[str, str]


class OrchestrationRuntime:
    """Build one static graph; runtime WorkPlans determine fan-out and waves."""

    def __init__(
        self,
        *,
        direct_executor: WorkExecutor,
        domain_workers: Mapping[str, WorkExecutor],
        result_board: ResultBoard | None = None,
        checkpointer=None,
    ) -> None:
        self._direct_executor = direct_executor
        self._domain_workers = dict(domain_workers)
        self._result_board = result_board or ResultBoard()
        self._checkpointer = checkpointer
        self.graph = self._build_graph()

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
            "merge_results", self._dispatch, ["execute_work_item", "finish"],
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
        return [
            Send("execute_work_item", {
                "work_item": item,
                "current_message": state.get("current_message", ""),
                "recent_relevant_turns": state.get("recent_relevant_turns", ()),
                "evidence_refs": state.get("evidence_refs", ()),
                "token_budget": state.get("token_budget", 6000),
                "facts": state.get("facts", ()),
                "trusted_context": state.get("trusted_context", {}),
            })
            for item in ready
        ]

    async def _execute_work_item(self, state: WorkerState):
        item = state["work_item"]
        context = AgentContextView(
            item,
            state["current_message"],
            state["facts"],
            state["recent_relevant_turns"],
            state["evidence_refs"],
            state["token_budget"],
            state.get("trusted_context", {}),
        )
        if item.control_mode is ControlMode.DIRECT:
            executor = self._direct_executor
        else:
            try:
                executor = self._domain_workers[item.owner_agent]
            except KeyError as exc:
                raise OrchestrationRuntimeError(
                    f"no domain worker registered for {item.owner_agent}"
                ) from exc
        result = await executor(context)
        return {"agent_results": [result]}

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
