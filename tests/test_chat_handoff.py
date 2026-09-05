import asyncio
from types import SimpleNamespace

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentType,
    OrchestratorResult,
)
from agents.react_engine import ReActResult, ReActStatus
from api import main
from application.chat_contracts import ChatCommand, Completed
from application.route_decision import (
    ComponentInvocation,
    ComponentStatus,
    RouteDecision,
    RouteMode,
    RouteRisk,
)
from agents.request_shape_policy import RequestShapePolicy
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.auth import Principal
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.ticket_service import TicketPriority
from services.response_delivery import DeliveryStatus, ResponseDelivery
from services.badcase_registry import BadCaseRegistry
from services.evolution import ActiveBundleResolver, AgentBundleRegistry, build_default_bundle
from memory.context import ContextAssembler


class FakeMemoryContext:
    recent_messages = []
    retrieval_hits = []

    @staticmethod
    def to_prompt_text():
        return ""

    @staticmethod
    def to_sections():
        return []


class FakeMemory:
    def __init__(self):
        self.messages = []
        self.message_metadata = []
        self.profile_updates = 0

    async def get_context(self, *_args, **_kwargs):
        return FakeMemoryContext()

    async def add_messages(self, _user_id, _conv_id, entries, *, extract_facts=False):
        persisted = []
        for role, content, metadata in entries:
            self.messages.append((role.value, content))
            self.message_metadata.append(dict(metadata))
            persisted.append(SimpleNamespace(role=role, content=content, metadata=metadata))
        if extract_facts:
            self.profile_updates += 1
        return persisted

    async def update_profile(self, *_args, **_kwargs):
        self.profile_updates += 1
        return None


class FakeResponseDeliveryService:
    def __init__(self):
        self.records = []

    def select_response(
        self, *, user_id, conv_id, request_id, response_text,
        identity_metadata=None,
    ):
        delivery = ResponseDelivery(
            response_id=f"response-{len(self.records) + 1}",
            user_id=user_id,
            conv_id=conv_id,
            request_id=request_id,
            seq=1 + sum(item.conv_id == conv_id for item in self.records),
            response_text=response_text,
            status=DeliveryStatus.SELECTED,
            selected_at="2026-09-02T00:00:00+00:00",
            delivered_at=None,
            read_at=None,
            identity_metadata=dict(identity_metadata or {}),
        )
        self.records.append(delivery)
        return delivery


class FakeOrchestrator:
    def __init__(self):
        self.feedback = []
        self.requests = []

    async def recognize_intent(self, _message, history=None, bundle=None):
        return SimpleNamespace(
            intent=IntentCategory.HUMAN_HANDOFF,
            intent_group="escalation",
            urgency=UrgencyLevel.HIGH,
            confidence=0.99,
            entities={},
            source_scores={"pattern": 0.99},
        )

    async def classify_request_shape(self, request):
        return RequestShapePolicy().decide(
            message=request.message,
            intent=request.intent,
            confidence=request.intent_confidence,
            urgency=request.urgency,
            entities=request.entities,
        )

    async def decide_route(self, request, shape):
        components = tuple(ComponentInvocation(
            name, ComponentStatus.SKIPPED, "FIXTURE", "a" * 64, "test-v1",
        ) for name in ("intent_fusion", "domain_routing", "instance_selection"))
        return RouteDecision(
            RouteMode.HANDOFF, "explicit_handoff", request.intent_confidence,
            (), RouteRisk.CRITICAL, ("USER_REQUESTED_HANDOFF",), (), (),
            components, "router-v1", shape.input_fingerprint,
        )

    async def run(self, request):
        self.requests.append(request)
        return OrchestratorResult(
            request_id=request.request_id,
            response="我会为你转接人工客服。",
            agent_type=AgentType.ESCALATION,
            intent=IntentCategory.HUMAN_HANDOFF,
            escalated=True,
            latency_ms=1.0,
            agent_types=[AgentType.ESCALATION],
            primary_agent=AgentType.ESCALATION,
            routing_reason="user requested human handoff",
            routing_confidence=0.99,
            producer_agent_keys=["escalation_0"],
        )

    def record_verification(self, agent_keys, status):
        self.feedback.append((list(agent_keys), status))


class FakeVerifier:
    async def verify(self, *_args, **_kwargs):
        return VerificationResult(
            status=VerificationStatus.PASS,
            grounded=True,
            need_escalation=False,
            reason="safe handoff response",
            reason_code=VerificationReasonCode.PASSED,
        )


