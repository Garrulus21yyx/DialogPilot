"""M4-T03B typed ActiveCase projection and relevance-policy proofs."""
from application.active_case import (
    ActiveCase,
    ActiveCaseContextPolicy,
    ActiveCaseContextRenderer,
    ActiveCaseConsumerMode,
    ActiveCaseProjection,
    ActiveCaseState,
    DEFAULT_ACTIVE_CASE_POLICY_BINDING,
)
from infrastructure.active_case_projection import TicketServiceActiveCaseReader
from services.ticket_service import TicketPriority, TicketService, TicketStatus


def _create(service, key, *, priority=TicketPriority.NORMAL, intent="other", metadata=None):
    return service.create_ticket(
        idempotency_key=key, user_id="user-1", conv_id="conv-1",
        request_id=key, question=f"issue {key}", published_response="legacy text",
        reason="handoff", priority=priority, intent=intent,
        agent_type="account_security" if "security" in intent else "general",
        verification_status="unknown", identity_metadata=metadata,
    )[0]


def _case(index, *, priority="normal", tags=(), refs=(), commitments=()):
    return ActiveCase(
        case_id=f"case-{index}", ticket_id=f"ticket-{index}",
        issue_summary=f"issue-{index}", intent_or_topic_tags=tuple(tags),
        product_order_entity_refs=tuple(refs), status="open",
        business_priority=priority,
        sla_and_commitment_refs=tuple(commitments), assignee="",
        created_at=f"2026-09-02T00:00:0{index}+00:00",
        updated_at=f"2026-09-02T00:00:0{index}+00:00", version=1,
    )


def test_reader_projects_owner_fields_and_excludes_closed_only(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    active = _create(
        service, "active", intent="delivery",
        metadata={"order_ref": "order:A123"},
    )
    resolved = _create(service, "resolved")
    closed = _create(service, "closed")
    service.transition(resolved.ticket_id, TicketStatus.IN_PROGRESS, actor="agent")
    service.transition(resolved.ticket_id, TicketStatus.RESOLVED, actor="agent")
    service.transition(closed.ticket_id, TicketStatus.CLOSED, actor="agent")

    result = TicketServiceActiveCaseReader(service).read(user_id="user-1")
    assert result.state is ActiveCaseState.CASES
    assert {case.ticket_id for case in result.cases} == {
        active.ticket_id, resolved.ticket_id,
    }
    projected = next(case for case in result.cases if case.ticket_id == active.ticket_id)
    assert projected.product_order_entity_refs == ("order:A123",)
    assert projected.issue_summary == "issue active"
    assert projected.version == 1


def test_empty_unavailable_and_conflict_are_distinct_closed_outcomes(tmp_path):
    empty = TicketServiceActiveCaseReader(
        TicketService(str(tmp_path / "empty.db"))
    ).read(user_id="user-1")
    assert empty.state is ActiveCaseState.NO_ACTIVE_CASE

    class Failing:
        def list_active_tickets(self, **_kwargs):
            raise TimeoutError("backend unavailable")

    unavailable = TicketServiceActiveCaseReader(Failing()).read(user_id="user-1")
    assert unavailable.state is ActiveCaseState.UNAVAILABLE
    assert unavailable.reason_codes == ("TICKET_SERVICE_TIMEOUTERROR",)

    service = TicketService(str(tmp_path / "duplicate.db"))
    ticket = _create(service, "duplicate")

    class Duplicate:
        def list_active_tickets(self, **_kwargs):
            return [ticket, ticket]

    conflict = TicketServiceActiveCaseReader(Duplicate()).read(user_id="user-1")
    assert conflict.state is ActiveCaseState.CONFLICT


def test_policy_hard_includes_exact_critical_security_then_selects_relevant_soft_case():
    projection = ActiveCaseProjection(ActiveCaseState.CASES, (
        _case(1),
        _case(2, tags=("delivery",), refs=("order:A123",)),
        _case(3),
        _case(4, priority="critical"),
        _case(5, tags=("account_security",)),
        _case(6, commitments=("breached:sla:response",)),
    ))
    selection = ActiveCaseContextPolicy().select(
        projection, query="continue ticket-3", intent_or_topics=("delivery",),
        entity_refs=("order:A123",),
    )
    # Hard includes are never dropped by the legacy soft limit of three.
    assert selection.target_case_ids == (
        "case-4", "case-3", "case-5", "case-6",
    )
    assert all(
        decision.hard_include for decision in selection.decisions
        if decision.selected
    )
    assert selection.shadow_matches_legacy is False


def test_policy_uses_relevance_before_priority_without_unfrozen_numeric_score():
    projection = ActiveCaseProjection(ActiveCaseState.CASES, (
        _case(1),
        _case(2),
        _case(3),
        _case(4, tags=("delivery",)),
    ))
    selection = ActiveCaseContextPolicy().select(
        projection, query="delivery issue", intent_or_topics=("delivery",),
    )
    assert selection.target_case_ids == ("case-4", "case-1", "case-2")
    assert selection.legacy_case_ids == ("case-1", "case-2", "case-3")


def test_renderer_separates_bounded_router_summary_from_task_scoped_evidence():
    projection = ActiveCaseProjection(ActiveCaseState.CASES, (
        _case(1, tags=("billing",), refs=("order:BILL",)),
        _case(2, tags=("delivery",), refs=("order:SHIP",)),
        _case(3),
        _case(4),
    ))
    policy = ActiveCaseContextPolicy()
    renderer = ActiveCaseContextRenderer()
    router = renderer.router_section(policy.select(
        projection, query="delivery", intent_or_topics=("delivery",),
    ))
    worker = renderer.worker_section(
        projection, task_query="check order SHIP",
        task_topics=("delivery",), task_entity_refs=("order:SHIP",),
    )

    import json
    router_payload = json.loads(router.content)
    worker_payload = json.loads(worker.content)
    assert len(router_payload["cases"]) == 3
    assert "product_order_entity_refs" not in router_payload["cases"][0]
    assert worker_payload["cases"][0]["case_id"] == "case-2"
    assert worker_payload["task_scope"] == {
        "entity_refs": ["order:SHIP"], "topics": ["delivery"],
    }


def test_default_pointer_is_shadow_and_rollback_keeps_legacy_single_consumer():
    binding = DEFAULT_ACTIVE_CASE_POLICY_BINDING
    assert binding.mode is ActiveCaseConsumerMode.SHADOW
    assert binding.active_policy_version == "active-case-legacy-recency-v1"
    rolled_back = binding.rollback()
    assert rolled_back.mode is ActiveCaseConsumerMode.LEGACY
    assert rolled_back.active_policy_version == binding.previous_verified_policy_version
