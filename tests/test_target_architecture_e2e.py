import asyncio
from datetime import datetime, timezone

from application.agent_result import AgentResult, AgentResultStatus, FactRecord, FactSourceKind
from application.capability_safety import (
    CapabilitySafetyGate,
    SafetyInvariant,
    SafetyObservation,
)
from application.conversation_state import ConversationOwner, ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.handoff_runtime import HandoffPublicationSelector
from application.delivery_contract import ConnectorCapability
from application.orchestration_runtime import AgentContextView, OrchestrationRuntime
from application.publication import PublicationPolicy
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    RouteMode,
    RoutePolicy,
    TurnPlanCompiler,
    TurnProposal,
)
from application.work_item import ArgumentValue
from application.write_workflow import (
    ApprovalGrant,
    GovernedWriteRuntime,
    InMemoryOperationLedger,
    WriteOutcomeStatus,
    WriteToolOutcome,
)
from core.identity import IdentityFactory


def _identity():
    return IdentityFactory(lambda: "fixed").create_invocation(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conversation-a",
        request_id="request-a",
    )


def _compile(*commands):
    registry = build_default_capability_registry("tenant-a")
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )
    accepted = RoutePolicy().accept(
        TurnProposal(ProposalDisposition.RESOLVED, commands, "E2E_UNDERSTOOD"),
        state,
        registry,
    )
    return TurnPlanCompiler().compile(accepted, state, registry, _identity())


class ReadExecutor:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    async def __call__(self, context: AgentContextView):
        item = context.work_item
        self.calls.append(item.work_item_id)
        if item.owner_agent in self.failures:
            return AgentResult(
                item.work_item_id,
                item.owner_agent,
                AgentResultStatus.RETRYABLE_FAILURE,
                "PROVIDER_UNAVAILABLE",
                "e2e-read-executor-v1",
                retryable=True,
            )
        facts = tuple(
            FactRecord(
                f"subject:{item.work_item_id}",
                requirement,
                '{"value":"verified"}',
                FactSourceKind.VERIFIED_STATE,
                f"receipt:{item.work_item_id}:{requirement}",
                item.allowed_tools[0],
                "v1",
                datetime.now(timezone.utc),
            )
            for requirement in item.requirement_ids
        )
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "VERIFIED",
            "e2e-read-executor-v1",
            facts=facts,
        )


class WriteTool:
    def __init__(self, receipt_id):
        self.receipt_id = receipt_id
        self.calls = 0

    async def execute(self, item, *, tool_id, arguments, operation_key):
        self.calls += 1
        return WriteToolOutcome(
            WriteOutcomeStatus.COMMITTED,
            self.receipt_id,
            item.expected_output_schema,
            "COMMITTED",
        )


class UnknownReconciler:
    async def reconcile(self, item, *, operation_key):
        return WriteToolOutcome(
            WriteOutcomeStatus.OUTCOME_UNKNOWN,
            reason_code="UNKNOWN",
        )


def _read_runtime(executor):
    return OrchestrationRuntime(
        direct_executor=executor,
        domain_workers={
            "general": executor,
            "product_technical": executor,
            "order_logistics": executor,
            "billing_refund": executor,
        },
    )


def test_e2e_order_status_uses_direct_tool_shape():
    plan = _compile(CommandProposal(
        "order", CommandKind.DIRECT_TOOL, "order_logistics", "Query order status",
        (ArgumentValue.create("order_id", "DP1234"),),
        ("order.current_state",), tool_id="order_lookup",
    ))
    executor = ReadExecutor()
    board = asyncio.run(_read_runtime(executor).execute(
        plan.work, current_message="Where is DP1234?",
    ))

    assert plan.route.mode is RouteMode.DIRECT
    assert board.complete and board.missing_requirement_ids == ()
    assert len(executor.calls) == 1


def test_e2e_product_identification_uses_one_domain_agent_skill():
    plan = _compile(CommandProposal(
        "product", CommandKind.RUN_SKILL, "product_technical", "Identify product",
        (ArgumentValue.create("asset_id", "IMG9"),),
        ("product.canonical_model",), skill_id="product_identification",
    ))
    executor = ReadExecutor()
    board = asyncio.run(_read_runtime(executor).execute(
        plan.work, current_message="What model is this?",
    ))

    assert plan.route.mode is RouteMode.AGENT_TASK
    assert board.complete and len(executor.calls) == 1


def test_e2e_refund_application_is_receipt_backed_and_idempotent():
    plan = _compile(CommandProposal(
        "refund-write", CommandKind.START_WORKFLOW, "billing_refund", "Create refund",
        (ArgumentValue.create("order_id", "DP1234"),),
        ("refund.request_action",),
        flow_ref="execute_refund:v1",
        action_ref="refund.request.create:v1",
        target_entity_ref="order:DP1234",
        target_entity_version="order:DP1234:v7",
        approval_binding="approval-refund-1",
    ))
    item = plan.work.items[0]
    tool = WriteTool("refund-R1")
    worker = GovernedWriteRuntime(
        ledger=InMemoryOperationLedger(),
        tool_port=tool,
        reconciliation_port=UnknownReconciler(),
        approval_grants={"approval-refund-1": ApprovalGrant(
            "approval-refund-1", item.operation_key, item.target_entity_version,
            True, "user:user-a",
        )},
    )
    runtime = OrchestrationRuntime(
        direct_executor=ReadExecutor(), domain_workers={"billing_refund": worker},
    )
    first = asyncio.run(runtime.execute(plan.work, current_message="Refund it"))
    second = asyncio.run(runtime.execute(plan.work, current_message="Refund it"))

    assert first.missing_requirement_ids == ()
    assert second.results[0].action_receipts[0].receipt_id == "refund-R1"
    assert tool.calls == 1


