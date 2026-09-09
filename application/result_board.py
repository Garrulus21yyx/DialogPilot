"""Deterministic merger for work outcomes, dependencies, facts, and partial failure."""
from __future__ import annotations

from dataclasses import dataclass, replace

from application.agent_result import AgentResult, AgentResultStatus, FactRecord, FactSourceKind, merge_facts
from application.work_item import (
    DependencySatisfaction,
    MissingDependencyOutcome,
    PartialDeliveryPolicy,
    WorkItem,
    WorkPlan,
)


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

    def coverage_for(self, item: WorkItem, result: AgentResult | None) -> dict:
        """Derived coverage of this paired contract/outcome, never a global join."""
        missing = sorted(ResultBoard._missing(item, result))
        pairs = self.outcome_items
        impacts = _conflict_impacts(pairs, self.conflict_keys, len(self.retained_outcomes))
        indexes = [index for index, pair in enumerate(pairs) if pair == (item, result)]
        if indexes:
            conflicts = sorted(set().union(*(impacts[index] for index in indexes)))
        else:
            conflicts = sorted({f"{f.subject_ref}:{f.requirement_id}" for f in result.facts
                                if f"{f.subject_ref}:{f.requirement_id}" in self.conflict_keys}) if result else []
        deliverable = bool(result and result.status in _DELIVERABLE and not missing and not conflicts
                           and (result.facts or result.action_receipts))
        return {"requirement_ids": list(item.requirement_ids), "missing_requirement_ids": missing,
                "conflict_keys": conflicts, "coverage_complete": not missing and not conflicts,
                "deliverable": deliverable,
                "delivery_reason": "CONFLICT_AFFECTED" if conflicts else "MISSING_REQUIREMENTS" if missing
                                   else "DELIVERABLE" if deliverable else "NO_DELIVERABLE_RESULT",
                "task_completed": bool(result and result.status is AgentResultStatus.SUCCEEDED
                                       and not missing and not conflicts)}

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
    version = "result-board-v3-conflict-scope"

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
        initial_pairs = (*retained_outcomes, *((item, by_id.get(item.work_item_id)) for item in plan.items))
        impacts = _conflict_impacts(initial_pairs, conflicts, len(retained_outcomes))
        affected = {item.work_item_id: impacts[len(retained_outcomes) + index]
                    for index, item in enumerate(plan.items)}
        blocked = []
        effective_by_id = dict(by_id)
        # Topological evaluation propagates a failed dependency through the whole
        # graph, independent of plan declaration or worker completion order.
        if plan.policy.missing_dependency_outcome is not MissingDependencyOutcome.BLOCK:
            raise ResultBoardError("unsupported missing dependency outcome policy")
        for item in (item for wave in plan.execution_waves() for item in wave):
            if item.work_item_id in by_id:
                continue
            failed_dependencies = tuple(
                dependency for dependency in item.dependencies
                if dependency in effective_by_id and not self._dependency_satisfied(
                    items[dependency], effective_by_id[dependency], affected[dependency], plan)
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
                and self._dependency_satisfied(items[dependency], effective_by_id[dependency], affected[dependency], plan)
                for dependency in item.dependencies
            )
        )

        outcomes = (*retained_outcomes, *((item, effective_by_id.get(item.work_item_id))
                                         for item in plan.items))
        missing = tuple(sorted({requirement for item, result in outcomes
                                for requirement in self._missing(item, result)}))
        complete = len(effective) == len(plan.items)
        snapshot = ResultBoardSnapshot(
            tuple(effective), all_facts, ready, tuple(blocked), missing, conflicts,
            complete, False, work_items=plan.items, retained_outcomes=retained_outcomes)
        coverage = [snapshot.coverage_for(item, result) for item, result in outcomes]
        if plan.policy.partial_delivery is not PartialDeliveryPolicy.DELIVERABLE_OUTCOMES_ONLY:
            raise ResultBoardError("unsupported partial delivery policy")
        return replace(snapshot, partial_delivery_allowed=(
            any(row["deliverable"] for row in coverage)
            and any(not row["task_completed"] for row in coverage)))

    @staticmethod
    def _missing(item: WorkItem, result: AgentResult | None) -> set[str]:
        supplied = set() if result is None else {
            *(fact.requirement_id for fact in result.facts),
            *(receipt.requirement_id for receipt in result.action_receipts),
        }
        return set(item.requirement_ids).difference(supplied)

    @classmethod
    def _dependency_satisfied(cls, item, result, conflicts, plan: WorkPlan | None = None):
        """A task-ID edge means successful completion, not partial progress."""
        if plan is not None and plan.policy.dependency_satisfaction is not DependencySatisfaction.SUCCESS_WITH_COVERAGE:
            raise ResultBoardError("unsupported dependency satisfaction policy")
        return (result.status is AgentResultStatus.SUCCEEDED and not cls._missing(item, result)
                and not conflicts)

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


def _conflict_impacts(pairs, conflicts, retained_count):
    """Propagate known fact conflicts over hard edges without rewriting results.

    Current edges resolve only within the current plan. Retained local IDs may
    recur: all matching retained predecessors are conservatively considered.
    """
    impacts = [{f"{fact.subject_ref}:{fact.requirement_id}" for fact in result.facts
                if f"{fact.subject_ref}:{fact.requirement_id}" in conflicts}
               if result else set() for _, result in pairs]
    edges = [[other for other, (upstream, _) in enumerate(pairs)
              if upstream.work_item_id in item.dependencies
              and (index < retained_count) == (other < retained_count)]
             for index, (item, _) in enumerate(pairs)]
    while True:
        updated = [impact.union(*(impacts[upstream] for upstream in edges[index]))
                   for index, impact in enumerate(impacts)]
        if updated == impacts:
            return updated
        impacts = updated


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
