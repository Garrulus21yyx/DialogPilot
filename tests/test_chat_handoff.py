import asyncio
from types import SimpleNamespace

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentType,
    OrchestratorResult,
    PlanningDisposition,
)
from agents.react_engine import ReActResult, ReActStatus
from api import main
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.auth import Principal
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.ticket_service import TicketPriority, TicketService
from services.response_delivery import ResponseDeliveryService
from services.evolution import AgentBundleRegistry, RolloutManager, build_default_bundle
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
        self.profile_updates = 0

    async def get_context(self, *_args, **_kwargs):
        return FakeMemoryContext()

    async def add_messages(self, _user_id, _conv_id, entries, *, extract_facts=False):
        persisted = []
        for role, content, metadata in entries:
            self.messages.append((role.value, content))
            persisted.append(SimpleNamespace(role=role, content=content, metadata=metadata))
        if extract_facts:
            self.profile_updates += 1
        return persisted

    async def update_profile(self, *_args, **_kwargs):
        self.profile_updates += 1
        return None


class FakeOrchestrator:
    def __init__(self):
        self.feedback = []

    async def recognize_intent(self, _message, history=None, bundle=None):
        return SimpleNamespace(
            intent=IntentCategory.HUMAN_HANDOFF,
            intent_group="escalation",
            urgency=UrgencyLevel.CRITICAL,
            confidence=0.99,
            entities={},
            source_scores={"pattern": 0.99},
        )

    async def run(self, request):
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
    tmp_path, monkeypatch,
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
            )

    class MustNotVerify:
        async def verify(self, *_args, **_kwargs):
            raise AssertionError("policy terminal must not call AnswerVerifier")

    class MustNotSearchOrExecuteTools:
        async def search_with_rewrite(self, *_args, **_kwargs):
            raise AssertionError("out-of-scope request must not run RAG")

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
    tickets = TicketService(str(tmp_path / "scope-tickets.db"))
    bundles = AgentBundleRegistry(str(tmp_path / "scope-agent-bundles.db"))
    bundles.bootstrap(build_default_bundle({}))

    monkeypatch.setattr(main, "_orchestrator", orchestrator)
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", MustNotVerify())
    monkeypatch.setattr(main, "_ticket_service", tickets)
    monkeypatch.setattr(
        main, "_response_delivery",
        ResponseDeliveryService(str(tmp_path / "scope-responses.db")),
    )
    monkeypatch.setattr(main, "_badcase_registry", None)
    monkeypatch.setattr(main, "_tool_manager", MustNotSearchOrExecuteTools())
    monkeypatch.setattr(main, "_bundle_registry", bundles)
    monkeypatch.setattr(
        main, "_rollout_manager", RolloutManager(bundles, bucket_salt="scope-test-salt"),
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


def test_chat_escalation_creates_one_persistent_idempotent_ticket(tmp_path, monkeypatch):
    """证明同一请求重试只创建一张持久工单，并返回相同 ticket_id。"""
    memory = FakeMemory()
    ticket_service = TicketService(str(tmp_path / "tickets.db"))
    orchestrator = FakeOrchestrator()
    monkeypatch.setattr(main, "_orchestrator", orchestrator)
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", FakeVerifier())
    monkeypatch.setattr(main, "_ticket_service", ticket_service)
    monkeypatch.setattr(
        main, "_response_delivery",
        ResponseDeliveryService(str(tmp_path / "responses.db")),
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
        main, "_rollout_manager", RolloutManager(bundles, bucket_salt="test-salt"),
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
    assert ticket_service.get_ticket(first.ticket_id).priority is TicketPriority.CRITICAL
    assert len(ticket_service.list_tickets()) == 1
    assert memory.profile_updates == 2
    assert orchestrator.feedback == [
        (["escalation_0"], "pass"),
        (["escalation_0"], "pass"),
    ]


def test_handoff_priority_preserves_typed_critical_urgency():
    """证明 CRITICAL 紧急度能完整投影到人工工单优先级。"""
    assert main._handoff_priority(UrgencyLevel.CRITICAL, "pass") is TicketPriority.CRITICAL
    assert main._handoff_priority(UrgencyLevel.HIGH, "reject") is TicketPriority.HIGH
    assert main._handoff_priority(UrgencyLevel.HIGH, "pass") is TicketPriority.NORMAL


def test_chat_waiting_approval_does_not_create_handoff_or_badcase(tmp_path, monkeypatch):
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
    tickets = TicketService(str(tmp_path / "tickets.db"))
    monkeypatch.setattr(main, "_orchestrator", PendingOrchestrator())
    monkeypatch.setattr(main, "_memory", memory)
    monkeypatch.setattr(main, "_answer_verifier", MustNotVerify())
    monkeypatch.setattr(main, "_ticket_service", tickets)
    monkeypatch.setattr(
        main, "_response_delivery",
        ResponseDeliveryService(str(tmp_path / "pending-responses.db")),
    )
    monkeypatch.setattr(main, "_badcase_registry", None)
    monkeypatch.setattr(main, "_tool_manager", None)
    bundles = AgentBundleRegistry(str(tmp_path / "pending-agent-bundles.db"))
    bundles.bootstrap(build_default_bundle({}))
    monkeypatch.setattr(main, "_bundle_registry", bundles)
    monkeypatch.setattr(
        main, "_rollout_manager", RolloutManager(bundles, bucket_salt="test-salt"),
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


def test_resume_endpoint_reverifies_completed_candidate(monkeypatch):
    captured = {}

    class Checkpoint:
        task_id = "billing_task"
        system = "trusted context"
        execution_context = {"task_input": "申请退款"}

        @staticmethod
        def to_public_dict():
            return {"run_id": "react-1", "status": "completed"}

    class ResumeOrchestrator:
        def get_react_run(self, run_id, *, user_id):
            captured["identity"] = (run_id, user_id)
            return Checkpoint()

        async def resume_react(self, *_args, **_kwargs):
            return ReActResult(
                content="退款申请已提交。",
                status=ReActStatus.COMPLETED,
                steps=2,
                run_id="react-1",
            )

    monkeypatch.setattr(main, "_orchestrator", ResumeOrchestrator())
    monkeypatch.setattr(main, "_answer_verifier", FakeVerifier())
    response = asyncio.run(main.resume_agent_run(
        "react-1",
        main.ReactResumeInput(approved=True),
        Principal(subject="user-1", scopes=frozenset({"tool:approve"})),
    ))

    assert captured["identity"] == ("react-1", "user-1")
    assert response["verified"] is True
    assert response["response"] == "退款申请已提交。"


def test_active_ticket_context_is_bounded_authoritative_projection(tmp_path, monkeypatch):
    service = TicketService(str(tmp_path / "tickets.db"))
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

    section = asyncio.run(main._active_ticket_context("user-1"))
    payload = __import__("json").loads(section.content)

    assert section.tag == "active_tickets"
    assert section.priority == 90
    assert payload["authority"] == "TicketService"
    assert payload["tickets"][0]["ticket_id"] == ticket.ticket_id
    assert payload["tickets"][0]["status"] == "open"
"""聊天发布边界与持久人工工单闭环的端到端测试。"""
