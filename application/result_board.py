"""Deterministic merger for work outcomes, dependencies, facts, and partial failure."""
from __future__ import annotations

from dataclasses import dataclass

from application.agent_result import AgentResult, AgentResultStatus, FactRecord, FactSourceKind, merge_facts
from application.work_item import WorkItem, WorkPlan


class ResultBoardError(ValueError):
    pass


_DELIVERABLE = {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL}
_TERMINAL = {
    AgentResultStatus.SUCCEEDED,
    AgentResultStatus.PARTIAL,
    AgentResultStatus.NEEDS_USER_INPUT,
    AgentResultStatus.NEEDS_EVIDENCE,
    AgentResultStatus.WAITING_APPROVAL,
    AgentResultStatus.BLOCKED,
    AgentResultStatus.RECONCILING,
    AgentResultStatus.RETRYABLE_FAILURE,
    AgentResultStatus.TERMINAL_FAILURE,
    AgentResultStatus.CANCELLED,
    AgentResultStatus.SUPERSEDED,
}


@dataclass(frozen=True)
class ResultBoardSnapshot:
    results: tuple[AgentResult, ...]
    facts: tuple[FactRecord, ...]
    ready_items: tuple[WorkItem, ...]
    blocked_results: tuple[AgentResult, ...]
    missing_requirement_ids: tuple[str, ...]
    conflict_keys: tuple[str, ...]
    complete: bool
    partial_delivery_allowed: bool
    work_items: tuple[WorkItem, ...] = ()
    retained_outcomes: tuple[tuple[WorkItem, AgentResult | None], ...] = ()

    def __post_init__(self):
        for name in ("results", "facts", "ready_items", "blocked_results", "missing_requirement_ids",
                     "conflict_keys", "work_items"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "retained_outcomes", tuple(tuple(pair) for pair in self.retained_outcomes))

    @property
    def outcome_items(self) -> tuple[tuple[WorkItem, AgentResult | None], ...]:
        results = {result.work_item_id: result for result in self.results}
        return (*self.retained_outcomes, *((item, results.get(item.work_item_id)) for item in self.work_items))

    @property
    def all_results(self) -> tuple[AgentResult, ...]:
        """Current execution plus unreplaced outcomes of this checkpoint's request."""
        return (*(result for _, result in self.retained_outcomes if result is not None), *self.results)

    @property
    def coverage_complete(self) -> bool:
        """Evidence coverage is independent of worker or answer success."""
        return not self.missing_requirement_ids and not self.conflict_keys

    @property
    def task_completed(self) -> bool:
        """All planned work succeeded, not merely returned a terminal outcome."""
        return (self.complete and self.coverage_complete
                and all(result is not None for _, result in self.retained_outcomes) and all(
            result.status is AgentResultStatus.SUCCEEDED for result in self.all_results
        ))


