"""LangGraph parent runtime for direct, single-agent, and multi-agent work."""
from __future__ import annotations

import operator
from dataclasses import dataclass
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


class WorkExecutor(Protocol):
    async def __call__(self, context: AgentContextView) -> AgentResult: ...


class ParentGraphState(TypedDict, total=False):
    work_plan: WorkPlan
    current_message: str
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    ready_items: tuple[WorkItem, ...]
    agent_results: Annotated[list[AgentResult], operator.add]
    facts: tuple[FactRecord, ...]
    board: ResultBoardSnapshot


class WorkerState(TypedDict):
    work_item: WorkItem
    current_message: str
    recent_relevant_turns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    token_budget: int
    facts: tuple[FactRecord, ...]


class OrchestrationRuntime:
    """Build one static graph; runtime WorkPlans determine fan-out and waves."""

    def __init__(
        self,
        *,
        direct_executor: WorkExecutor,
        domain_workers: Mapping[str, WorkExecutor],
        result_board: ResultBoard | None = None,
    ) -> None:
        self._direct_executor = direct_executor
        self._domain_workers = dict(domain_workers)
        self._result_board = result_board or ResultBoard()
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
        return builder.compile()

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
    ) -> ResultBoardSnapshot:
        result = await self.graph.ainvoke({
            "work_plan": work_plan,
            "current_message": current_message,
            "recent_relevant_turns": recent_relevant_turns,
            "evidence_refs": evidence_refs,
            "token_budget": token_budget,
            "agent_results": [],
            "facts": (),
        })
        return result["board"]