def test_chat_out_of_scope_is_a_policy_terminal_without_worker_verifier_or_memory_write(
    tmp_path, monkeypatch, ticket_service,
):
    """无恶意越域请求只做范围重定向，不污染客服执行、工单和长期记忆链路。"""
    class ScopeRecognizer:
        async def recognize(self, *_args, **_kwargs):
            return SimpleNamespace(
                intent=IntentCategory.OTHER,
                intent_group="other",
                urgency=UrgencyLevel.LOW,
                confidence=0.95,
                entities={},
                source_scores={"llm": 0.95},
                classifier_fingerprint="c" * 64,
                input_fingerprint="d" * 64,
            )

    class MustNotVerify:
        async def verify(self, *_args, **_kwargs):
            raise AssertionError("policy terminal must not call AnswerVerifier")

    class MustNotSearchOrExecuteTools:
        @staticmethod
        def audit_records(*_args, **_kwargs):
            return []

        @staticmethod
        def registry_fingerprint(*_args, **_kwargs):
            return "scope-test-tools"

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._intent_recognizer = ScopeRecognizer()

    async def must_not_execute(*_args, **_kwargs):
        raise AssertionError("out-of-scope request must not execute a worker")

    orchestrator._execute = must_not_execute
    memory = FakeMemory()
    tickets = ticket_service
    bundles = AgentBundleRegistry(str(tmp_path / "scope-agent-bundles.db"))
    bundles.bootstrap(build_default_bundle({}))

    monkeypatch.setattr(main, "_orchestrator", orchestrator)
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", MustNotVerify())
    monkeypatch.setattr(main, "_ticket_service", tickets)
    monkeypatch.setattr(
        main, "_response_delivery",
        FakeResponseDeliveryService(),
    )
    prediction_registry = BadCaseRegistry(
        str(tmp_path / "scope-badcases.db"),
        identity_salt="scope-test-identity-salt-at-least-32-bytes",
    )
    monkeypatch.setattr(main, "_badcase_registry", prediction_registry)
    monkeypatch.setattr(main, "_tool_manager", MustNotSearchOrExecuteTools())
    monkeypatch.setattr(main, "_bundle_registry", bundles)
    monkeypatch.setattr(
        main, "_bundle_resolver", ActiveBundleResolver(bundles),
    )
    monkeypatch.setattr(
        main,
        "_context_assembler",
        ContextAssembler(max_input_tokens=2048, reserved_output_tokens=256),
    )

    response = asyncio.run(main.chat(
        main.ChatRequest(message="六边形有几条边？", request_id="scope-request"),
        Principal(subject="user-1", scopes=frozenset({"chat"})),
    ))

    assert response.intent == "other"
    assert response.intent_prediction_id
    assert response.intent_classifier_fingerprint == "c" * 64
    assert prediction_registry.get_intent_prediction(
        response.intent_prediction_id,
    ).predicted_intent == "other"
    assert response.routing_disposition == "out_of_scope"
    assert response.agent_type == "orchestrator"
    assert response.agent_types == []
    assert response.primary_agent == ""
    assert response.task_plan == {}
    assert response.agent_outcomes == []
    assert response.tool_audit == []
    assert response.knowledge_used is False
    assert response.memory_retrieval == []
    assert response.verification_status == "pass"
    assert response.verification_reason_code == "policy_terminal"
    assert response.escalated is False
    assert response.ticket_id is None
    assert response.response_seq == 1
    assert response.delivery_status.value == "selected"
    assert "客服范围" in response.response
    assert memory.messages == []
    assert memory.profile_updates == 0
    assert tickets.list_tickets() == []


def test_chat_escalation_creates_one_persistent_idempotent_ticket(
    tmp_path, monkeypatch, ticket_service,
):
    """证明同一请求重试只创建一张持久工单，并返回相同 ticket_id。"""
    memory = FakeMemory()
    orchestrator = FakeOrchestrator()
    monkeypatch.setattr(main, "_orchestrator", orchestrator)
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", FakeVerifier())
    monkeypatch.setattr(main, "_ticket_service", ticket_service)

    class BreachedCommitments:
        def breached_refs(self, *, user_id):
            assert user_id == "user-1"
            return ("breached:commitment:promise-1:v2",)

    monkeypatch.setattr(main, "_commitment_service", BreachedCommitments())
    monkeypatch.setattr(
        main, "_response_delivery",
        FakeResponseDeliveryService(),
    )
    monkeypatch.setattr(
        main,
        "_context_assembler",
        ContextAssembler(max_input_tokens=2048, reserved_output_tokens=256),
    )
    monkeypatch.setattr(main, "_tool_manager", None)
    bundles = AgentBundleRegistry(str(tmp_path / "agent-bundles.db"))
    bundles.bootstrap(build_default_bundle({}))
    monkeypatch.setattr(main, "_bundle_registry", bundles)
    monkeypatch.setattr(
        main, "_bundle_resolver", ActiveBundleResolver(bundles),
    )

    request = main.ChatRequest(
        message="我要转人工",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="stable-request-1",
    )
    principal = Principal(subject="user-1", scopes=frozenset({"chat"}))
    first = asyncio.run(main.chat(request, principal))
    retry = asyncio.run(main.chat(request, principal))

    assert first.escalated is True
    assert first.handoff_created is True
    assert retry.handoff_created is False
    assert retry.ticket_id == first.ticket_id
    assert retry.response_seq == 2
    assert retry.response_id != first.response_id
    persisted_ticket = ticket_service.get_ticket(first.ticket_id)
    assert persisted_ticket.priority is TicketPriority.CRITICAL
    assert persisted_ticket.identity_metadata["commitment_refs"] == (
        "breached:commitment:promise-1:v2"
    )
    assert len(ticket_service.list_tickets()) == 1
    assert memory.profile_updates == 2
    first_identity = orchestrator.requests[0].identity_metadata
    retry_identity = orchestrator.requests[1].identity_metadata
    assert first_identity == retry_identity
    assert first_identity["request_id"] == "stable-request-1"
    assert first_identity["invocation_key"].startswith("invocation:v1:")
    assert memory.message_metadata[0]["invocation_key"] == first_identity["invocation_key"]
    assert orchestrator.feedback == [
        (["escalation_0"], "pass"),
        (["escalation_0"], "pass"),
    ]
    application_outcome = asyncio.run(main._chat_application().handle(ChatCommand(
        message="我要转人工",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="stable-request-2",
    )))
    assert isinstance(application_outcome, Completed)
    assert {stage.stage for stage in application_outcome.stages} == {
        "memory_load",
        "bundle_resolution",
        "intent",
        "knowledge_retrieval",
        "active_case",
            "route_path_plan",
            "media",
            "route_and_agent",
        "verification",
        "ticket",
        "delivery",
        "tool",
        "memory_write",
    }


