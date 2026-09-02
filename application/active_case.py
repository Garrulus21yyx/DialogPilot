"""M4-T03B typed ActiveCase projection and bounded selection policy."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Mapping, Sequence

from memory.context import ContextSection


class ActiveCaseState(str, Enum):
    NO_ACTIVE_CASE = "NO_ACTIVE_CASE"
    CASES = "CASES"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ActiveCase:
    case_id: str
    ticket_id: str
    issue_summary: str
    intent_or_topic_tags: tuple[str, ...]
    product_order_entity_refs: tuple[str, ...]
    status: str
    business_priority: str
    sla_and_commitment_refs: tuple[str, ...]
    assignee: str
    created_at: str
    updated_at: str
    version: int

    def __post_init__(self) -> None:
        required = (
            self.case_id, self.ticket_id, self.issue_summary, self.status,
            self.business_priority, self.created_at, self.updated_at,
        )
        if any(not str(value).strip() for value in required) or self.version < 1:
            raise ValueError("active case projection is incomplete")

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "ticket_id": self.ticket_id,
            "issue_summary": self.issue_summary,
            "intent_or_topic_tags": list(self.intent_or_topic_tags),
            "product_order_entity_refs": list(self.product_order_entity_refs),
            "status": self.status,
            "business_priority": self.business_priority,
            "sla_and_commitment_refs": list(self.sla_and_commitment_refs),
            "assignee": self.assignee,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }


@dataclass(frozen=True)
class ActiveCaseProjection:
    state: ActiveCaseState
    cases: tuple[ActiveCase, ...] = ()
    reason_codes: tuple[str, ...] = ()
    producer: str = "ticket-service-active-case-v1"

    def __post_init__(self) -> None:
        if (self.state is ActiveCaseState.CASES) != bool(self.cases):
            raise ValueError("active case state/cases mismatch")
        if self.state in {ActiveCaseState.UNAVAILABLE, ActiveCaseState.CONFLICT} and (
            not self.reason_codes
        ):
            raise ValueError("failed active case projection requires reason codes")


@dataclass(frozen=True)
class ActiveCaseSelectionDecision:
    case_id: str
    selected: bool
    hard_include: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ActiveCaseSelection:
    cases: tuple[ActiveCase, ...]
    decisions: tuple[ActiveCaseSelectionDecision, ...]
    policy_version: str = "active-case-context-policy-v1"

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)


@dataclass(frozen=True)
class ActiveCaseContextView:
    projection: ActiveCaseProjection
    selection: ActiveCaseSelection
    section: object | None = None


class ActiveCaseContextRenderer:
    """Render bounded router summaries or task-scoped worker evidence."""

    priority = 90

    @staticmethod
    def _section(payload: Mapping[str, object], description: str) -> ContextSection:
        return ContextSection(
            tag="active_tickets",
            description=description,
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            priority=ActiveCaseContextRenderer.priority,
        )

    def router_section(self, selection: ActiveCaseSelection) -> ContextSection | None:
        if not selection.cases:
            return None
        return self._section({
            "authority": "TicketService",
            "policy_version": selection.policy_version,
            "cases": [{
                "case_id": case.case_id,
                "ticket_id": case.ticket_id,
                "issue_summary": case.issue_summary,
                "intent_or_topic_tags": list(case.intent_or_topic_tags),
                "status": case.status,
                "business_priority": case.business_priority,
                "updated_at": case.updated_at,
                "version": case.version,
            } for case in selection.cases],
        }, "有界当前事项摘要与引用；仅供 Router/Planner 形成任务")

    def worker_section(
        self,
        projection: ActiveCaseProjection,
        *,
        task_query: str,
        task_topics: Sequence[str] = (),
        task_entity_refs: Sequence[str] = (),
        policy: "ActiveCaseContextPolicy | None" = None,
    ) -> ContextSection | None:
        selection = (policy or ActiveCaseContextPolicy()).select(
            projection,
            query=task_query,
            intent_or_topics=task_topics,
            entity_refs=task_entity_refs,
        )
        if not selection.cases:
            return None
        return self._section({
            "authority": "TicketService",
            "policy_version": selection.policy_version,
            "task_scope": {
                "topics": list(task_topics),
                "entity_refs": list(task_entity_refs),
            },
            "cases": [case.to_dict() for case in selection.cases],
        }, "当前 Worker 任务所需的事项证据；不得跨任务广播")


class ActiveCaseContextPolicy:
    version = "active-case-context-policy-v1"
    max_cases = 3
    _PRIORITY = {"critical": 0, "high": 1, "normal": 2}

    def select(
        self,
        projection: ActiveCaseProjection,
        *,
        query: str,
        intent_or_topics: Sequence[str] = (),
        entity_refs: Sequence[str] = (),
    ) -> ActiveCaseSelection:
        if projection.state is not ActiveCaseState.CASES:
            return ActiveCaseSelection((), ())
        query_folded = str(query).casefold()
        topic_set = {str(item).casefold() for item in intent_or_topics if str(item)}
        entity_set = {str(item).casefold() for item in entity_refs if str(item)}
        ranked = []
        metadata = {}
        for index, case in enumerate(projection.cases):
            exact = (
                case.ticket_id.casefold() in query_folded
                or case.case_id.casefold() in query_folded
            )
            critical = case.business_priority == "critical"
            security = any(
                "security" in tag.casefold()
                for tag in case.intent_or_topic_tags
            )
            breached = any(
                ref.casefold().startswith(("breached:", "sla_breached:"))
                for ref in case.sla_and_commitment_refs
            )
            hard = exact or critical or security or breached
            topic_match = bool(topic_set & {
                tag.casefold() for tag in case.intent_or_topic_tags
            })
            entity_match = bool(entity_set & {
                ref.casefold() for ref in case.product_order_entity_refs
            })
            reasons = tuple(
                reason for condition, reason in (
                    (exact, "EXACT_TICKET_REF"),
                    (critical, "CRITICAL_PRIORITY"),
                    (security, "SECURITY_CASE"),
                    (breached, "BREACHED_SLA_OR_COMMITMENT"),
                    (topic_match, "TOPIC_MATCH"),
                    (entity_match, "ENTITY_MATCH"),
                ) if condition
            ) or ("RECENCY_FALLBACK",)
            key = (
                0 if hard else 1,
                0 if (topic_match or entity_match) else 1,
                self._PRIORITY.get(case.business_priority, 99),
                index,
            )
            ranked.append((key, case))
            metadata[case.case_id] = (hard, reasons)
        hard_cases = [case for key, case in sorted(ranked) if key[0] == 0]
        soft_cases = [case for key, case in sorted(ranked) if key[0] != 0]
        soft_limit = max(0, self.max_cases - len(hard_cases))
        selected = tuple(hard_cases + soft_cases[:soft_limit])
        selected_ids = {case.case_id for case in selected}
        decisions = tuple(
            ActiveCaseSelectionDecision(
                case.case_id, case.case_id in selected_ids,
                metadata[case.case_id][0], metadata[case.case_id][1],
            )
            for case in projection.cases
        )
        return ActiveCaseSelection(selected, decisions)


def opaque_refs(metadata: Mapping[str, str]) -> tuple[str, ...]:
    allowed = ("order_ref", "product_ref", "entity_ref")
    return tuple(
        str(metadata[key]) for key in allowed
        if str(metadata.get(key) or "").strip()
    )