class ResultBoard:
    version = "result-board-v2-outcome-coverage"

    def evaluate(
        self,
        plan: WorkPlan,
        results: tuple[AgentResult, ...],
        *,
        retained_outcomes: tuple[tuple[WorkItem, AgentResult | None], ...] = (),
    ) -> ResultBoardSnapshot:
        items = {item.work_item_id: item for item in plan.items}
        by_id: dict[str, AgentResult] = {}
        for result in results:
            item = items.get(result.work_item_id)
            if item is None:
                raise ResultBoardError("result is outside the work plan")
            if result.owner_agent != item.owner_agent:
                raise ResultBoardError("result owner differs from work item owner")
            if result.work_item_id in by_id:
                raise ResultBoardError("work item produced more than one result")
            by_id[result.work_item_id] = result

        for item, result in retained_outcomes:
            if result is not None and (result.work_item_id != item.work_item_id
                                      or result.owner_agent != item.owner_agent):
                raise ResultBoardError("retained result differs from its work item")
        all_facts = current_facts(merge_facts(
            *(result.facts for _, result in retained_outcomes if result is not None),
            *(by_id[item.work_item_id].facts for item in plan.items if item.work_item_id in by_id)))
        conflicts = self._conflicts(all_facts)
        blocked = []
        effective_by_id = dict(by_id)
        # Topological evaluation propagates a failed dependency through the whole
        # graph, independent of plan declaration or worker completion order.
        for item in (item for wave in plan.execution_waves() for item in wave):
            if item.work_item_id in by_id:
                continue
            failed_dependencies = tuple(
                dependency for dependency in item.dependencies
                if dependency in effective_by_id and not self._dependency_satisfied(
                    items[dependency], effective_by_id[dependency], conflicts)
            )
            if failed_dependencies and all(
                dependency in effective_by_id and effective_by_id[dependency].status in _TERMINAL
                for dependency in item.dependencies
            ):
                blocked.append(AgentResult(
                    item.work_item_id,
                    item.owner_agent,
                    AgentResultStatus.BLOCKED,
                    "UPSTREAM_NOT_SUCCESSFUL:" + ",".join(failed_dependencies),
                    self.version,
                ))
                effective_by_id[item.work_item_id] = blocked[-1]
        # Parallel reducers may deliver Worker results in any completion order.
        # The WorkPlan is the ordering authority for every downstream projection.
        effective = tuple(
            effective_by_id[item.work_item_id]
            for item in plan.items
            if item.work_item_id in effective_by_id
        )
        ready = tuple(
            item for item in plan.items
            if item.work_item_id not in effective_by_id
            and all(
                dependency in effective_by_id
                and self._dependency_satisfied(items[dependency], effective_by_id[dependency], conflicts)
                for dependency in item.dependencies
            )
        )

        outcomes = (*retained_outcomes, *((item, effective_by_id.get(item.work_item_id))
                                         for item in plan.items))
        missing = tuple(sorted({requirement for item, result in outcomes
                                for requirement in self._missing(item, result)}))
        complete = len(effective) == len(plan.items)
        successes = any(result is not None and result.status in _DELIVERABLE
                        for _, result in outcomes)
        incomplete = any(result is None or result.status is not AgentResultStatus.SUCCEEDED
                         or self._missing(item, result) for item, result in outcomes)
        return ResultBoardSnapshot(
            tuple(effective),
            all_facts,
            ready,
            tuple(blocked),
            missing,
            conflicts,
            complete,
            bool(successes and incomplete and not conflicts),
            work_items=plan.items,
            retained_outcomes=retained_outcomes,
        )

    @staticmethod
    def _missing(item: WorkItem, result: AgentResult | None) -> set[str]:
        supplied = set() if result is None else {
            *(fact.requirement_id for fact in result.facts),
            *(receipt.requirement_id for receipt in result.action_receipts),
        }
        return set(item.requirement_ids).difference(supplied)

    @classmethod
    def _dependency_satisfied(cls, item, result, conflicts):
        """A task-ID edge means successful completion, not partial progress."""
        return (result.status is AgentResultStatus.SUCCEEDED and not cls._missing(item, result)
                and not any(f"{fact.subject_ref}:{fact.requirement_id}" in conflicts
                            for fact in result.facts))

    @staticmethod
    def _conflicts(facts: tuple[FactRecord, ...]) -> tuple[str, ...]:
        values: dict[tuple[str, str], str] = {}
        conflicts = set()
        for fact in facts:
            key = (fact.subject_ref, fact.requirement_id)
            prior = values.setdefault(key, fact.value_json)
            if prior != fact.value_json:
                conflicts.add(f"{key[0]}:{key[1]}")
        return tuple(sorted(conflicts))


def current_facts(facts: tuple[FactRecord, ...]) -> tuple[FactRecord, ...]:
    """Project sequential authoritative reads; retain unordered conflicts and history upstream.

    Only an actual read begun after a previous read completed supersedes it.
    Cache/legacy observations without a trusted interval cannot establish ordering.
    """
    def identity(fact):
        return (fact.subject_ref, fact.requirement_id, fact.producer_id, fact.producer_version, fact.source_ref)
    values = {}
    contradictory = set()
    for fact in facts:
        key = identity(fact)
        if values.setdefault(key, fact.observation_signature) != fact.observation_signature:
            contradictory.add(key)
    def supersedes(new, old):
        return (new.source_kind is old.source_kind is FactSourceKind.VERIFIED_STATE
            and identity(old) not in contradictory and identity(new) not in contradictory
            and (new.subject_ref, new.requirement_id, new.producer_id, new.producer_version)
                == (old.subject_ref, old.requirement_id, old.producer_id, old.producer_version)
            and new.source_ref != old.source_ref
            and new.observation_started_at is not None and old.observation_started_at is not None
            and new.observation_started_at > old.observed_at)
    return tuple(fact for fact in facts if not any(supersedes(other, fact) for other in facts))