def test_e2e_refund_and_product_questions_fan_out_to_two_domains():
    plan = _compile(
        CommandProposal(
            "refund", CommandKind.RUN_SKILL, "billing_refund", "Query refund",
            (ArgumentValue.create("order_id", "DP1234"),),
            ("refund.current_state",), skill_id="refund_status_summary",
        ),
        CommandProposal(
            "product", CommandKind.RUN_SKILL, "product_technical", "Identify product",
            (ArgumentValue.create("asset_id", "IMG9"),),
            ("product.canonical_model",), skill_id="product_identification",
        ),
    )
    executor = ReadExecutor()
    board = asyncio.run(_read_runtime(executor).execute(
        plan.work, current_message="Refund status and identify this product",
    ))

    assert plan.route.mode is RouteMode.MULTI_DOMAIN
    assert board.complete and len(executor.calls) == 2


def test_e2e_product_failure_preserves_refund_success_as_partial_result():
    plan = _compile(
        CommandProposal(
            "refund", CommandKind.RUN_SKILL, "billing_refund", "Query refund",
            (ArgumentValue.create("order_id", "DP1234"),),
            ("refund.current_state",), skill_id="refund_status_summary",
        ),
        CommandProposal(
            "product", CommandKind.RUN_SKILL, "product_technical", "Identify product",
            (ArgumentValue.create("asset_id", "IMG9"),),
            ("product.canonical_model",), skill_id="product_identification",
        ),
    )
    executor = ReadExecutor(failures=("product_technical",))
    board = asyncio.run(_read_runtime(executor).execute(
        plan.work, current_message="two tasks",
    ))

    statuses = {item.owner_agent: item.status for item in board.results}
    assert statuses["billing_refund"] is AgentResultStatus.SUCCEEDED
    assert statuses["product_technical"] is AgentResultStatus.RETRYABLE_FAILURE
    assert board.partial_delivery_allowed is True


def test_e2e_handoff_requires_ticket_receipt_before_human_ownership_and_claim():
    plan = _compile(CommandProposal(
        "handoff", CommandKind.START_WORKFLOW, "human_service", "Create ticket",
        requirement_ids=("support.handoff_action",),
        flow_ref="human_handoff:v1",
        action_ref="support.handoff.create:v1",
        target_entity_ref="conversation:conversation-a",
        target_entity_version="conversation-a:v0",
    ))
    item = plan.work.items[0]
    tool = WriteTool("ticket-1")
    worker = GovernedWriteRuntime(
        ledger=InMemoryOperationLedger(),
        tool_port=tool,
        reconciliation_port=UnknownReconciler(),
        approval_grants={item.approval_binding: ApprovalGrant(
            item.approval_binding, item.operation_key, item.target_entity_version,
            True, "user-command:request-a",
        )},
    )
    board = asyncio.run(OrchestrationRuntime(
        direct_executor=ReadExecutor(), domain_workers={"human_service": worker},
    ).execute(plan.work, current_message="Human please"))
    receipt = board.results[0].action_receipts[0]
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    ).transfer_to_human(receipt)
    publication = HandoffPublicationSelector().select(
        invocation=_identity(),
        result=board.results[0],
        bundle_version="customer-service-v1",
        created_at="2026-09-03T12:00:00+00:00",
        policy=PublicationPolicy(
            ConnectorCapability.IDEMPOTENT_SEND, 3, "retry-v1",
            "2026-09-04T00:00:00+00:00",
        ),
    )

    assert state.owner is ConversationOwner.HUMAN
    assert "ticket-1" in publication.response_text


def test_capability_safety_failure_disables_only_the_affected_capability():
    decisions = CapabilitySafetyGate().evaluate(
        ("execute_refund", "order_status", "product_identification"),
        (
            SafetyObservation(
                "execute_refund", SafetyInvariant.DUPLICATE_SIDE_EFFECT,
                False, "eval:refund-duplicate-1",
            ),
            SafetyObservation(
                "order_status", SafetyInvariant.UNAUTHORIZED_TOOL,
                True, "eval:order-auth-1",
            ),
        ),
    )
    by_id = {item.capability_id: item for item in decisions}

    assert by_id["execute_refund"].enabled is False
    assert by_id["order_status"].enabled is True
    assert by_id["product_identification"].enabled is True


def test_capability_safety_required_evidence_is_fail_closed_per_capability():
    decisions = CapabilitySafetyGate().evaluate(
        ("execute_refund", "order_status"),
        (SafetyObservation(
            "execute_refund",
            SafetyInvariant.UNAUTHORIZED_TOOL,
            True,
            "eval:refund-tool-auth",
        ),),
        required_invariants={
            "execute_refund": (
                SafetyInvariant.UNAUTHORIZED_TOOL,
                SafetyInvariant.DUPLICATE_SIDE_EFFECT,
            ),
            "order_status": (),
        },
    )
    by_id = {item.capability_id: item for item in decisions}

    assert by_id["execute_refund"].enabled is False
    assert by_id["execute_refund"].failed_invariants == (
        SafetyInvariant.DUPLICATE_SIDE_EFFECT,
    )
    assert by_id["execute_refund"].evidence_refs == (
        "missing:execute_refund:DUPLICATE_SIDE_EFFECT",
    )
    assert by_id["order_status"].enabled is True