def test_handoff_priority_preserves_typed_critical_urgency():
    """证明 CRITICAL 紧急度能完整投影到人工工单优先级。"""
    assert main._handoff_priority(UrgencyLevel.CRITICAL, "pass") is TicketPriority.CRITICAL
    assert main._handoff_priority(UrgencyLevel.HIGH, "reject") is TicketPriority.HIGH
    assert main._handoff_priority(UrgencyLevel.HIGH, "pass") is TicketPriority.NORMAL


def test_chat_waiting_approval_does_not_create_handoff_or_badcase(
    tmp_path, monkeypatch, ticket_service,
):
    """预期审批等待不是 Agent 故障，不应偷偷创建人工工单。"""
    class PendingOrchestrator(FakeOrchestrator):
        async def run(self, request):
            return OrchestratorResult(
                request_id=request.request_id,
                response="高风险操作已暂停并等待宿主审批。",
                agent_type=AgentType.BILLING,
                intent=IntentCategory.REFUND,
                synthesis_status="awaiting_approval",
                agent_outcomes=[{
                    "task_id": "billing_task",
                    "status": "awaiting_approval",
                    "react_run_id": "react-1",
                    "pending_approval_call_ids": ["call-1"],
                }],
                coverage={"complete": False},
                awaiting_approval=True,
                react_run_ids=["react-1"],
                pending_approval_call_ids=["call-1"],
            )

    class MustNotVerify:
        async def verify(self, *_args, **_kwargs):
            raise AssertionError("pending approval is not a publishable candidate")

    memory = FakeMemory()
    tickets = ticket_service
    monkeypatch.setattr(main, "_orchestrator", PendingOrchestrator())
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", MustNotVerify())
    monkeypatch.setattr(main, "_ticket_service", tickets)
    monkeypatch.setattr(
        main, "_response_delivery",
        FakeResponseDeliveryService(),
    )
    monkeypatch.setattr(main, "_badcase_registry", None)
    monkeypatch.setattr(main, "_tool_manager", None)
    bundles = AgentBundleRegistry(str(tmp_path / "pending-agent-bundles.db"))
    bundles.bootstrap(build_default_bundle({}))
    monkeypatch.setattr(main, "_bundle_registry", bundles)
    monkeypatch.setattr(
        main, "_bundle_resolver", ActiveBundleResolver(bundles),
    )
    monkeypatch.setattr(
        main, "_context_assembler",
        ContextAssembler(max_input_tokens=2048, reserved_output_tokens=256),
    )
    response = asyncio.run(main.chat(
        main.ChatRequest(message="帮我申请退款", request_id="request-pending"),
        Principal(subject="user-1", scopes=frozenset({"chat"})),
    ))

    assert response.awaiting_approval is True
    assert response.react_run_ids == ["react-1"]
    assert response.pending_approval_call_ids == ["call-1"]
    assert response.verification_reason_code == "approval_required"
    assert response.escalated is False
    assert response.ticket_id is None
    assert tickets.list_tickets() == []




def test_active_ticket_context_is_bounded_authoritative_projection(
    tmp_path, monkeypatch, ticket_service,
):
    service = ticket_service
    ticket, _ = service.create_ticket(
        idempotency_key="request-1",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="request-1",
        question="订单 A123 仍未送达",
        published_response="已经转人工处理",
        reason="delivery overdue",
    )
    monkeypatch.setattr(main, "_ticket_service", service)

    view = asyncio.run(main._active_ticket_context(
        "user-1", query="继续订单问题", intent_or_topics=("other",),
    ))
    section = view.section
    payload = __import__("json").loads(section.content)

    assert section.tag == "active_tickets"
    assert section.priority == 90
    assert payload["authority"] == "TicketService"
    assert payload["cases"][0]["ticket_id"] == ticket.ticket_id
    assert payload["cases"][0]["status"] == "open"
    assert "published_response" not in payload["cases"][0]
"""聊天发布边界与持久人工工单闭环的端到端测试。"""
